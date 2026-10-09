FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall everything.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as a non-root user; uploads live on a shared volume at /app/data/uploads.
RUN useradd --create-home appuser \
    && mkdir -p /app/data/uploads \
    && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000

# gthread workers: each streaming answer holds a thread, not a whole process.
CMD ["gunicorn", "wsgi:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "gthread", "--threads", "8", "--timeout", "120", "--access-logfile", "-"]
