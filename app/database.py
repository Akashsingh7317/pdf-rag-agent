"""Schema migrations and one-time data upgrades, run at start-up."""

import logging
import os

import flask_migrate
import sqlalchemy as sa
from sqlalchemy import update

from app.config import BASE_DIR
from app.extensions import db
from app.models import Document, DocumentStatus
from app.services import vector_store

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")
BASELINE_REVISION = "0001_baseline"


def upgrade_database() -> None:
    """Bring the schema up to date. A database created before migrations
    existed already matches the baseline, so it is stamped, not re-created."""
    tables = set(sa.inspect(db.engine).get_table_names())
    if "documents" in tables and "alembic_version" not in tables:
        logger.info("Database has no migration history: marking it as %s", BASELINE_REVISION)
        flask_migrate.stamp(directory=MIGRATIONS_DIR, revision=BASELINE_REVISION)
    flask_migrate.upgrade(directory=MIGRATIONS_DIR)


def migrate_legacy_vectors() -> None:
    """Move vectors from the old per-document collections into the shared
    layout and record which embedding model produced them."""
    try:
        moved = vector_store.migrate_legacy_collections()
        for doc_id, model in moved.items():
            doc = db.session.get(Document, doc_id)
            if doc is not None and not doc.embedding_model:
                doc.embedding_model = model
        if moved:
            db.session.commit()
    except Exception:  # noqa: BLE001 - never block start-up; affected documents can be re-indexed
        db.session.rollback()
        logger.exception("Could not migrate old vector collections")


def fail_interrupted_jobs() -> None:
    """Thread-mode jobs die with the process. After a restart, mark them failed
    so the UI doesn't show them as 'processing' forever. (Celery mode doesn't
    need this: unacknowledged tasks are re-delivered to a worker.)"""
    db.session.execute(
        update(Document)
        .where(Document.status.in_(DocumentStatus.IN_PROGRESS))
        .values(
            status=DocumentStatus.FAILED,
            stage=None,
            error="Processing was interrupted by a server restart. Click Retry to process it again.",
        )
    )
    db.session.commit()
