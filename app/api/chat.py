"""Chat endpoints. Answers stream as Server-Sent Events:
sources -> token (many) -> done, or error at any point.

  POST /api/documents/<id>/chat   ask one document
  POST /api/chat                  ask across all of your ready documents
  GET/DELETE .../messages         read or clear the matching conversation
"""

import json
import logging

from flask import Response, current_app, jsonify, request, stream_with_context
from flask_jwt_extended import current_user
from sqlalchemy import delete, select

from app.api import api
from app.api.documents import get_document_or_404
from app.errors import ApiError
from app.extensions import db, limiter
from app.models import ChatMessage, Document, DocumentStatus
from app.services import llm, rag, retrieval

logger = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _chat_limit() -> str:
    return current_app.config["CHAT_RATE_LIMIT"]


def _conversation(user_id: str, doc_id: str | None):
    """Filter for one conversation: a document's, or the all-documents one."""
    scope = ChatMessage.document_id == doc_id if doc_id else ChatMessage.document_id.is_(None)
    return (ChatMessage.user_id == user_id, scope)


def _read_question() -> str:
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ApiError("'question' is required.")
    max_chars = current_app.config["MAX_QUESTION_CHARS"]
    if len(question) > max_chars:
        raise ApiError(f"Question is too long (max {max_chars} characters).")
    return question


def _stream_chat(documents: list[Document], question: str, doc_id: str | None) -> Response:
    user_id = current_user.id
    turns = current_app.config["HISTORY_TURNS"]
    recent = db.session.scalars(
        select(ChatMessage)
        .where(*_conversation(user_id, doc_id))
        .order_by(ChatMessage.id.desc())
        .limit(turns * 2)
    ).all()
    history = [{"role": m.role, "content": m.content} for m in reversed(recent)]
    across_all = doc_id is None

    def events():
        answer_parts = []
        try:
            search_query = rag.condense_question(question, history)
            chunks = retrieval.search(search_query, documents)
            sources = rag.to_sources(chunks, documents)
            yield _sse("sources", {"sources": sources, "search_query": search_query})

            for token in rag.stream_answer(documents, question, history, chunks, across_all):
                answer_parts.append(token)
                yield _sse("token", {"text": token})

            # Saved only after a complete answer, so a failed request leaves no half-turn.
            answer = ChatMessage(
                user_id=user_id, document_id=doc_id, role="assistant",
                content="".join(answer_parts), sources=sources,
            )
            db.session.add_all(
                [ChatMessage(user_id=user_id, document_id=doc_id, role="user", content=question), answer]
            )
            db.session.commit()
            yield _sse("done", {"message_id": answer.id})
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            known = llm.describe_error(exc)
            if known:
                logger.error("Chat failed (document %s): %s", doc_id or "all", known)
            else:
                logger.exception("Chat failed (document %s)", doc_id or "all")
            message = known or "Something went wrong while answering. Please try again."
            yield _sse("error", {"message": message})

    return Response(
        stream_with_context(events()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- One document ---------------------------------------------------------------

@api.post("/documents/<doc_id>/chat")
@limiter.limit(_chat_limit)
def chat(doc_id: str):
    doc = get_document_or_404(doc_id)
    if doc.status != DocumentStatus.READY:
        raise ApiError(f"Document is not ready yet (status: {doc.status}).", 409)
    if doc.embedding_model != llm.embedding_model_name():
        raise ApiError(
            "This document was indexed with a different AI model. Re-index it to chat with it.", 409
        )
    return _stream_chat([doc], _read_question(), doc.id)


@api.get("/documents/<doc_id>/messages")
def list_messages(doc_id: str):
    doc = get_document_or_404(doc_id)
    return jsonify(messages=[message.to_dict() for message in doc.messages])


@api.delete("/documents/<doc_id>/messages")
def clear_messages(doc_id: str):
    doc = get_document_or_404(doc_id)
    db.session.execute(delete(ChatMessage).where(*_conversation(current_user.id, doc.id)))
    db.session.commit()
    return "", 204


# --- All documents --------------------------------------------------------------

@api.post("/chat")
@limiter.limit(_chat_limit)
def chat_all():
    documents = db.session.scalars(
        select(Document)
        .where(
            Document.user_id == current_user.id,
            Document.status == DocumentStatus.READY,
            Document.embedding_model == llm.embedding_model_name(),
        )
        .order_by(Document.filename)
    ).all()
    if not documents:
        raise ApiError("No documents are ready yet. Add a file with the + button first.", 409)
    return _stream_chat(list(documents), _read_question(), None)


@api.get("/chat/messages")
def list_all_messages():
    messages = db.session.scalars(
        select(ChatMessage).where(*_conversation(current_user.id, None)).order_by(ChatMessage.id)
    ).all()
    return jsonify(messages=[message.to_dict() for message in messages])


@api.delete("/chat/messages")
def clear_all_messages():
    db.session.execute(delete(ChatMessage).where(*_conversation(current_user.id, None)))
    db.session.commit()
    return "", 204
