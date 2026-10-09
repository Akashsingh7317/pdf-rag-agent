"""PDF RAG Agent: chat with your PDFs, Word files, text files and photos,
with cited answers, hybrid search, voice questions and user accounts."""

import logging
import os
import secrets

from flask import Flask, render_template

from app.api import api
from app.config import Config
from app.database import MIGRATIONS_DIR, fail_interrupted_jobs, migrate_legacy_vectors, upgrade_database
from app.errors import register_error_handlers
from app.extensions import db, limiter, migrate
from app.security import init_auth
from app.tasks import init_celery, init_thread_pool

logger = logging.getLogger(__name__)

# The UI is one page with its own scripts and styles: no inline scripts, no
# third-party origins. Inline styles are allowed for progress-bar widths.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    _configure_logging(app.config["LOG_LEVEL"])
    _ensure_dirs(app)
    _ensure_secret_key(app)

    db.init_app(app)
    migrate.init_app(app, db, directory=MIGRATIONS_DIR, render_as_batch=True)
    init_auth(app)
    limiter.init_app(app)
    register_error_handlers(app)
    app.register_blueprint(api)

    init_celery(app)
    if app.config["INGESTION_BACKEND"] == "thread":
        init_thread_pool(app)

    @app.get("/")
    def index():
        max_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return render_template("index.html", max_upload_mb=max_mb)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # Only on HTML pages: a CSP on an inline PDF can stop the browser's PDF viewer.
        if response.mimetype == "text/html":
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    with app.app_context():
        if app.config["AUTO_MIGRATE"]:
            upgrade_database()
        migrate_legacy_vectors()
        if app.config["INGESTION_BACKEND"] == "thread":
            fail_interrupted_jobs()

    return app


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _ensure_dirs(app: Flask) -> None:
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///") and ":memory:" not in uri:
        os.makedirs(os.path.dirname(uri.removeprefix("sqlite:///")) or ".", exist_ok=True)


def _ensure_secret_key(app: Flask) -> None:
    """Use SECRET_KEY from the environment; for local runs without one,
    generate a random key once and keep it in the data folder."""
    if app.config.get("SECRET_KEY"):
        return
    path = os.path.join(app.config["DATA_DIR"], ".secret_key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as file:
            file.write(secrets.token_hex(32))
        logger.warning("SECRET_KEY is not set; generated one in %s (set SECRET_KEY in production)", path)
    except FileExistsError:
        pass
    with open(path, encoding="utf-8") as file:
        app.config["SECRET_KEY"] = file.read().strip()
