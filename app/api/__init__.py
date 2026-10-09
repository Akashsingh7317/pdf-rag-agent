from flask import Blueprint, request
from flask_jwt_extended import verify_jwt_in_request

api = Blueprint("api", __name__, url_prefix="/api")

# Everything else requires a logged-in user: secure by default, so a new
# route can't be left unprotected by accident.
PUBLIC_ENDPOINTS = {"api.health", "api.auth_status", "api.register", "api.login", "api.logout"}


@api.before_request
def require_login():
    if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS:
        verify_jwt_in_request()


from app.api import auth, chat, documents, voice  # noqa: E402,F401  (register routes)
