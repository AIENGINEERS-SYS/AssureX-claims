"""Collision-resistant public identifiers; uniqueness is also enforced by the DB."""
from uuid import uuid4


def public_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:20].upper()}"
