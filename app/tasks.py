"""Background ingestion. The API only calls enqueue_ingestion(); which
backend runs the job is a config switch (INGESTION_BACKEND)."""

import logging
from concurrent.futures import ThreadPoolExecutor

from celery import Celery, Task, shared_task
from flask import Flask, current_app

from app.services import ingestion

logger = logging.getLogger(__name__)


def init_celery(app: Flask) -> Celery:
    """Standard Flask + Celery wiring: every task runs inside an app context."""

    class FlaskTask(Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


def init_thread_pool(app: Flask) -> None:
    app.extensions["ingestion_pool"] = ThreadPoolExecutor(
        max_workers=app.config["INGESTION_THREADS"], thread_name_prefix="ingest"
    )


@shared_task(name="ingest_document")
def ingest_document_task(doc_id: str) -> None:
    ingestion.ingest_document(doc_id)


def _run_in_app_context(app: Flask, doc_id: str) -> None:
    with app.app_context():
        ingestion.ingest_document(doc_id)


def enqueue_ingestion(doc_id: str) -> None:
    app = current_app._get_current_object()
    backend = app.config["INGESTION_BACKEND"]
    logger.info("Queueing ingestion of %s via %s", doc_id, backend)

    if backend == "celery":
        ingest_document_task.delay(doc_id)
    elif backend == "sync":
        ingestion.ingest_document(doc_id)
    else:
        app.extensions["ingestion_pool"].submit(_run_in_app_context, app, doc_id)
