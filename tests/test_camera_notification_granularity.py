"""Tests for per-camera notification granularity (TR-2 through TR-7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from lookout.adapters.memory.notification import MockNotificationAdapter
from lookout.adapters.memory.repository import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, ThreatLevel
from lookout.domain.models import CameraEvent, EventCategory
from lookout.domain.service import EventProcessingService


def test_camera_config_notification_granularity_defaults() -> None:
    """TR-2: Verify CameraConfig notification granularity defaults."""
    cam = CameraConfig(id="cam1", name="Cam 1", stream_url="rtsp://localhost/c1")
    assert cam.notifications_enabled is True
    assert cam.notifications_muted is False
    assert cam.notification_min_threat is None
    assert cam.notification_event_types == ["motion", "sound", "person", "ding"]
    assert cam.notify_pass1 is True
    assert cam.notify_pass2 is True


def test_should_notify_suppresses_muted_and_disabled_cameras() -> None:
    """TR-3: Verify should_notify suppresses muted and disabled cameras."""
    cfg = LookoutConfig(
        cameras=[
            CameraConfig(
                id="muted_cam",
                name="Muted",
                stream_url="rtsp://localhost/muted",
                notifications_enabled=False,
            ),
            CameraConfig(
                id="disabled_cam",
                name="Disabled",
                stream_url="rtsp://localhost/disabled",
                enabled=False,
            ),
        ]
    )
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        notification=MockNotificationAdapter(),
        config=cfg,
    )
    event_muted = CameraEvent(
        id="e1",
        camera_id="muted_cam",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.HIGH,
        summary="Motion on muted_cam",
    )
    event_disabled = CameraEvent(
        id="e2",
        camera_id="disabled_cam",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.HIGH,
        summary="Motion on disabled_cam",
    )
    assert service.should_notify(event_muted, pass_num=1) is False
    assert service.should_notify(event_disabled, pass_num=1) is False


def test_should_notify_respects_per_camera_min_threat_level() -> None:
    """TR-4: Verify should_notify respects per-camera min threat level."""
    cfg = LookoutConfig(
        cameras=[
            CameraConfig(
                id="porch",
                name="Porch",
                stream_url="rtsp://localhost/porch",
                notification_min_threat=ThreatLevel.HIGH,
            )
        ]
    )
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        notification=MockNotificationAdapter(),
        config=cfg,
    )
    event_low = CameraEvent(
        id="e1",
        camera_id="porch",
        timestamp=datetime.now(UTC),
        category=EventCategory.PACKAGE_EVENT,
        threat_level=ThreatLevel.LOW,
        summary="Package on porch",
    )
    event_high = CameraEvent(
        id="e2",
        camera_id="porch",
        timestamp=datetime.now(UTC),
        category=EventCategory.ACOUSTIC_ANOMALY,
        threat_level=ThreatLevel.HIGH,
        summary="Alarm on porch",
    )
    assert service.should_notify(event_low, pass_num=1) is False
    assert service.should_notify(event_high, pass_num=1) is True


def test_should_notify_filters_event_types() -> None:
    """TR-5: Verify should_notify filters event types."""
    cfg = LookoutConfig(
        cameras=[
            CameraConfig(
                id="nursery",
                name="Nursery",
                stream_url="rtsp://localhost/nursery",
                notification_event_types=["sound", "ding"],
            )
        ]
    )
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        notification=MockNotificationAdapter(),
        config=cfg,
    )
    motion_event = CameraEvent(
        id="e1",
        camera_id="nursery",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Motion on nursery",
        metadata={"trigger": "motion"},
    )
    sound_event = CameraEvent(
        id="e2",
        camera_id="nursery",
        timestamp=datetime.now(UTC),
        category=EventCategory.ACOUSTIC_ANOMALY,
        threat_level=ThreatLevel.HIGH,
        summary="Sound on nursery",
        metadata={"alarm_type": "Sound"},
    )
    ding_event = CameraEvent(
        id="e3",
        camera_id="nursery",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Doorbell ding on nursery",
        metadata={"kind": "ding"},
    )
    assert service.should_notify(motion_event, pass_num=1) is False
    assert service.should_notify(sound_event, pass_num=1) is True
    assert service.should_notify(ding_event, pass_num=1) is True


def test_should_notify_pass1_and_pass2_delivery_behavior() -> None:
    """TR-6: Verify Pass 1 and Pass 2 delivery flags."""
    cfg = LookoutConfig(
        cameras=[
            CameraConfig(
                id="cam_p2",
                name="Pass2Only",
                stream_url="rtsp://localhost/p2",
                notify_pass1=False,
                notify_pass2=True,
            ),
            CameraConfig(
                id="cam_p1",
                name="Pass1Only",
                stream_url="rtsp://localhost/p1",
                notify_pass1=True,
                notify_pass2=False,
            ),
        ]
    )
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        notification=MockNotificationAdapter(),
        config=cfg,
    )
    event_pass2_only = CameraEvent(
        id="e1",
        camera_id="cam_p2",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Motion on cam_p2",
    )
    event_pass1_only = CameraEvent(
        id="e2",
        camera_id="cam_p1",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Motion on cam_p1",
    )
    assert service.should_notify(event_pass2_only, pass_num=1) is False
    assert service.should_notify(event_pass2_only, pass_num=2) is True
    assert service.should_notify(event_pass1_only, pass_num=1) is True
    assert service.should_notify(event_pass1_only, pass_num=2) is False


def test_patch_camera_settings_persists_notification_granularity(tmp_path: Path) -> None:
    """TR-7: Verify PATCH /api/cameras/{id}/settings persists notification granularity."""
    cfg_path = tmp_path / "lookout.yaml"
    cfg = LookoutConfig(
        cameras=[CameraConfig(id="porch", name="Porch", stream_url="rtsp://localhost/porch")]
    )
    cfg_path.write_text("system_name: Lookout\n", encoding="utf-8")
    app = create_app(config=cfg, config_path=str(cfg_path))
    with TestClient(app) as client:
        res = client.patch(
            "/api/cameras/porch/settings",
            json={
                "notifications_enabled": True,
                "notification_min_threat": "HIGH",
                "notification_event_types": ["sound", "ding"],
                "notify_pass1": False,
                "notify_pass2": True,
            },
        )
        assert res.status_code == 200
        cam_res = client.get("/api/cameras")
        assert cam_res.status_code == 200
        cdata = cam_res.json()["cameras"][0]
        assert cdata["notification_min_threat"] == "HIGH"
    updated_cam = cfg.cameras[0]
    assert updated_cam.notification_min_threat == ThreatLevel.HIGH
    assert updated_cam.notification_event_types == ["sound", "ding"]
    assert updated_cam.notify_pass1 is False
