"""Compatibility shim for FastAPI ingress imports."""

from lookout.adapters.fastapi_ingress import (
    FastAPIAdapter,
    FastAPIIngressAdapter,
    app,
    create_app,
)

__all__ = ["FastAPIAdapter", "FastAPIIngressAdapter", "app", "create_app"]
