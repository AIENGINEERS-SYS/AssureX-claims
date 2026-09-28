"""Unbound extensions keep blueprints and models independent of the app factory."""
from ipaddress import ip_address

from flask import current_app, has_request_context, request
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from backend.db.base import Base


def _canonical_client_ip(value):
    """Return one stable key for equivalent IPv4/IPv6 spellings."""
    if not isinstance(value, str):
        return None
    try:
        address = ip_address(value.strip())
    except ValueError:
        return None
    # A client can arrive as either 192.0.2.1 or ::ffff:192.0.2.1 depending on
    # the proxy/network stack. Treat those as the same rate-limit identity.
    if getattr(address, "ipv4_mapped", None) is not None:
        address = address.ipv4_mapped
    return address.compressed.lower()


def rate_limit_key():
    """Resolve a spoof-resistant client key for Flask-Limiter.

    Proxy headers are ignored unless RATELIMIT_TRUST_PROXY_HEADERS is enabled.
    When enabled, only the hop selected from the *right* side of
    X-Forwarded-For is trusted; attacker-controlled prefixes therefore cannot
    rotate buckets when the trusted proxy appends the real client address.
    Malformed or incomplete forwarding data fails closed to the socket peer.
    """
    if not has_request_context():
        return "no-request-context"

    peer = _canonical_client_ip(request.remote_addr)
    if peer is None:
        raw_peer = (request.remote_addr or "unknown").strip().lower()
        peer = raw_peer or "unknown"

    if not current_app.config.get("RATELIMIT_TRUST_PROXY_HEADERS", False):
        return peer

    trusted_hops = current_app.config.get("RATELIMIT_TRUSTED_PROXY_COUNT", 1)
    if type(trusted_hops) is not int or trusted_hops < 1:
        return peer

    forwarded = request.headers.get("X-Forwarded-For", "")
    chain = [item.strip() for item in forwarded.split(",") if item.strip()]
    if len(chain) < trusted_hops:
        return peer

    client = _canonical_client_ip(chain[-trusted_hops])
    return client or peer


db = SQLAlchemy(model_class=Base)
bcrypt = Bcrypt()
jwt = JWTManager()
migrate = Migrate()
limiter = Limiter(key_func=rate_limit_key)
