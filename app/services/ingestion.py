"""Ingestion: extract text -> split into overlapping chunks -> embed in
batches -> store in Chroma -> write a short summary. Progress and a human
readable stage are saved after every step so the UI can show them live.

Text extraction by file type:
  PDF    one entry per page (real page numbers). Pages without a text layer
         (scanned) are rendered and read by the vision model (OCR).
  Word   paragraphs and tables, grouped into ~page-sized sections
  Text   grouped into ~page-sized sections
  Image  the vision model transcribes the text and describes the picture
"""

import logging
import re
from dataclasses import dataclass
from typing import Callable

import docx
import pypdfium2 as pdfium
from docx.table import Table
from flask import current_app
from langchain_core.documents import Document as Chunk
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.extensions import db
from app.models import Document, DocumentStatus
from app.services import file_types, llm, retrieval, summary, vector_store, vision

logger = logging.getLogger(__name__)

# Progress bar: extract text 0-15%, read scanned pages 15-50% (only when there
# are any), embed up to 95%, summary 95-99%, ready = 100%.
TEXT_END, OCR_END, EMBED_END = 15, 50, 95

# Word and text files have no fixed pages, so text is grouped into sections of
# about one printed page. Citations then point to "p. 3" = the third section.
SECTION_CHARS = 3000
OCR_RENDER_SCALE = 2  # 144 dpi: sharp enough for OCR, small enough to upload

# report(stage, progress_percent)
Report = Callable[[str, int], None]


class IngestionError(Exception):
    """A failure whose message is safe and useful to show the user."""


@dataclass
class Extraction:
    pages: list[Chunk]
    total_pages: int
    ocr_pages: int = 0


def _no_report(stage: str, progress: int) -> None:
    pass


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pages(path: str, report: Report = _no_report) -> Extraction:
    """One Document per page (or section) that has text, with its 1-based
    page number in metadata."""
    kind = file_types.kind_for(path)
    if kind == "docx":
        pages = split_into_sections(_read_docx(path))
    elif kind == "text":
        pages = split_into_sections(_read_text(path))
    elif kind == "image":
        report("Reading the photo with AI", 5)
        pages = _read_image(path)
    else:
        return _read_pdf(path, report)
    return Extraction(pages, len(pages))


def split_into_sections(text: str) -> list[Chunk]:
    sections, current = [], ""
    for line in clean_text(text).split("\n"):
        if current and len(current) + len(line) > SECTION_CHARS:
            sections.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    sections.append(current)
    sections = [s.strip() for s in sections if s.strip()]
    return [Chunk(page_content=s, metadata={"page": i}) for i, s in enumerate(sections, start=1)]


def _read_docx(path: str) -> str:
    try:
        document = docx.Document(path)
    except Exception as exc:  # noqa: BLE001
        raise IngestionError(
            "Could not read this Word file. Make sure it is a .docx file (not the older .doc) "
            "and not damaged."
        ) from exc
    lines = []
    for block in document.iter_inner_content():  # paragraphs and tables, in order
        if isinstance(block, Table):
            for row in block.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        else:
            lines.append(block.text)
    return "\n".join(lines)


def _read_text(path: str) -> str:
    with open(path, "rb") as file:
        return file.read().decode("utf-8-sig", errors="replace")


def _read_image(path: str) -> list[Chunk]:
    try:
        text = clean_text(vision.describe_image(path))
    except vision.ImageReadError as exc:
        raise IngestionError(str(exc)) from exc
    return [Chunk(page_content=text, metadata={"page": 1})] if text else []


def _read_pdf(path: str, report: Report) -> Extraction:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise IngestionError("This PDF is password-protected. Remove the password and upload again.")
        total = len(reader.pages)
    except IngestionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IngestionError(f"Could not read the PDF (the file may be corrupted): {exc}") from exc

    config = current_app.config
    texts: dict[int, str] = {}
    scanned: list[int] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = clean_text(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page shouldn't fail the whole book
            logger.warning("Could not extract text from page %d of %s", number, path)
            text = ""
        if text:
            texts[number] = text
        if len(text) < config["OCR_MIN_CHARS"]:
            scanned.append(number)
        if number % 25 == 0 or number == total:
            report("Extracting text", TEXT_END * number // total)

    ocr_pages = 0
    if scanned and config["OCR_ENABLED"]:
        to_read = scanned[: config["OCR_MAX_PAGES"]]
        _ocr_pages(path, to_read, texts, report)
        ocr_pages = len(to_read)

    pages = [Chunk(page_content=texts[n], metadata={"page": n}) for n in sorted(texts)]
    return Extraction(pages, total, ocr_pages)


def _ocr_pages(path: str, numbers: list[int], texts: dict[int, str], report: Report) -> None:
    """Read pages without a text layer with the vision model. A page that
    fails is skipped; if nothing at all could be read, the error is raised so
    the user sees why (e.g. a rate limit)."""
    first_error = None
    pdf = pdfium.PdfDocument(path)
    try:
        for i, number in enumerate(numbers, start=1):
            report(
                f"Reading scanned page {i} of {len(numbers)} with AI",
                TEXT_END + (OCR_END - TEXT_END) * (i - 1) // len(numbers),
            )
            page = pdf[number - 1]
            try:
                text = clean_text(vision.transcribe_page(page.render(scale=OCR_RENDER_SCALE).to_pil()))
                if text:
                    texts[number] = text
            except Exception as exc:  # noqa: BLE001
                logger.warning("OCR failed for page %d of %s: %s", number, path, exc)
                first_error = first_error or exc
            finally:
                page.close()
    finally:
        pdf.close()
    if first_error is not None and not texts:
        raise first_error


def split_pages(pages: list[Chunk], chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(pages)  # page metadata is carried over
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk"] = index
    return chunks


def _empty_message(kind: str) -> str:
    if kind == "pdf":
        if current_app.config["OCR_ENABLED"]:
            return "Couldn't find any readable text in this PDF, even after reading its pages with AI."
        return (
            "No selectable text found in this PDF. It looks scanned: turn on OCR "
            "(OCR_ENABLED=true) or run OCR on it first, then upload it again."
        )
    return {
        "docx": "This Word file doesn't contain any text.",
        "text": "This file is empty.",
        "image": "Couldn't read anything from this image.",
    }[kind]


def _save(doc: Document, **fields) -> None:
    for key, value in fields.items():
        setattr(doc, key, value)
    db.session.commit()


def _mark_failed(doc_id: str, message: str) -> None:
    db.session.rollback()
    doc = db.session.get(Document, doc_id)
    if doc is not None:
        _save(doc, status=DocumentStatus.FAILED, error=message, stage=None)


def ingest_document(doc_id: str) -> None:
    """Run the full ingestion for one document. Must run inside an app
    context. Safe to re-run: the old vectors are replaced."""
    doc = db.session.get(Document, doc_id)
    if doc is None:
        logger.warning("Ingestion skipped, document %s no longer exists", doc_id)
        return

    config = current_app.config
    logger.info("Ingesting %s (%s)", doc.filename, doc_id)
    try:
        _save(doc, status=DocumentStatus.PROCESSING, progress=0, stage="Reading the file", error=None)

        def report(stage: str, progress: int) -> None:
            _save(doc, stage=stage, progress=progress)

        extraction = extract_pages(doc.file_path, report)
        if not extraction.pages:
            raise IngestionError(_empty_message(doc.kind))

        chunks = split_pages(extraction.pages, config["CHUNK_SIZE"], config["CHUNK_OVERLAP"])
        start = OCR_END if extraction.ocr_pages else TEXT_END
        _save(doc, page_count=extraction.total_pages, progress=start, stage="Creating embeddings")
        logger.info("%s: %d pages -> %d chunks", doc_id, extraction.total_pages, len(chunks))

        vector_store.delete_document(doc_id)
        retrieval.forget(doc_id)
        embeddings = llm.get_embeddings()
        batch_size = config["EMBED_BATCH_SIZE"]
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            vector_store.add_chunks(doc_id, batch, embeddings.embed_documents([c.page_content for c in batch]))
            done = offset + len(batch)
            _save(doc, progress=start + (EMBED_END - start) * done // len(chunks))

        _save(
            doc,
            chunk_count=len(chunks),
            embedding_model=llm.embedding_model_name(),
            progress=EMBED_END + 2,
            stage="Writing a summary",
        )
        overview = summary.generate_overview(doc.filename, chunks)
        _save(
            doc,
            summary=overview["summary"] if overview else None,
            suggested_questions=overview["questions"] if overview else None,
            status=DocumentStatus.READY,
            progress=100,
            stage=None,
        )
        logger.info("Ingestion complete for %s", doc_id)

    except IngestionError as exc:
        logger.warning("Ingestion failed for %s: %s", doc_id, exc)
        _mark_failed(doc_id, str(exc))
    except Exception as exc:  # noqa: BLE001
        known = llm.describe_error(exc)
        if known:
            logger.error("Ingestion failed for %s: %s", doc_id, known)
        else:
            logger.exception("Ingestion crashed for %s", doc_id)
        _mark_failed(doc_id, known or f"Processing failed: {exc}")
