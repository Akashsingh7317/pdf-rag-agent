"""Upgrading an installation from before accounts and the shared vector
collection existed: the database is stamped and migrated, the vectors are
moved without re-embedding, and the first account inherits everything."""

import sqlite3

import chromadb
import sqlalchemy as sa
from chromadb.config import Settings

from app import create_app
from app.extensions import db
from tests.conftest import parse_sse
from tests.conftest import logged_in_client

LEGACY_SCHEMA = """
CREATE TABLE documents (
    id VARCHAR(32) NOT NULL, filename VARCHAR(255) NOT NULL, file_path VARCHAR(500) NOT NULL,
    file_hash VARCHAR(64) NOT NULL, size_bytes INTEGER NOT NULL, status VARCHAR(20) NOT NULL,
    progress INTEGER NOT NULL, page_count INTEGER, chunk_count INTEGER, error TEXT,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id));
CREATE INDEX ix_documents_status ON documents (status);
CREATE UNIQUE INDEX ix_documents_file_hash ON documents (file_hash);
CREATE TABLE chat_messages (
    id INTEGER NOT NULL, document_id VARCHAR(32) NOT NULL, role VARCHAR(10) NOT NULL,
    content TEXT NOT NULL, sources JSON, created_at DATETIME NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE);
CREATE INDEX ix_chat_messages_document_id ON chat_messages (document_id);
"""
DOC_ID = "a" * 32
MODEL = "local/all-MiniLM-L6-v2"


def build_legacy_install(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(tmp_path / "app.db")
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        "INSERT INTO documents VALUES (?, 'old manual.pdf', ?, ?, 10, 'ready', 100, 3, 2, NULL,"
        " '2026-09-01 10:00:00', '2026-09-01 10:00:00')",
        (DOC_ID, str(tmp_path / "old.pdf"), "f" * 64),
    )
    connection.execute(
        "INSERT INTO chat_messages VALUES (1, ?, 'user', 'hello', NULL, '2026-09-01 10:01:00')", (DOC_ID,)
    )
    connection.commit()
    connection.close()

    chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    legacy = chroma.get_or_create_collection(f"doc_{DOC_ID}", metadata={"embedding_model": MODEL}, embedding_function=None)
    legacy.add(
        ids=[f"{DOC_ID}-0", f"{DOC_ID}-1"],
        embeddings=[[0.1] * 64, [0.2] * 64],
        documents=["The warranty lasts 2 years.", "Clean the glass weekly."],
        metadatas=[{"page": 2, "chunk": 0}, {"page": 3, "chunk": 1}],
    )


def test_legacy_install_is_upgraded_in_place(tmp_path, app):
    # `app` (unused) installs the fake models; this test builds its own app.
    build_legacy_install(tmp_path / "legacy")
    legacy = tmp_path / "legacy"
    upgraded = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{legacy / 'app.db'}",
            "UPLOAD_DIR": str(legacy / "uploads"),
            "CHROMA_DIR": str(legacy / "chroma"),
            "EMBEDDING_PROVIDER": "local",
            "INGESTION_BACKEND": "sync",
            "RATELIMIT_ENABLED": False,
        }
    )

    with upgraded.app_context():
        inspector = sa.inspect(db.engine)
        assert "users" in inspector.get_table_names()
        assert {"user_id", "summary", "embedding_model"} <= {c["name"] for c in inspector.get_columns("documents")}
        version = db.session.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0002_users_and_workspace"
        names = [c.name for c in upgraded.extensions["chroma_client"].list_collections()]
        assert names == ["chunks-local-all-minilm-l6-v2"]  # old per-document collection moved

    owner = logged_in_client(upgraded, "owner@example.com")
    documents = owner.get("/api/documents").get_json()["documents"]
    assert [(d["filename"], d["status"], d["needs_reindex"]) for d in documents] == [("old manual.pdf", "ready", False)]
    assert len(owner.get(f"/api/documents/{DOC_ID}/messages").get_json()["messages"]) == 1

    events = parse_sse(owner.post(f"/api/documents/{DOC_ID}/chat", json={"question": "warranty"}).get_data())
    assert events[-1][0] == "done"
    assert {s["page"] for s in events[0][1]["sources"]} == {2, 3}

    with upgraded.app_context():
        db.session.remove()
        db.engine.dispose()
