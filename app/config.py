"""Application configuration, loaded from environment variables (.env)."""

import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    DATA_DIR = DATA_DIR

    # --- Security -------------------------------------------------------------
    # Signs login tokens. Set it in production; for local runs one is generated
    # once and kept in data/.secret_key so logins survive restarts.
    SECRET_KEY = os.environ.get("SECRET_KEY") or None
    JWT_TOKEN_LOCATION = ["headers", "cookies"]   # API clients: Bearer header; browser: cookie
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=_int("SESSION_DAYS", 7))
    JWT_COOKIE_SECURE = _bool("COOKIE_SECURE", False)  # set true when served over HTTPS
    JWT_COOKIE_SAMESITE = "Lax"
    JWT_COOKIE_CSRF_PROTECT = True                # cookie logins must send X-CSRF-TOKEN
    JWT_SESSION_COOKIE = False                    # keep the login after the browser closes
    PASSWORD_MIN_LENGTH = 8
    PASSWORD_MAX_LENGTH = 1024  # hashing very long passwords is a cheap way to burn CPU
    # Uploads and recordings have their own limits; any other request is small JSON.
    MAX_JSON_BYTES = 64 * 1024

    # --- Database -----------------------------------------------------------
    # SQLite needs zero setup for local dev. In Docker/production point this
    # at MySQL: mysql+pymysql://user:pass@host:3306/pdf_rag
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(DATA_DIR, "app.db")
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    # Apply database migrations on start-up. Docker runs "flask db upgrade" as a
    # separate step instead, so several processes never migrate at once.
    AUTO_MIGRATE = _bool("AUTO_MIGRATE", True)

    # --- Uploads ------------------------------------------------------------
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))
    MAX_CONTENT_LENGTH = _int("MAX_UPLOAD_MB", 200) * 1024 * 1024

    # --- Vector store (Chroma) ----------------------------------------------
    # Local: embedded Chroma persisted to disk. Docker: set CHROMA_HOST to use
    # the Chroma server so the web app and the Celery worker share one store.
    CHROMA_DIR = os.environ.get("CHROMA_DIR", os.path.join(DATA_DIR, "chroma"))
    CHROMA_HOST = os.environ.get("CHROMA_HOST") or None
    CHROMA_PORT = _int("CHROMA_PORT", 8000)

    # --- AI providers ---------------------------------------------------------
    # LLM_PROVIDER:       openai | groq   (Groq has a free tier)
    # EMBEDDING_PROVIDER: openai | local  (local = all-MiniLM-L6-v2 on CPU: free, offline)
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "openai").strip().lower()
    OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    GROQ_CHAT_MODEL = os.environ.get("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
    # Vision models read uploaded photos and scanned PDF pages.
    GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
    OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")
    # Speech-to-text for voice questions.
    GROQ_TRANSCRIBE_MODEL = os.environ.get("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3-turbo")
    OPENAI_TRANSCRIBE_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    MAX_AUDIO_MB = _int("MAX_AUDIO_MB", 20)

    # --- Ingestion ------------------------------------------------------------
    CHUNK_SIZE = _int("CHUNK_SIZE", 1200)
    CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 200)
    EMBED_BATCH_SIZE = _int("EMBED_BATCH_SIZE", 100)
    # Scanned PDF pages (no text layer) are read by the vision model.
    OCR_ENABLED = _bool("OCR_ENABLED", True)
    OCR_MAX_PAGES = _int("OCR_MAX_PAGES", 150)
    OCR_MIN_CHARS = 25  # a page with less text than this is treated as scanned
    SUMMARY_ENABLED = _bool("SUMMARY_ENABLED", True)

    # --- Retrieval ------------------------------------------------------------
    # hybrid = vector (meaning) + BM25 keywords (exact terms), fused with RRF
    RETRIEVAL_MODE = os.environ.get("RETRIEVAL_MODE", "hybrid")
    RETRIEVAL_K = _int("RETRIEVAL_K", 5)                    # chunks sent to the LLM
    RETRIEVAL_CANDIDATES = _int("RETRIEVAL_CANDIDATES", 20)  # per search method, before fusion
    RRF_K = 60                                               # standard Reciprocal Rank Fusion constant
    MAX_CHUNKS_PER_PAGE = 2                                  # keeps the context diverse
    HISTORY_TURNS = _int("HISTORY_TURNS", 3)                 # past Q&A pairs used for follow-ups
    MAX_QUESTION_CHARS = 2000

    # --- Background ingestion -----------------------------------------------
    # "thread": in-process thread pool (local dev, single process)
    # "celery": Celery worker + Redis broker (Docker / production)
    # "sync":   run inline in the request (tests only)
    INGESTION_BACKEND = os.environ.get("INGESTION_BACKEND", "thread")
    INGESTION_THREADS = _int("INGESTION_THREADS", 2)
    # A job that saved no progress for this long is treated as stuck: the user
    # may then retry or delete it (progress is saved at least every few minutes).
    STALE_JOB_MINUTES = _int("STALE_JOB_MINUTES", 30)
    CELERY = {
        "broker_url": REDIS_URL,
        "task_ignore_result": True,
        "task_acks_late": True,              # re-deliver if a worker dies mid-ingest
        "task_reject_on_worker_lost": True,
        "worker_prefetch_multiplier": 1,     # one big PDF per worker process at a time
        # The soft limit raises inside the task, so the document is marked failed;
        # the hard limit kills the process and would leave it "processing".
        "task_soft_time_limit": 55 * 60,
        "task_time_limit": 60 * 60,
    }

    # --- Rate limiting (Flask-Limiter) --------------------------------------
    # Limits are per logged-in user (per IP for anonymous requests). memory://
    # is per-process; use Redis with several workers: redis://localhost:6379/1
    RATELIMIT_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = "300 per minute"
    RATELIMIT_HEADERS_ENABLED = True
    UPLOAD_RATE_LIMIT = os.environ.get("UPLOAD_RATE_LIMIT", "10 per minute")
    CHAT_RATE_LIMIT = os.environ.get("CHAT_RATE_LIMIT", "30 per minute")
    TRANSCRIBE_RATE_LIMIT = os.environ.get("TRANSCRIBE_RATE_LIMIT", "20 per minute")
    AUTH_RATE_LIMIT = os.environ.get("AUTH_RATE_LIMIT", "10 per minute")

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
