import os

from app.models import Document
from tests.conftest import FAKE_QUESTIONS, FAKE_SUMMARY, upload


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


def test_index_page_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"PDF RAG Agent" in res.data


def test_upload_requires_a_file(client):
    res = client.post("/api/documents", data={}, content_type="multipart/form-data")
    assert res.status_code == 400
    assert "file" in res.get_json()["error"]


def test_upload_rejects_unsupported_extension(client):
    res = upload(client, b"MZ\x90\x00", filename="setup.exe")
    assert res.status_code == 415
    assert "Unsupported file type" in res.get_json()["error"]


def test_upload_rejects_renamed_non_pdf(client):
    res = upload(client, b"just some text, not a pdf", filename="fake.pdf")
    assert res.status_code == 415
    assert "not a valid PDF" in res.get_json()["error"]


def test_upload_ingests_document(client):
    res = upload(client)
    assert res.status_code == 202
    doc_id = res.get_json()["document"]["id"]
    assert res.headers["Location"].endswith(f"/api/documents/{doc_id}")

    doc = client.get(f"/api/documents/{doc_id}").get_json()["document"]
    assert doc["status"] == "ready"
    assert doc["progress"] == 100
    assert doc["page_count"] == 3
    assert doc["chunk_count"] >= 3
    assert doc["filename"] == "manual.pdf"
    assert doc["stage"] is None
    assert doc["needs_reindex"] is False
    assert doc["summary"] == FAKE_SUMMARY
    assert doc["suggested_questions"] == FAKE_QUESTIONS

    listed = client.get("/api/documents").get_json()["documents"]
    assert [d["id"] for d in listed] == [doc_id]


def test_duplicate_upload_reuses_existing_document(client):
    first = upload(client).get_json()["document"]
    res = upload(client, filename="copy-of-manual.pdf")
    assert res.status_code == 200
    body = res.get_json()
    assert body["duplicate"] is True
    assert body["document"]["id"] == first["id"]


def test_failed_document_is_retried_on_reupload(client, monkeypatch):
    from app.services import ingestion

    original = ingestion.split_pages

    def broken(*args, **kwargs):
        raise ingestion.IngestionError("boom")

    monkeypatch.setattr(ingestion, "split_pages", broken)
    doc_id = upload(client).get_json()["document"]["id"]
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["status"] == "failed"

    monkeypatch.setattr(ingestion, "split_pages", original)
    res = upload(client)
    assert res.status_code == 202
    assert res.get_json()["document"]["id"] == doc_id
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["status"] == "ready"


def test_retry_endpoint_reprocesses_failed_document(client, monkeypatch):
    from app.services import ingestion

    original = ingestion.split_pages
    monkeypatch.setattr(ingestion, "split_pages", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    doc_id = upload(client).get_json()["document"]["id"]
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["status"] == "failed"

    monkeypatch.setattr(ingestion, "split_pages", original)
    res = client.post(f"/api/documents/{doc_id}/retry")
    assert res.status_code == 202
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["status"] == "ready"

    # Only failed documents can be retried.
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 409


def test_delete_document_removes_record_and_file(app, client):
    doc_id = upload(client).get_json()["document"]["id"]
    with app.app_context():
        from app.extensions import db

        file_path = db.session.get(Document, doc_id).file_path
    assert os.path.exists(file_path)

    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert not os.path.exists(file_path)
    res = client.get(f"/api/documents/{doc_id}")
    assert res.status_code == 404
    assert res.get_json() == {"error": "Document not found."}


def test_unknown_document_returns_json_404(client):
    res = client.delete("/api/documents/does-not-exist")
    assert res.status_code == 404
    assert "error" in res.get_json()
