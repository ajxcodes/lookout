"""Unit tests for Home Assistant Container deployment, Traefik reverse proxy CSP and TLS routing."""

from pathlib import Path


def test_ha_container_iframe_and_csp_headers() -> None:
    doc_path = Path("docs/HOME_ASSISTANT_CONTAINER.md")
    assert doc_path.is_file(), "docs/HOME_ASSISTANT_CONTAINER.md missing"
    traefik_config = doc_path.read_text(encoding="utf-8")

    assert "frame-ancestors" in traefik_config and "ha.home.ajx.codes" in traefik_config


def test_traefik_routing_includes_streams_and_stream_mp4() -> None:
    doc_path = Path("docs/HOME_ASSISTANT_CONTAINER.md")
    traefik_config = doc_path.read_text(encoding="utf-8")

    assert "/api/stream.mp4" in traefik_config and "/api/streams" in traefik_config
    assert "/api/ws" in traefik_config
    assert "/api/webrtc" in traefik_config
    assert "/api/frame.jpeg" in traefik_config
