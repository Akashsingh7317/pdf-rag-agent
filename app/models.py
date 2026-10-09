"""Database models: users, their documents, and chat history."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .services import file_types


class DocumentStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

    IN_PROGRESS = (QUEUED, PROCESSING)


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # SQLite and MySQL hand back naive datetimes; we always store UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)  # scrypt, salted

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "created_at": _iso(self.created_at)}


class Document(db.Model):
    __tablename__ = "documents"
    # The same file is embedded once per user, never shared across users.
    __table_args__ = (UniqueConstraint("user_id", "file_hash", name="uq_documents_user_file"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)  # SHA-256
    size_bytes: Mapped[int] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.QUEUED, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    stage: Mapped[str | None] = mapped_column(String(120))     # e.g. "Reading scanned page 3 of 12"
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    summary: Mapped[str | None] = mapped_column(Text)
    suggested_questions: Mapped[list | None] = mapped_column(JSON)
    # Vectors from different embedding models can't be compared, so we record
    # which model indexed the document and ask for a re-index if it changes.
    embedding_model: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )

    @property
    def kind(self) -> str:
        """pdf | docx | text | image, from the stored file's extension."""
        return file_types.kind_for(self.file_path) or "pdf"

    @property
    def version(self) -> str:
        """Changes whenever the document is re-indexed (used as a cache key)."""
        return f"{self.embedding_model}:{self.chunk_count}:{_iso(self.updated_at)}"

    def to_dict(self, current_embedding_model: str | None = None) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "page_count": self.page_count,
            "chunk_count": self.chunk_count,
            "error": self.error,
            "summary": self.summary,
            "suggested_questions": self.suggested_questions or [],
            "needs_reindex": bool(
                current_embedding_model
                and self.status == DocumentStatus.READY
                and self.embedding_model != current_embedding_model
            ),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # NULL = a conversation across all of the user's documents.
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[Document | None] = relationship(back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "sources": self.sources or [],
            "created_at": _iso(self.created_at),
        }
