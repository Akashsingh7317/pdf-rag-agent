"""JSON error responses for the API: every error has the shape {"error": "..."}."""

import logging

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from app.services.llm import LLMConfigError

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        return jsonify(error=exc.message), exc.status

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(exc):
        limit = request.max_content_length or app.config["MAX_CONTENT_LENGTH"]
        if limit < 1024 * 1024:
            return jsonify(error="Request is too large."), 413
        return jsonify(error=f"File is too large. The limit is {limit // (1024 * 1024)} MB."), 413

    @app.errorhandler(LLMConfigError)
    def handle_llm_config_error(exc: LLMConfigError):
        # e.g. an unknown EMBEDDING_PROVIDER: say what to fix instead of a bare 500.
        logger.error("AI provider configuration: %s", exc)
        return jsonify(error=str(exc)), 500

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if not request.path.startswith("/api/"):
            return exc
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.path)
        return jsonify(error="Internal server error"), 500
