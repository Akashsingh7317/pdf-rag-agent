"""Celery worker entry point (used when INGESTION_BACKEND=celery).

Linux / Docker:  celery -A celery_worker.celery_app worker --loglevel=info --concurrency=2
Windows:         celery -A celery_worker.celery_app worker --loglevel=info --pool=solo
"""

from app import create_app

flask_app = create_app()
celery_app = flask_app.extensions["celery"]
