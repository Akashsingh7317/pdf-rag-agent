"""Stuck jobs, request limits, Devanagari keyword search, partial OCR failure
and security headers."""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import update

from app.api import auth
from app.config import Config
from app.extensions import db
from app.models import Document, DocumentStatus
from app.services import llm
from app.services.retrieval import tokenize
from tests.conftest import PASSWORD, make_pdf, upload


def _uploaded(client) -> str:
    return upload(client).get_json()["document"]["id"]


def _set_in_progress(app, doc_id: str, minutes_ago: int) -> None:
    with app.app_context():
        db.session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(
                status=DocumentStatus.PROCESSING,
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            )
        )
        db.session.commit()


# --- Stuck jobs -------------------------------------------------------------------

def test_celery_soft_time_limit_fires_before_the_hard_one():
    assert Config.CELERY["task_soft_time_limit"] < Config.CELERY["task_time_limit"]


def test_a_running_job_cannot_be_deleted_or_retried(app, client):
    doc_id = _uploaded(client)
    _set_in_progress(app, doc_id, minutes_ago=1)
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["stuck"] is False
    assert client.delete(f"/api/documents/{doc_id}").status_code == 409
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 409


def test_a_stuck_job_can_be_retried(app, client):
    doc_id = _uploaded(client)
    _set_in_progress(app, doc_id, minutes_ago=app.config["STALE_JOB_MINUTES"] + 5)
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["stuck"] is True

    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 202
    doc = client.get(f"/api/documents/{doc_id}").get_json()["document"]
    assert doc["status"] == "ready"
    assert doc["stuck"] is False


def test_a_stuck_job_can_be_deleted(app, client):
    doc_id = _uploaded(client)
    _set_in_progress(app, doc_id, minutes_ago=app.config["STALE_JOB_MINUTES"] + 5)
    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert client.get(f"/api/documents/{doc_id}").status_code == 404


# --- Request limits and registration -------------------------------------------------

def test_large_json_bodies_are_rejected_before_login(app):
    res = app.test_client().post(
        "/api/auth/login",
        data='{"email": "a@b.co", "password": "' + "x" * (70 * 1024) + '"}',
        content_type="application/json",
    )
    assert res.status_code == 413
    assert "error" in res.get_json()


def test_uploads_keep_the_large_limit(client):
    assert upload(client).status_code == 202


def test_overlong_passwords_are_refused(app):
    test_client = app.test_client()
    long_password = "x" * (app.config["PASSWORD_MAX_LENGTH"] + 1)
    res = test_client.post(
        "/api/auth/register", json={"email": "long@example.com", "password": long_password}
    )
    assert res.status_code == 400
    res = test_client.post("/api/auth/login", json={"email": "long@example.com", "password": long_password})
    assert res.status_code == 401


def test_concurrent_registration_with_the_same_email_is_a_409(app, monkeypatch):
    test_client = app.test_client()
    form = {"email": "twin@example.com", "password": PASSWORD}
    assert test_client.post("/api/auth/register", json=form).status_code == 201

    # Simulate losing the race: the "email already exists" check sees nothing.
    real_select = auth.select
    monkeypatch.setattr(auth, "select", lambda *args: real_select(*args).where(sa.false()))
    res = test_client.post("/api/auth/register", json=form)
    assert res.status_code == 409
    assert "already exists" in res.get_json()["error"]


def test_bad_provider_setting_is_explained(app, client):
    _uploaded(client)
    app.config["EMBEDDING_PROVIDER"] = "nonsense"
    res = client.get("/api/documents")
    assert res.status_code == 500
    assert "EMBEDDING_PROVIDER" in res.get_json()["error"]


# --- Devanagari keyword search ----------------------------------------------------------

def test_devanagari_words_are_not_split_at_vowel_signs():
    assert tokenize("भुगतान की अंतिम तिथि क्या है।") == ["भुगतान", "की", "अंतिम", "तिथि", "क्या", "है"]


def test_mixed_scripts_and_numbers_tokenize():
    assert tokenize("Payment 15 Oct तक") == ["payment", "15", "oct", "तक"]


# --- OCR -------------------------------------------------------------------------------

def test_failed_ocr_on_every_scanned_page_fails_the_document(client, monkeypatch):
    def broken_vision_model():
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm, "get_vision_model", broken_vision_model)
    pages = ["A normal page with plenty of selectable text on it.", ""]
    doc_id = upload(client, make_pdf(pages), filename="mixed.pdf").get_json()["document"]["id"]
    doc = client.get(f"/api/documents/{doc_id}").get_json()["document"]
    assert doc["status"] == "failed"
    assert "rate limited" in doc["error"]


# --- Security headers ----------------------------------------------------------------------

def test_html_page_has_a_content_security_policy(app):
    res = app.test_client().get("/")
    assert "script-src 'self'" in res.headers["Content-Security-Policy"]
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_original_files_are_not_sniffed_and_have_no_csp(client):
    doc_id = _uploaded(client)
    res = client.get(f"/api/documents/{doc_id}/file")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in res.headers  # would break the browser's PDF viewer
