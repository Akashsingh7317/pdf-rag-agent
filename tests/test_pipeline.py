"""Unit tests for text processing and prompt-building helpers."""

import pytest
from langchain_core.documents import Document as Chunk

from app.models import Document
from app.services import rag
from app.services.ingestion import IngestionError, clean_text, extract_pages, split_pages
from tests.conftest import SAMPLE_PAGES, make_pdf


def test_clean_text_normalises_whitespace():
    assert clean_text("  a\x00b   c\t\td\n\n\n\n\ne  ") == "ab c d\n\ne"


def test_extract_pages_keeps_page_numbers(app, tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf(["First page of the handbook", "", "Third page of the handbook"]))
    app.config["OCR_ENABLED"] = False
    with app.app_context():
        extraction = extract_pages(str(path))
    assert extraction.total_pages == 3
    assert [p.metadata["page"] for p in extraction.pages] == [1, 3]  # empty page skipped
    assert extraction.pages[1].page_content == "Third page of the handbook"
    assert extraction.ocr_pages == 0


def test_extract_pages_rejects_corrupted_file(app, tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 this is not really a pdf")
    with app.app_context(), pytest.raises(IngestionError):
        extract_pages(str(path))


def test_split_pages_carries_page_metadata():
    pages = [Chunk(page_content=text, metadata={"page": i}) for i, text in enumerate(SAMPLE_PAGES, 1)]
    chunks = split_pages(pages, chunk_size=60, chunk_overlap=10)
    assert len(chunks) > len(pages)
    assert [c.metadata["chunk"] for c in chunks] == list(range(len(chunks)))
    assert {c.metadata["page"] for c in chunks} == {1, 2, 3}


def test_build_context_labels_pages():
    chunks = [Chunk(page_content="alpha", metadata={"page": 7}), Chunk(page_content="beta", metadata={"page": 2})]
    context = rag.build_context(chunks)
    assert "[Excerpt 1 | Page 7]\nalpha" in context
    assert "[Excerpt 2 | Page 2]\nbeta" in context


def test_build_context_names_files_across_documents():
    chunks = [Chunk(page_content="alpha", metadata={"page": 3, "doc_id": "d1"})]
    assert "[Excerpt 1 | report.pdf | Page 3]" in rag.build_context(chunks, {"d1": "report.pdf"})


def test_to_sources_dedupes_by_document_and_page():
    docs = [
        Document(id="d1", filename="b.pdf", file_path="d1.pdf"),
        Document(id="d2", filename="a.docx", file_path="d2.docx"),
    ]
    chunks = [
        Chunk(page_content="late  text", metadata={"doc_id": "d1", "page": 9}),
        Chunk(page_content="early", metadata={"doc_id": "d1", "page": 1}),
        Chunk(page_content="late again", metadata={"doc_id": "d1", "page": 9}),
        Chunk(page_content="notes", metadata={"doc_id": "d2", "page": 1}),
        Chunk(page_content="not mine", metadata={"doc_id": "other", "page": 1}),
    ]
    assert rag.to_sources(chunks, docs) == [
        {"doc_id": "d2", "filename": "a.docx", "kind": "docx", "page": 1, "snippet": "notes"},
        {"doc_id": "d1", "filename": "b.pdf", "kind": "pdf", "page": 1, "snippet": "early"},
        {"doc_id": "d1", "filename": "b.pdf", "kind": "pdf", "page": 9, "snippet": "late text"},
    ]


def test_answers_use_plain_hyphens_and_spaces():
    typographic = "Call 1800‑123‑4567 within 2 years"
    assert typographic.translate(rag.PLAIN_TYPOGRAPHY) == "Call 1800-123-4567 within 2 years"


def test_condense_question_skips_llm_without_history():
    assert rag.condense_question("What is X?", []) == "What is X?"
