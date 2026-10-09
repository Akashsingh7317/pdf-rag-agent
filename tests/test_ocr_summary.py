"""Scanned PDF pages read by the vision model, and document summaries."""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.services import llm
from tests.conftest import FAKE_IMAGE_TEXT, FAKE_QUESTIONS, FAKE_SUMMARY, make_pdf, parse_sse, upload


def document(client, res):
    return client.get(f"/api/documents/{res.get_json()['document']['id']}").get_json()["document"]


def test_scanned_pages_are_read_with_ocr(client):
    doc = document(client, upload(client, make_pdf(["", ""]), filename="scanned.pdf"))
    assert doc["status"] == "ready"
    assert doc["page_count"] == 2
    sources = parse_sse(
        client.post(f"/api/documents/{doc['id']}/chat", json={"question": "total?"}).get_data()
    )[0][1]["sources"]
    assert {s["page"] for s in sources} == {1, 2}
    assert "INVOICE #4471" in sources[0]["snippet"]


def test_only_pages_without_text_go_to_ocr(client, monkeypatch):
    calls = []

    def counting_vision_model():
        calls.append(1)
        return GenericFakeChatModel(messages=iter([AIMessage(FAKE_IMAGE_TEXT)]))

    monkeypatch.setattr(llm, "get_vision_model", counting_vision_model)
    pages = ["A normal page with plenty of selectable text on it.", "", "Another normal text page here."]
    doc = document(client, upload(client, make_pdf(pages), filename="mixed.pdf"))
    assert doc["status"] == "ready"
    assert len(calls) == 1  # only page 2


def test_ocr_can_be_turned_off(app, client):
    app.config["OCR_ENABLED"] = False
    doc = document(client, upload(client, make_pdf(["", ""]), filename="scanned.pdf"))
    assert doc["status"] == "failed"
    assert "OCR_ENABLED=true" in doc["error"]


def test_ocr_failure_on_every_page_explains_why(client, monkeypatch):
    def broken_vision_model():
        raise llm.LLMConfigError("GROQ_API_KEY is not set. Add it to your .env file and restart the server.")

    monkeypatch.setattr(llm, "get_vision_model", broken_vision_model)
    doc = document(client, upload(client, make_pdf(["", ""]), filename="scanned.pdf"))
    assert doc["status"] == "failed"
    assert "GROQ_API_KEY is not set" in doc["error"]


def test_summary_and_questions_are_generated(client):
    doc = document(client, upload(client))
    assert doc["summary"] == FAKE_SUMMARY
    assert doc["suggested_questions"] == FAKE_QUESTIONS


def test_summary_failure_does_not_fail_the_document(client, monkeypatch):
    from tests import conftest

    def chat_model(streaming=True):
        if not streaming:  # the summary uses a non-streaming call
            raise RuntimeError("model down")
        return conftest.FakeChat()

    monkeypatch.setattr(llm, "get_chat_model", chat_model)
    doc = document(client, upload(client))
    assert doc["status"] == "ready"
    assert doc["summary"] is None


def test_summary_can_be_generated_later(client, app):
    app.config["SUMMARY_ENABLED"] = False
    doc = document(client, upload(client))
    assert doc["summary"] is None

    app.config["SUMMARY_ENABLED"] = True
    res = client.post(f"/api/documents/{doc['id']}/summary")
    assert res.status_code == 200
    assert res.get_json()["document"]["summary"] == FAKE_SUMMARY
