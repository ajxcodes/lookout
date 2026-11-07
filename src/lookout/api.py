"""Compatibility module exposing the FastAPI application."""

from lookout.adapters.fastapi_ingress import app, create_app

__all__ = ["app", "create_app"]
