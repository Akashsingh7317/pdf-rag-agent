"""Accounts, sessions and per-user isolation."""

from app.extensions import db
from app.models import ChatMessage, Document
from tests.conftest import PASSWORD, logged_in_client, sign_up, upload


def test_status_reports_first_run(app):
    anonymous = app.test_client()
    body = anonymous.get("/api/auth/status").get_json()
    assert body == {"user": None, "has_users": False, "unclaimed_documents": 0}


def test_register_logs_in_with_cookie_and_token(app):
    browser = app.test_client()
    res = sign_up(browser, "Ravi@Example.com", name="Ravi")
    body = res.get_json()
    assert body["user"]["email"] == "ravi@example.com"  # normalised
    assert body["user"]["name"] == "Ravi"
    assert body["access_token"]
    assert "password" not in str(body["user"])
    assert "access_token_cookie" in res.headers.get("Set-Cookie", "") or browser.get_cookie("access_token_cookie")

    status = browser.get("/api/auth/status").get_json()
    assert status["user"]["email"] == "ravi@example.com"
    assert status["has_users"] is True


def test_register_validates_input(app):
    anonymous = app.test_client()
    bad_email = anonymous.post("/api/auth/register", json={"email": "nope", "password": PASSWORD})
    assert bad_email.status_code == 400
    short = anonymous.post("/api/auth/register", json={"email": "a@b.co", "password": "short"})
    assert short.status_code == 400
    assert "at least 8" in short.get_json()["error"]


def test_duplicate_email_is_rejected(app):
    sign_up(app.test_client(), "same@example.com")
    res = app.test_client().post(
        "/api/auth/register", json={"email": "same@example.com", "password": PASSWORD}
    )
    assert res.status_code == 409


def test_login_and_logout(app):
    sign_up(app.test_client(), "mira@example.com")
    browser = app.test_client()
    wrong = browser.post("/api/auth/login", json={"email": "mira@example.com", "password": "wrong-password"})
    unknown = browser.post("/api/auth/login", json={"email": "who@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json()  # doesn't reveal which emails exist

    assert browser.post("/api/auth/login", json={"email": "mira@example.com", "password": PASSWORD}).status_code == 200
    assert browser.get("/api/auth/me").status_code == 200
    browser.post("/api/auth/logout")
    assert browser.get("/api/auth/me").status_code == 401


def test_api_requires_login(app):
    anonymous = app.test_client()
    for method, url in [("get", "/api/documents"), ("post", "/api/chat"), ("post", "/api/transcribe")]:
        res = getattr(anonymous, method)(url)
        assert res.status_code == 401, url
        assert res.get_json() == {"error": "Please log in to continue."}
    assert anonymous.get("/api/health").status_code == 200  # public


def test_cookie_sessions_need_the_csrf_header_for_writes(app):
    browser = app.test_client()
    sign_up(browser)
    assert browser.get("/api/documents").status_code == 200  # reads are fine
    blocked = browser.delete("/api/chat/messages")
    assert blocked.status_code == 401
    assert "Security check failed" in blocked.get_json()["error"]

    csrf = browser.get_cookie("csrf_access_token").value
    assert browser.delete("/api/chat/messages", headers={"X-CSRF-TOKEN": csrf}).status_code == 204


def test_users_only_see_their_own_documents(app):
    asha = logged_in_client(app, "asha@example.com")
    ravi = logged_in_client(app, "ravi@example.com")
    doc_id = upload(asha).get_json()["document"]["id"]

    assert ravi.get("/api/documents").get_json()["documents"] == []
    for method, url in [
        ("get", f"/api/documents/{doc_id}"),
        ("get", f"/api/documents/{doc_id}/file"),
        ("get", f"/api/documents/{doc_id}/messages"),
        ("post", f"/api/documents/{doc_id}/chat"),
        ("delete", f"/api/documents/{doc_id}"),
    ]:
        assert getattr(ravi, method)(url, json={"question": "hi"}).status_code == 404, url
    assert ravi.post("/api/chat", json={"question": "warranty?"}).status_code == 409  # no docs of his own


def test_same_file_is_separate_per_user(app):
    asha = logged_in_client(app, "asha@example.com")
    ravi = logged_in_client(app, "ravi@example.com")
    first = upload(asha)
    second = upload(ravi)
    assert first.status_code == second.status_code == 202
    assert first.get_json()["document"]["id"] != second.get_json()["document"]["id"]


def test_first_account_claims_documents_from_before_accounts_existed(app):
    with app.app_context():
        legacy = Document(filename="old.pdf", file_path="old.pdf", file_hash="b" * 64, size_bytes=1, status="ready")
        db.session.add(legacy)
        db.session.flush()
        db.session.add(ChatMessage(document_id=legacy.id, role="user", content="hello"))
        db.session.commit()
        legacy_id = legacy.id

    assert app.test_client().get("/api/auth/status").get_json()["unclaimed_documents"] == 1
    first = logged_in_client(app, "owner@example.com")
    assert [d["id"] for d in first.get("/api/documents").get_json()["documents"]] == [legacy_id]
    assert len(first.get(f"/api/documents/{legacy_id}/messages").get_json()["messages"]) == 1

    second = logged_in_client(app, "later@example.com")
    assert second.get("/api/documents").get_json()["documents"] == []
