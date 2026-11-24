"""Real-code-path tests for native Ring ingestion."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

from lookout.adapters.native_stream_manager import MediaCaptureError
from lookout.adapters.ring_native import (
    RingAuthError,
    RingNativeAdapter,
    RingStreamSourceError,
    decode_ring_token,
    encode_ring_token,
)
from lookout.adapters.ring_native import (
    _parse_datetime as _ring_parse_datetime,
)
from lookout.config.schema import CameraConfig, RingConfig
from lookout.domain.models import EventCategory
from lookout.ports.egress import VideoRecorderPort

JPEG = b"\xff\xd8\xff\xe0" + b"ring-jpeg-payload" * 64
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x00\x00\x08mdat" + b"\x00" * 256

INNER_TOKEN = {"rt": "refresh-token/with+chars", "hid": "hardware-id-1", "pnc": "pnc"}
RAW_TOKEN = encode_ring_token(INNER_TOKEN)


class FakeStreamManager:
    def __init__(
        self,
        frame_bytes: bytes = JPEG,
        clip_bytes: bytes = MP4,
        frame_error: Exception | None = None,
        clip_error: Exception | None = None,
    ) -> None:
        self.frame_bytes = frame_bytes
        self.clip_bytes = clip_bytes
        self.frame_error = frame_error
        self.clip_error = clip_error
        self.ensured: list[tuple[str, str | None]] = []
        self.frame_calls: list[str] = []
        self.removed: list[str] = []
        self.streams: dict[str, str] = {}

    async def ensure_stream(self, camera_id: str, source: str | None = None) -> None:
        self.ensured.append((camera_id, source))
        if source is not None:
            self.streams[camera_id] = source

    async def remove_stream(self, camera_id: str) -> None:
        self.removed.append(camera_id)
        self.streams.pop(camera_id, None)

    async def frame(self, camera_id: str) -> bytes:
        self.frame_calls.append(camera_id)
        if self.frame_error is not None:
            raise self.frame_error
        return self.frame_bytes

    async def clip(self, camera_id: str, duration_seconds: float) -> bytes:
        if self.clip_error is not None:
            raise self.clip_error
        return self.clip_bytes


class FakeRingDevice:
    device_api_id = 410964110
    device_id = "649a637fdc48"
    name = "Front Door"
    model = "doorbell_v4"
    kind = "doorbell"
    family = "doorbots"
    connection_status = "online"

    def __init__(self, history: list[dict[str, Any]] | None = None) -> None:
        self.history = history or []
        self.history_calls: list[dict[str, Any]] = []

    async def async_history(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.history_calls.append(kwargs)
        return list(self.history)


class FakeRingClient:
    def __init__(self, devices: list[FakeRingDevice], fail: bool = False) -> None:
        self.device_list = devices
        self.fail = fail

    def devices(self) -> Any:
        if self.fail:
            raise RuntimeError("ring auth failed")
        return SimpleNamespace(video_devices=list(self.device_list))

    async def async_update_devices(self) -> None:
        if self.fail:
            raise RuntimeError("ring auth failed")


def _camera(camera_id: str = "front_door") -> CameraConfig:
    return CameraConfig(
        id=camera_id,
        name="Front Door",
        stream_url=(
            "ring:?camera_id=410964110&device_id=649a637fdc48"
            f"&refresh_token={quote(RAW_TOKEN, safe='')}"
        ),
        native_provider="ring",
        native_device_id="410964110",
    )


def test_ring_adapter_implements_video_recorder_port() -> None:
    assert isinstance(RingNativeAdapter(config=RingConfig()), VideoRecorderPort)


def test_token_roundtrip() -> None:
    assert decode_ring_token(RAW_TOKEN) == INNER_TOKEN
    with pytest.raises(RingAuthError):
        decode_ring_token("not-base64")


def test_build_stream_source_uses_full_encoded_token() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))
    device = {
        "device_api_id": 410964110,
        "device_id": "649a637fdc48",
        "name": "Front Door",
    }

    source = adapter.build_stream_source(device)
    snapshot = adapter.build_stream_source(device, snapshot=True)

    assert source == (
        "ring:?camera_id=410964110&device_id=649a637fdc48"
        f"&refresh_token={quote(RAW_TOKEN, safe='')}"
    )
    assert snapshot == source + "&snapshot"


def test_build_stream_source_requires_device_fields() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))
    with pytest.raises(RingStreamSourceError):
        adapter.build_stream_source({"device_api_id": 1})


async def test_capture_snapshot_uses_live_bridge_stream_and_writes_jpeg(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Snapshots must come from the H.264 bridge, never a second ring: session."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager()
    # A stale stream from an older build must be removed so it stops retrying 406.
    manager.streams["front_door_snapshot"] = "ring:?camera_id=410964110&snapshot"
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    path = await adapter.capture_snapshot("front_door")

    assert path.endswith(".jpg")
    assert Path(path).read_bytes() == JPEG
    assert manager.removed == ["front_door_snapshot"]
    assert "front_door_snapshot" not in manager.streams
    assert manager.ensured[0][0] == "front_door"
    source = manager.ensured[0][1] or ""
    assert source.startswith("exec:node ")
    assert "ring_bridge/index.mjs" in source
    assert "--camera-id 410964110" in source
    assert "&snapshot" not in source
    assert manager.frame_calls == ["front_door"]


async def test_capture_snapshot_failure_writes_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager(frame_error=MediaCaptureError("empty frame"))
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    with pytest.raises(MediaCaptureError):
        await adapter.capture_snapshot("front_door")
    assert list(tmp_path.iterdir()) == []


async def test_record_clip_writes_mp4(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager()
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    path = await adapter.record_clip("front_door", 5.0)

    assert path.endswith(".mp4")
    data = Path(path).read_bytes()
    assert data == MP4
    assert b"ftyp" in data[:64]
    assert not manager.ensured[0][1].endswith("&snapshot")


async def test_record_clip_failure_writes_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager(clip_error=MediaCaptureError("no mp4"))
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    with pytest.raises(MediaCaptureError):
        await adapter.record_clip("front_door", 5.0)
    assert list(tmp_path.iterdir()) == []


async def test_discover_and_test_connection_use_real_device_list() -> None:
    fake = FakeRingClient([FakeRingDevice()])
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN), client=fake)

    devices = await adapter.discover_devices()
    result = await adapter.test_connection()

    assert devices[0]["id"] == "410964110"
    assert devices[0]["device_id"] == "649a637fdc48"
    assert devices[0]["stream_source"].startswith("ring:?camera_id=410964110")
    assert result["reachable"] is True
    assert len(result["devices"]) == 1


async def test_connection_reports_failure_instead_of_claiming_reachable() -> None:
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([], fail=True),
    )

    result = await adapter.test_connection()

    assert result["reachable"] is False
    assert "ring auth failed" in (result["error"] or "")


async def test_poll_events_maps_real_history_fields() -> None:
    created = datetime.now(UTC) - timedelta(minutes=1)
    device = FakeRingDevice(
        history=[
            {
                "id": 987654,
                "created_at": created,
                "kind": "motion",
                "answered": True,
                "cv_properties": {"detection_type": "human"},
                "recording": {"status": "ready"},
                "snapshot_url": "",
            }
        ]
    )
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([device]),
        cameras=[_camera()],
    )

    payloads = await adapter.poll_events("front_door", since=created - timedelta(seconds=5))

    assert payloads[0]["event_id"] == "987654"
    assert payloads[0]["camera_id"] == "front_door"
    assert payloads[0]["kind"] == "motion"
    assert payloads[0]["detection_type"] == "human"
    assert payloads[0]["answered"] is True
    assert payloads[0]["timestamp"] == created.isoformat()
    assert device.history_calls == [{"limit": 50}]


async def test_poll_events_filters_by_since() -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    device = FakeRingDevice(
        history=[{"id": 1, "created_at": old, "kind": "motion", "cv_properties": {}}]
    )
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([device]),
        cameras=[_camera()],
    )

    assert await adapter.poll_events("front_door", since=datetime.now(UTC)) == []


async def test_ingest_event_requires_real_payload() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))

    with pytest.raises(ValueError):
        await adapter.ingest_event(None)
    with pytest.raises(ValueError):
        await adapter.ingest_event({})


async def test_ingest_event_builds_camera_event_from_payload() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))
    payload = {
        "event_id": "987654",
        "camera_id": "front_door",
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": "motion",
        "detection_type": "human",
        "metadata": {"provider": "ring"},
    }

    event = await adapter.ingest_event(payload)

    assert event.id == "ring-987654"
    assert event.camera_id == "front_door"
    assert event.category == EventCategory.UNKNOWN_VISITOR
    assert event.metadata["provider"] == "ring"


def test_parse_datetime_handles_epoch_millis() -> None:
    result = _ring_parse_datetime(1700000000000)
    assert result == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


async def test_ring_auth_uses_compatible_user_agent(tmp_path: Any, monkeypatch: Any) -> None:
    """TR-8: Auth receives a device-compatible UA, never lookout/<version>."""
    import ring_doorbell

    captured: dict[str, Any] = {}

    class CapturingAuth:
        def __init__(self, user_agent: str, **kwargs: Any) -> None:
            captured["user_agent"] = user_agent
            self.token = {"refresh_token": "refreshed"}
            self.hardware_id = None

        async def async_refresh_tokens(self) -> None:
            return None

        async def async_close(self) -> None:
            return None

    class CapturingRing:
        def __init__(self, auth: Any) -> None:
            return None

        async def async_update_devices(self) -> None:
            return None

        def devices(self) -> Any:
            return SimpleNamespace(video_devices=[])

    monkeypatch.setattr(ring_doorbell, "Auth", CapturingAuth)
    monkeypatch.setattr(ring_doorbell, "Ring", CapturingRing)
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN), runtime_dir=str(tmp_path)
    )
    await adapter._ring()

    assert captured["user_agent"].startswith("android:com.ringapp")
    assert "lookout/" not in captured["user_agent"]


async def test_ring_client_skips_legacy_session_bootstrap(tmp_path: Any, monkeypatch: Any) -> None:
    """_ring() seeds a session sentinel so the 406 session POST is never issued."""
    import ring_doorbell

    captured: dict[str, Any] = {}

    class CapturingAuth:
        def __init__(self, user_agent: str, **kwargs: Any) -> None:
            captured["user_agent"] = user_agent
            self.token = {"refresh_token": "refreshed"}
            self.hardware_id = None

        async def async_refresh_tokens(self) -> None:
            return None

        async def async_close(self) -> None:
            return None

    class CapturingRing:
        def __init__(self, auth: Any) -> None:
            self.session: dict[str, Any] | None = None
            self.session_calls = 0
            captured["ring"] = self

        async def async_create_session(self) -> None:
            self.session_calls += 1
            raise AssertionError("legacy clients_api/session bootstrap must be skipped")

        async def async_update_devices(self) -> None:
            assert self.session is not None

        def devices(self) -> Any:
            return SimpleNamespace(video_devices=[])

    monkeypatch.setattr(ring_doorbell, "Auth", CapturingAuth)
    monkeypatch.setattr(ring_doorbell, "Ring", CapturingRing)

    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN), runtime_dir=str(tmp_path)
    )
    await adapter._ring()

    ring = captured["ring"]
    assert ring.session_calls == 0
    assert ring.session == {"clients_api_session": "skipped"}
    assert captured["user_agent"].startswith("android:com.ringapp")


async def test_authz_denied_rotates_client_profile_and_succeeds(tmp_path: Any) -> None:
    """TR-9: a 406 ext_authz_denied rotates profiles and the retry ingests events."""
    created = datetime.now(UTC) - timedelta(minutes=1)

    class FlakyDevice(FakeRingDevice):
        def __init__(self) -> None:
            super().__init__()
            self.history_calls = 0

        async def async_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.history_calls += 1
            if self.history_calls == 1:
                raise RuntimeError(
                    "HTTP error with status code 406 during query of url "
                    "https://api.ring.com/clients_api/session: ext_authz_denied "
                    "refresh_token=SECRET"
                )
            return [
                {
                    "id": 555001,
                    "created_at": created,
                    "kind": "motion",
                    "cv_properties": {"detection_type": "human"},
                }
            ]

    device = FlakyDevice()
    client = FakeRingClient([device])
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=client,
        cameras=[_camera()],
        runtime_dir=str(tmp_path),
    )
    initial_profile = adapter._profile_state.current().name

    payloads = await adapter.poll_events("front_door")

    assert len(payloads) == 1
    assert device.history_calls == 2
    assert adapter._profile_state.current().name != initial_profile
    assert adapter.last_success_at is not None
    assert adapter.consecutive_failures == 0
    assert "SECRET" not in (adapter._last_error or "")


async def test_authz_denied_exhaustion_raises_actionable_error(tmp_path: Any) -> None:
    """TR-10: exhausting every profile raises an actionable, redacted auth error."""
    from lookout.adapters.ring_api_compat import DEFAULT_PROFILES

    class AlwaysDeniedDevice(FakeRingDevice):
        async def async_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError(
                "HTTP error with status code 406 during query of url "
                "https://api.ring.com/clients_api/session: ext_authz_denied "
                "refresh_token=SECRET"
            )

    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([AlwaysDeniedDevice()]),
        cameras=[_camera()],
        runtime_dir=str(tmp_path),
    )

    with pytest.raises(RingAuthError, match="ring_authz_denied") as excinfo:
        await adapter.poll_events("front_door")

    message = str(excinfo.value)
    # TR-10: the message must state both the profile and attempt counts so a
    # single extra (non-authz) attempt cannot be misreported as profile count.
    assert f"{len(DEFAULT_PROFILES)} of {len(DEFAULT_PROFILES)} profile(s)" in message
    assert f"after {len(DEFAULT_PROFILES)} attempt(s)" in message
    assert "SECRET" not in message
    assert "SECRET" not in (adapter._last_error or "")
    assert adapter.consecutive_failures == len(DEFAULT_PROFILES)


async def test_authz_exhaustion_message_accurate_with_single_profile(tmp_path: Any) -> None:
    """The exhaustion message must stay accurate with a single client profile.

    Regression for the PR #97 note: with one profile the old wording said
    "all client profiles after 1 attempts", hiding the profile count and
    misstating the attempt count. The message now pins both counts.
    """
    from lookout.adapters.ring_api_compat import RingClientProfile, RingProfileState

    class AlwaysDeniedDevice(FakeRingDevice):
        async def async_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError(
                "HTTP error with status code 406 during query of url "
                "https://api.ring.com/clients_api/session: ext_authz_denied "
                "refresh_token=SECRET"
            )

    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([AlwaysDeniedDevice()]),
        cameras=[_camera()],
        runtime_dir=str(tmp_path),
    )
    # Force exactly one available profile; rotation is a no-op with one profile.
    adapter._profile_state = RingProfileState(
        (RingClientProfile("single", "android:com.ringapp:6.62.0"),)
    )

    with pytest.raises(RingAuthError, match="ring_authz_denied") as excinfo:
        await adapter.poll_events("front_door")

    message = str(excinfo.value)
    assert "1 of 1 profile(s)" in message
    assert "after 1 attempt(s)" in message
    assert "SECRET" not in message


async def test_poll_events_reraises_non_authz_error_without_success_state(
    tmp_path: Any,
) -> None:
    """A persistent non-authz error must surface, not fall through to success.

    Regression for the missing ``raise`` after the retry branch: when every
    attempt fails with a non-authz exception, ``poll_events`` must propagate it
    and leave the success counters / last payloads untouched.
    """

    class AlwaysFailingDevice(FakeRingDevice):
        async def async_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("upstream database connection lost")

    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([AlwaysFailingDevice()]),
        cameras=[_camera()],
        runtime_dir=str(tmp_path),
    )
    # Seed a prior successful poll to prove the failing poll does not overwrite it.
    prior_success = datetime.now(UTC) - timedelta(days=1)
    adapter._last_success = prior_success
    adapter._last_event_count = 999
    adapter._last_error = None
    adapter.consecutive_failures = 0

    with pytest.raises(RuntimeError, match="upstream database connection lost"):
        await adapter.poll_events("front_door")

    # Two attempts: the first retries, the second re-raises. No success state
    # or stale payloads are produced on either path.
    assert adapter.consecutive_failures == 2
    assert adapter.last_success_at == prior_success
    assert adapter._last_event_count == 999
    assert "upstream database connection lost" in (adapter._last_error or "")


async def test_ring_ding_doorbell_event_capture() -> None:
    """TR-9: Verify Ring doorbell ding event is captured and notified."""
    from lookout.adapters.memory.notification import MockNotificationAdapter
    from lookout.adapters.memory.repository import InMemoryEventRepository
    from lookout.domain.models import EventCategory
    from lookout.domain.service import EventProcessingService

    repo = InMemoryEventRepository()
    notifier = MockNotificationAdapter()
    service = EventProcessingService(repository=repo, notification=notifier)

    event = await service.ingest_raw_event(
        "front_door",
        {"metadata": {"kind": "ding", "trigger": "ding"}},
    )
    assert event.category == EventCategory.UNKNOWN_VISITOR
    assert "ding" in event.summary.lower()
    assert len(notifier.sent_notifications) == 1
