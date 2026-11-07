"""CLI entrypoint for running the Lookout web service via python -m lookout."""

import os

import uvicorn


def main() -> None:
    """Run Lookout web service with uvicorn factory."""
    host = os.environ.get("LOOKOUT_HOST", "0.0.0.0")
    port = int(os.environ.get("LOOKOUT_PORT", "8000"))
    uvicorn.run(
        "lookout.adapters.web.app:create_app",
        factory=True,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
