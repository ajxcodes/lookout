"""Compatibility shim for FastAPI ingress imports."""

from lookout.adapters.fastapi_ingress import (
    FastAPIIngressAdapter,
    IngressFastAPIAdapter,
    app,
    create_app,
)

__all__ = ["FastAPIIngressAdapter", "IngressFastAPIAdapter", "app", "create_app"]
