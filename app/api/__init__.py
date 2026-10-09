from flask import Blueprint, current_app, request
from flask_jwt_extended import verify_jwt_in_request

api = Blueprint("api", __name__, url_prefix="/api")

# Everything else requires a logged-in user: secure by default, so a new
# route can't be left unprotected by accident.
PUBLIC_ENDPOINTS = {"api.health", "api.auth_status", "api.register", "api.login", "api.logout"}


def _body_limit(endpoint: str | None) -> int:
    """MAX_CONTENT_LENGTH is sized for big uploads; every other route only
    takes small JSON, so it gets a much smaller limit (checked before the body
    is read, even for requests that aren't logged in)."""
    config = current_app.config
    if endpoint == "api.upload_document":
        return config["MAX_CONTENT_LENGTH"]
    if endpoint == "api.transcribe":
        return (config["MAX_AUDIO_MB"] + 1) * 1024 * 1024  # + room for the multipart framing
    return config["MAX_JSON_BYTES"]


@api.before_request
def limit_body_and_require_login():
    request.max_content_length = _body_limit(request.endpoint)
    if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS:
        verify_jwt_in_request()


from app.api import auth, chat, documents, voice  # noqa: E402,F401  (register routes)
