"""Serialization helpers for domain entities in web API responses."""

from typing import Any

from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    InferenceProvenance,
)


def serialize_bounding_box(box: BoundingBox) -> dict[str, Any]:
    """Serialize BoundingBox dataclass to dictionary."""
    return {
        "label": box.label,
        "confidence": box.confidence,
        "x_min": box.x_min,
        "y_min": box.y_min,
        "x_max": box.x_max,
        "y_max": box.y_max,
    }


def serialize_metrics(metrics: DetectionMetrics) -> dict[str, Any]:
    """Serialize DetectionMetrics dataclass to dictionary."""
    return {
        "confidence": metrics.confidence,
        "bounding_boxes": [serialize_bounding_box(b) for b in metrics.bounding_boxes],
        "audio_decibels": metrics.audio_decibels,
        "duration_seconds": metrics.duration_seconds,
        "total_latency_ms": metrics.total_latency_ms,
    }


def serialize_provenance(prov: InferenceProvenance) -> dict[str, Any]:
    """Serialize InferenceProvenance dataclass to dictionary."""
    return {
        "provider": prov.provider,
        "model_name": prov.model_name,
        "latency_ms": prov.latency_ms,
        "confidence": prov.confidence,
        "tokens_used": prov.tokens_used,
        "raw_output": prov.raw_output,
        "timestamp": prov.timestamp.isoformat(),
    }


def serialize_event(event: CameraEvent) -> dict[str, Any]:
    """Serialize CameraEvent aggregate root to JSON-compatible dictionary."""
    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "timestamp": event.timestamp.isoformat(),
        "category": event.category.value,
        "threat_level": int(event.threat_level),
        "summary": event.summary,
        "metrics": serialize_metrics(event.metrics),
        "provenance": [serialize_provenance(p) for p in event.provenance],
        "resident_id": event.resident_id,
        "pet_id": event.pet_id,
        "media_uri": event.media_uri,
        "snapshot_uri": event.snapshot_uri,
        "metadata": event.metadata,
        "is_threat": event.is_threat(),
        "has_resident": event.has_resident(),
        "has_pet": event.has_pet(),
    }
