"""Supported upload types. The extension decides how a file is read; a check
of the first bytes stops renamed files (e.g. an .exe called photo.png)."""

import os

KINDS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "text",
    ".md": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

SUPPORTED_LABEL = "PDF, Word (.docx), text (.txt, .md) or image (.png, .jpg, .webp)"


def extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def kind_for(filename: str) -> str | None:
    return KINDS.get(extension(filename))


def content_matches(kind: str, head: bytes) -> bool:
    """Check the first bytes of the file against what its kind should look like."""
    if kind == "pdf":
        return b"%PDF-" in head[:1024]
    if kind == "docx":
        return head.startswith(b"PK\x03\x04")  # .docx is a zip archive
    if kind == "image":
        return (
            head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"\xff\xd8\xff")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        )
    if kind == "text":
        return b"\x00" not in head[:4096]  # binary files contain NUL bytes
    return False
