"""Isolated public share application package (port 8001)."""

from backend.app.public.app import create_public_app

__all__ = ["create_public_app"]
