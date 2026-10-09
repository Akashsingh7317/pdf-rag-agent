"""Accounts: sign up, log in, log out, current user."""

import re

from flask import current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    current_user,
    get_current_user,
    set_access_cookies,
    unset_jwt_cookies,
    verify_jwt_in_request,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.api import api
from app.errors import ApiError
from app.extensions import db, limiter
from app.models import ChatMessage, Document, User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Checked when the email is unknown, so a wrong email takes as long as a wrong
# password and response times don't reveal which emails have accounts.
_DUMMY_HASH = generate_password_hash("not-a-real-password")
LOGIN_FAILED = "Incorrect email or password."


def _auth_limit() -> str:
    return current_app.config["AUTH_RATE_LIMIT"]


def _user_count() -> int:
    return db.session.scalar(select(func.count()).select_from(User))


def _form() -> dict:
    data = request.get_json(silent=True) or {}
    return {
        "name": str(data.get("name") or "").strip()[:100],
        "email": str(data.get("email") or "").strip().lower(),
        "password": str(data.get("password") or ""),
    }


def _session_response(user: User, status: int = 200, **extra):
    token = create_access_token(identity=user.id)
    response = jsonify(user=user.to_dict(), access_token=token, **extra)
    set_access_cookies(response, token)
    return response, status


def _claim_unowned_data(user: User) -> int:
    """Documents and chats from before accounts existed go to the first account."""
    claimed = db.session.execute(
        update(Document).where(Document.user_id.is_(None)).values(user_id=user.id)
    ).rowcount
    db.session.execute(
        update(ChatMessage).where(ChatMessage.user_id.is_(None)).values(user_id=user.id)
    )
    return claimed


@api.get("/auth/status")
def auth_status():
    """Who is logged in, and whether any account exists yet (the page shows
    "Create your account" on first run)."""
    try:
        verify_jwt_in_request(optional=True)
        user = get_current_user()
    except Exception:  # noqa: BLE001 - an expired or invalid cookie just means "logged out"
        user = None
    payload = {"user": user.to_dict() if user else None, "has_users": _user_count() > 0}
    if not payload["has_users"]:
        payload["unclaimed_documents"] = db.session.scalar(
            select(func.count()).select_from(Document).where(Document.user_id.is_(None))
        )
    return jsonify(payload)


@api.post("/auth/register")
@limiter.limit(_auth_limit)
def register():
    form = _form()
    if not EMAIL_RE.match(form["email"]) or len(form["email"]) > 255:
        raise ApiError("Enter a valid email address.")
    min_length = current_app.config["PASSWORD_MIN_LENGTH"]
    if len(form["password"]) < min_length:
        raise ApiError(f"Password must be at least {min_length} characters.")
    max_length = current_app.config["PASSWORD_MAX_LENGTH"]
    if len(form["password"]) > max_length:
        raise ApiError(f"Password must be at most {max_length} characters.")
    if db.session.scalar(select(User).filter_by(email=form["email"])):
        raise _email_taken()

    first_user = _user_count() == 0
    user = User(email=form["email"], name=form["name"] or form["email"].split("@")[0])
    user.set_password(form["password"])
    db.session.add(user)
    try:
        db.session.flush()
    except IntegrityError:
        # The same email was registered concurrently and the other request won.
        db.session.rollback()
        raise _email_taken() from None
    claimed = _claim_unowned_data(user) if first_user else 0
    db.session.commit()
    return _session_response(user, 201, claimed_documents=claimed)


def _email_taken() -> ApiError:
    return ApiError("An account with this email already exists. Log in instead.", 409)


@api.post("/auth/login")
@limiter.limit(_auth_limit)
def login():
    form = _form()
    if len(form["password"]) > current_app.config["PASSWORD_MAX_LENGTH"]:
        raise ApiError(LOGIN_FAILED, 401)  # no account can have it
    user = db.session.scalar(select(User).filter_by(email=form["email"]))
    if user is None:
        check_password_hash(_DUMMY_HASH, form["password"])
        raise ApiError(LOGIN_FAILED, 401)
    if not user.check_password(form["password"]):
        raise ApiError(LOGIN_FAILED, 401)
    return _session_response(user)


@api.post("/auth/logout")
def logout():
    response = jsonify(ok=True)
    unset_jwt_cookies(response)
    return response


@api.get("/auth/me")
def me():
    return jsonify(user=current_user.to_dict())
