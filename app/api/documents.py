"""Document endpoints: upload, list, status, original file, summary,
reprocess, delete. A "document" is any supported upload: PDF, Word, text
file or image. Every query is scoped to the logged-in user."""

import hashlib
import logging
import os
import uuid
from datetime import timedelta

from flask import current_app, jsonify, request, send_file, url_for
from flask_jwt_extended import current_user
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api import api
from app.errors import ApiError
from app.extensions import db, limiter
from app.models import Document, DocumentStatus
from app.services import file_types, llm, retrieval, summary, vector_store
from app.tasks import enqueue_ingestion

logger = logging.getLogger(__name__)

READ_CHUNK = 1024 * 1024


def get_document_or_404(doc_id: str) -> Document:
    """The user's own document. Other users' documents are reported as not
    found, so their ids can't even be confirmed to exist."""
    doc = db.session.get(Document, doc_id)
    if doc is None or doc.user_id != current_user.id:
        raise ApiError("Document not found.", 404)
    return doc


def _stale_after() -> timedelta:
    return timedelta(minutes=current_app.config["STALE_JOB_MINUTES"])


def document_json(doc: Document) -> dict:
    return doc.to_dict(current_embedding_model=llm.embedding_model_name(), stale_after=_stale_after())


def _save_and_hash(file_storage, dest_path: str, kind: str) -> tuple[str, int]:
    """Stream the upload to disk while hashing it; reject content that doesn't
    match the file type."""
    hasher = hashlib.sha256()
    size = 0
    with open(dest_path, "wb") as out:
        while chunk := file_storage.stream.read(READ_CHUNK):
            # Checking the first bytes, not just the extension, stops renamed files early.
            if size == 0 and not file_types.content_matches(kind, chunk):
                raise ApiError(f"This file is not a valid {kind.upper()} file.", 415)
            hasher.update(chunk)
            out.write(chunk)
            size += len(chunk)
    if size == 0:
        raise ApiError("The uploaded file is empty.")
    return hasher.hexdigest(), size


def _queue(doc: Document) -> None:
    try:
        enqueue_ingestion(doc.id)
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue ingestion for %s", doc.id)
        doc.status = DocumentStatus.FAILED
        doc.error = "Could not queue the document for processing. Try again shortly."
        db.session.commit()
        raise ApiError("The background worker is unavailable. Please try again shortly.", 503)


def _reset_for_processing(doc: Document) -> None:
    doc.status, doc.progress, doc.error, doc.stage = DocumentStatus.QUEUED, 0, None, None


@api.get("/health")
@limiter.exempt
def health():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logger.exception("Health check: database unreachable")
        return jsonify(status="error", database="unreachable"), 503
    return jsonify(status="ok")


@api.get("/documents")
def list_documents():
    docs = db.session.scalars(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.created_at.desc())
    ).all()
    return jsonify(documents=[document_json(doc) for doc in docs])


@api.get("/documents/<doc_id>")
def get_document(doc_id: str):
    return jsonify(document=document_json(get_document_or_404(doc_id)))


@api.post("/documents")
@limiter.limit(lambda: current_app.config["UPLOAD_RATE_LIMIT"])
def upload_document():
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Attach a file in a form field named 'file'.")
    display_name = os.path.basename(file.filename.replace("\\", "/"))[:255]
    kind = file_types.kind_for(display_name)
    if kind is None:
        raise ApiError(f"Unsupported file type. Upload a {file_types.SUPPORTED_LABEL} file.", 415)

    user_id = current_user.id
    upload_dir = current_app.config["UPLOAD_DIR"]
    tmp_path = os.path.join(upload_dir, f".upload-{uuid.uuid4().hex}.part")
    try:
        digest, size = _save_and_hash(file, tmp_path, kind)

        existing = db.session.scalar(select(Document).filter_by(user_id=user_id, file_hash=digest))
        if existing is not None and existing.status != DocumentStatus.FAILED:
            # Same file already uploaded by this user: reuse it instead of paying to embed it again.
            return jsonify(document=document_json(existing), duplicate=True), 200

        if existing is not None:
            # It failed last time: retry on the same record.
            doc = existing
            doc.filename = display_name
            _reset_for_processing(doc)
            os.replace(tmp_path, doc.file_path)
            db.session.commit()
        else:
            doc_id = uuid.uuid4().hex
            file_path = os.path.join(upload_dir, doc_id + file_types.extension(display_name))
            doc = Document(
                id=doc_id,
                user_id=user_id,
                filename=display_name,
                file_path=file_path,
                file_hash=digest,
                size_bytes=size,
                status=DocumentStatus.QUEUED,
                progress=0,
            )
            os.replace(tmp_path, file_path)
            db.session.add(doc)
            try:
                db.session.commit()
            except IntegrityError:
                # The same file was uploaded concurrently and the other request won.
                db.session.rollback()
                os.remove(file_path)
                winner = db.session.scalar(select(Document).filter_by(user_id=user_id, file_hash=digest))
                return jsonify(document=document_json(winner), duplicate=True), 200
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    _queue(doc)
    location = url_for("api.get_document", doc_id=doc.id)
    return jsonify(document=document_json(doc), duplicate=False), 202, {"Location": location}


@api.get("/documents/<doc_id>/file")
def get_document_file(doc_id: str):
    """The original upload, shown inline: PDFs open in the browser's viewer
    (append #page=N to jump to a page) and images display directly."""
    doc = get_document_or_404(doc_id)
    if not os.path.exists(doc.file_path):
        raise ApiError("The original file is missing.", 404)
    return send_file(
        doc.file_path,
        mimetype=file_types.MIME_TYPES.get(file_types.extension(doc.file_path)),
        download_name=doc.filename,
        conditional=True,
    )


@api.post("/documents/<doc_id>/retry")
@limiter.limit(lambda: current_app.config["UPLOAD_RATE_LIMIT"])
def retry_document(doc_id: str):
    """Process a document again from the file already on disk: after a
    failure (e.g. a fixed API key), when the embedding model changed, or when
    its job is stuck."""
    doc = get_document_or_404(doc_id)
    info = document_json(doc)
    if not (doc.status == DocumentStatus.FAILED or info["needs_reindex"] or info["stuck"]):
        raise ApiError(
            "Only failed or stuck documents, or ones indexed with a different AI model, "
            "can be processed again.",
            409,
        )
    if not os.path.exists(doc.file_path):
        raise ApiError("The original file is missing. Delete this document and upload it again.", 409)

    _reset_for_processing(doc)
    db.session.commit()
    _queue(doc)
    return jsonify(document=document_json(doc)), 202


@api.post("/documents/<doc_id>/summary")
@limiter.limit(lambda: current_app.config["CHAT_RATE_LIMIT"])
def create_summary(doc_id: str):
    """(Re)generate the overview, e.g. for documents indexed before summaries existed."""
    doc = get_document_or_404(doc_id)
    if doc.status != DocumentStatus.READY or document_json(doc)["needs_reindex"]:
        raise ApiError("The document must be ready before it can be summarised.", 409)
    overview = summary.generate_overview(doc.filename, vector_store.get_chunks([doc.id]))
    if overview is None:
        raise ApiError("Couldn't write a summary right now. Please try again.", 502)
    doc.summary, doc.suggested_questions = overview["summary"], overview["questions"]
    db.session.commit()
    return jsonify(document=document_json(doc))


@api.delete("/documents/<doc_id>")
def delete_document(doc_id: str):
    doc = get_document_or_404(doc_id)
    if doc.status in DocumentStatus.IN_PROGRESS and not doc.is_stuck(_stale_after()):
        raise ApiError("This document is still being processed. Delete it once it finishes.", 409)

    vector_store.delete_document(doc.id)
    retrieval.forget(doc.id)
    file_path = doc.file_path
    db.session.delete(doc)  # its chat messages are removed by the cascade
    db.session.commit()
    try:
        os.remove(file_path)
    except FileNotFoundError:
        pass
    return "", 204
