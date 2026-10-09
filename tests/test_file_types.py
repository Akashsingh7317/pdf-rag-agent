"""Word, text and image uploads (the vision model is faked)."""

import base64
import io

from PIL import Image

from app.services import rag
from app.services.ingestion import SECTION_CHARS, split_into_sections
from app.services.vision import MAX_SIDE, image_to_data_url
from tests.conftest import make_docx, make_image, make_pdf, parse_sse, upload


def status_of(client, doc_id):
    return client.get(f"/api/documents/{doc_id}").get_json()["document"]


def ask_sources(client, doc_id, question="Anything?"):
    res = client.post(f"/api/documents/{doc_id}/chat", json={"question": question})
    events = parse_sse(res.get_data())
    assert events[-1][0] == "done"
    return events[0][1]["sources"]


def test_word_document_with_table_is_indexed(client):
    content = make_docx(
        ["X200 Printer Guide", "Keep the printer away from direct sunlight."],
        table=[["Item", "Warranty"], ["X200", "2 years"]],
    )
    res = upload(client, content, filename="guide.docx")
    assert res.status_code == 202
    doc = status_of(client, res.get_json()["document"]["id"])
    assert (doc["status"], doc["kind"], doc["page_count"]) == ("ready", "docx", 1)

    snippets = " ".join(s["snippet"] for s in ask_sources(client, doc["id"]))
    assert "X200 | 2 years" in snippets  # table rows are kept, cell by cell


def test_text_file_is_indexed(client):
    res = upload(client, "# Notes\n\nThe meeting is on Friday.".encode(), filename="notes.md")
    doc = status_of(client, res.get_json()["document"]["id"])
    assert (doc["status"], doc["kind"]) == ("ready", "text")


def test_empty_text_file_fails_clearly(client):
    res = upload(client, b"   \n\n  ", filename="empty.txt")
    doc = status_of(client, res.get_json()["document"]["id"])
    assert doc["status"] == "failed"
    assert doc["error"] == "This file is empty."


def test_damaged_word_file_fails_clearly(client):
    res = upload(client, b"PK\x03\x04 not really a docx", filename="broken.docx")
    doc = status_of(client, res.get_json()["document"]["id"])
    assert doc["status"] == "failed"
    assert "Could not read this Word file" in doc["error"]


def test_photo_is_read_by_the_vision_model(client):
    res = upload(client, make_image(), filename="invoice.png")
    assert res.status_code == 202
    doc = status_of(client, res.get_json()["document"]["id"])
    assert (doc["status"], doc["kind"], doc["page_count"]) == ("ready", "image", 1)

    sources = ask_sources(client, doc["id"], "What is the total?")
    assert "INVOICE #4471" in sources[0]["snippet"]


def test_jpeg_and_webp_are_accepted(client):
    for fmt, name in (("JPEG", "a.jpg"), ("WEBP", "b.webp")):
        res = upload(client, make_image(fmt=fmt, size=(50 + len(name), 40)), filename=name)
        assert res.status_code == 202, name


def test_renamed_file_is_rejected(client):
    res = upload(client, make_pdf(["hello"]), filename="photo.png")
    assert res.status_code == 415
    assert "not a valid IMAGE file" in res.get_json()["error"]


def test_original_file_is_served_inline(client):
    content = make_image()
    doc_id = upload(client, content, filename="pic.png").get_json()["document"]["id"]
    res = client.get(f"/api/documents/{doc_id}/file")
    assert res.status_code == 200
    assert res.mimetype == "image/png"
    assert res.data == content
    assert "attachment" not in res.headers.get("Content-Disposition", "")


def test_long_text_is_split_into_numbered_sections():
    text = "\n".join(f"Line {i}: " + "word " * 20 for i in range(100))  # ~11 KB
    sections = split_into_sections(text)
    assert len(sections) >= 3
    assert [s.metadata["page"] for s in sections] == list(range(1, len(sections) + 1))
    assert all(len(s.page_content) <= SECTION_CHARS for s in sections)


def test_image_is_downscaled_and_flattened_before_sending(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(make_image(size=(4000, 1000), mode="RGBA"))
    url = image_to_data_url(str(path))
    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert max(image.size) == MAX_SIDE
        assert image.mode == "RGB"


def test_answer_script_follows_the_question():
    assert "Devanagari" in rag.language_rule("कुल कितने पेज हैं?").split(",")[0]
    assert "ONLY in Roman" in rag.language_rule("total kitne page hai?")
    assert "ONLY in Roman" in rag.language_rule("How many pages?")


def test_answer_prompt_describes_the_document():
    info, _ = rag.describe_document("pdf", 12)
    assert info == "a PDF with 12 pages in total."
    info, citation = rag.describe_document("image", 1)
    assert "image" in info and "Don't cite page numbers" in citation
