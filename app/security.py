"""JWT login sessions.

Browsers get the token in an HttpOnly cookie (JavaScript can't read it, so an
XSS bug can't steal it) plus a CSRF token that must be echoed in the
X-CSRF-TOKEN header on every write. API clients can send the same token as
"Authorization: Bearer <token>" instead.
"""

from flask import Flask, jsonify

from app.extensions import db, jwt
from app.models import User


def init_auth(app: Flask) -> None:
    jwt.init_app(app)

    @jwt.user_lookup_loader
    def load_user(_header: dict, payload: dict):
        return db.session.get(User, payload["sub"])

    def unauthorized(message: str):
        return jsonify(error=message), 401

    @jwt.unauthorized_loader
    def missing_token(reason: str):
        if "CSRF" in reason:
            return unauthorized("Security check failed. Refresh the page and try again.")
        return unauthorized("Please log in to continue.")

    @jwt.invalid_token_loader
    def invalid_token(_reason: str):
        return unauthorized("Your session is invalid. Please log in again.")

    @jwt.expired_token_loader
    def expired_token(_header: dict, _payload: dict):
        return unauthorized("Your session has expired. Please log in again.")

    @jwt.user_lookup_error_loader
    def missing_user(_header: dict, _payload: dict):
        return unauthorized("This account no longer exists. Please log in again.")
