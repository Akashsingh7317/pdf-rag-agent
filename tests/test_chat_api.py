from app.extensions import db
from app.models import Document, DocumentStatus
from app.services import llm
from tests.conftest import FAKE_ANSWER, FAKE_STANDALONE_QUESTION, make_docx, parse_sse, upload


def ask(client, doc_id, question):
    url = "/api/chat" if doc_id is None else f"/api/documents/{doc_id}/chat"
    res = client.post(url, json={"question": question})
    res.get_data()  # drain the stream so the answer is generated and saved
    return res


def my_user_id(client) -> str:
    return client.get("/api/auth/me").get_json()["user"]["id"]


def test_chat_streams_answer_with_sources_and_saves_history(client):
    doc_id = upload(client).get_json()["document"]["id"]

    res = ask(client, doc_id, "How long is the warranty?")
    assert res.status_code == 200
    assert res.mimetype == "text/event-stream"

    events = parse_sse(res.data)
    names = [name for name, _ in events]
    assert names[0] == "sources"
    assert names[-1] == "done"
    assert set(names[1:-1]) == {"token"}

    sources = events[0][1]["sources"]
    assert sources and all({"doc_id", "filename", "page", "snippet"} <= set(s) for s in sources)
    assert {s["filename"] for s in sources} == {"manual.pdf"}
    assert [s["page"] for s in sources] == sorted(s["page"] for s in sources)
    # First question has no history, so it is searched as-is.
    assert events[0][1]["search_query"] == "How long is the warranty?"

    answer = "".join(data["text"] for name, data in events if name == "token")
    assert answer == FAKE_ANSWER

    messages = client.get(f"/api/documents/{doc_id}/messages").get_json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == FAKE_ANSWER
    assert messages[1]["sources"] == sources


def test_follow_up_question_is_rewritten_using_history(client):
    doc_id = upload(client).get_json()["document"]["id"]
    ask(client, doc_id, "Tell me about the X200 printer warranty")

    events = parse_sse(ask(client, doc_id, "How long is it?").data)
    assert events[0][1]["search_query"] == FAKE_STANDALONE_QUESTION


def test_chat_rejects_document_that_is_not_ready(app, client):
    with app.app_context():
        doc = Document(
            user_id=my_user_id(client), filename="big.pdf", file_path="x", file_hash="a" * 64,
            size_bytes=1, status=DocumentStatus.PROCESSING,
        )
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id

    assert ask(client, doc_id, "Anything?").status_code == 409


def test_chat_validates_question(client):
    doc_id = upload(client).get_json()["document"]["id"]
    assert ask(client, doc_id, "   ").status_code == 400
    assert ask(client, doc_id, "x" * 2001).status_code == 400


def test_llm_failure_is_reported_as_sse_error_and_not_saved(client, monkeypatch):
    doc_id = upload(client).get_json()["document"]["id"]

    def missing_key(streaming=True):
        raise llm.LLMConfigError("GROQ_API_KEY is not set.")

    monkeypatch.setattr(llm, "get_chat_model", missing_key)
    events = parse_sse(ask(client, doc_id, "How long is the warranty?").data)
    assert events[-1] == ("error", {"message": "GROQ_API_KEY is not set."})
    assert client.get(f"/api/documents/{doc_id}/messages").get_json()["messages"] == []


def test_clear_messages(client):
    doc_id = upload(client).get_json()["document"]["id"]
    ask(client, doc_id, "How long is the warranty?")
    assert len(client.get(f"/api/documents/{doc_id}/messages").get_json()["messages"]) == 2
    assert client.delete(f"/api/documents/{doc_id}/messages").status_code == 204
    assert client.get(f"/api/documents/{doc_id}/messages").get_json()["messages"] == []


# --- Across all documents -----------------------------------------------------------

def test_all_documents_chat_uses_every_ready_document(client):
    manual = upload(client).get_json()["document"]["id"]
    notes = upload(
        client, make_docx(["Offsite in Rishikesh on 14 November."]), filename="offsite.docx"
    ).get_json()["document"]["id"]

    res = ask(client, None, "What is planned?")
    events = parse_sse(res.data)
    assert events[-1][0] == "done"
    sources = events[0][1]["sources"]
    assert {s["doc_id"] for s in sources} == {manual, notes}
    assert {s["filename"] for s in sources} == {"manual.pdf", "offsite.docx"}

    # The conversation is separate from each document's own chat.
    all_messages = client.get("/api/chat/messages").get_json()["messages"]
    assert [m["role"] for m in all_messages] == ["user", "assistant"]
    assert client.get(f"/api/documents/{manual}/messages").get_json()["messages"] == []

    assert client.delete("/api/chat/messages").status_code == 204
    assert client.get("/api/chat/messages").get_json()["messages"] == []


def test_all_documents_chat_needs_a_ready_document(client):
    res = client.post("/api/chat", json={"question": "Anything?"})
    assert res.status_code == 409
    assert "No documents are ready" in res.get_json()["error"]


def test_all_documents_chat_skips_failed_documents(client):
    good = upload(client).get_json()["document"]["id"]
    failed = upload(client, b"   ", filename="empty.txt").get_json()["document"]["id"]
    assert client.get(f"/api/documents/{failed}").get_json()["document"]["status"] == "failed"

    sources = parse_sse(ask(client, None, "warranty").data)[0][1]["sources"]
    assert {s["doc_id"] for s in sources} == {good}
