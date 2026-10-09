"""Flask extension instances, created here and bound to the app in create_app()."""

from flask_jwt_extended import JWTManager, get_jwt_identity, verify_jwt_in_request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def rate_limit_key() -> str:
    """Limit per logged-in user, so users behind one IP (an office, a proxy)
    don't share a budget; fall back to the IP address when not logged in."""
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
    except Exception:  # noqa: BLE001 - an invalid token is handled by the auth check
        identity = None
    return f"user:{identity}" if identity else get_remote_address()


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
jwt = JWTManager()
limiter = Limiter(rate_limit_key)
