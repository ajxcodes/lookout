"""Domain orchestration service for camera event processing."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from lookout.domain.models import (
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)
from lookout.ports.egress import (
    AudioClassifierPort,
    EventRepositoryPort,
    FaceRecognitionPort,
    NotificationPort,
    VideoRecorderPort,
    VisionInferencePort,
)
from lookout.ports.ingress import EventIngressPort


class EventProcessingService(EventIngressPort):
    """Core domain orchestrator coordinating ingress, inference, evaluation, and dispatch."""

    def __init__(
        self,
        repository: EventRepositoryPort,
        notification: NotificationPort | None = None,
        video_recorder: VideoRecorderPort | None = None,
        vision_inference: VisionInferencePort | None = None,
        audio_classifier: AudioClassifierPort | None = None,
        face_recognition: FaceRecognitionPort | None = None,
        light_ml_detector: Any = None,
        illumination_filter: Any = None,
        pet_classifier: Any = None,
        zone_evaluator: Any = None,
        entity_matcher: Any = None,
        notification_threshold: ThreatLevel = ThreatLevel.LOW,
        notification_target: str = "default",
    ) -> None:
        self.repository = repository
        self.notification = notification
        self.video_recorder = video_recorder
        self.vision_inference = vision_inference
        self.audio_classifier = audio_classifier
        self.face_recognition = face_recognition
        self.light_ml_detector = light_ml_detector
        self.illumination_filter = illumination_filter
        self.pet_classifier = pet_classifier
        self.zone_evaluator = zone_evaluator
        self.entity_matcher = entity_matcher
        self.notification_threshold = notification_threshold
        self.notification_target = notification_target

    @staticmethod
    def _evaluate_threat_level(category: EventCategory) -> ThreatLevel:
        """Map event category to domain default threat level."""
        match category:
            case (
                EventCategory.VERIFIED_RESIDENT
                | EventCategory.KNOWN_PET
                | EventCategory.FALSE_POSITIVE
                | EventCategory.PET_ELIMINATION
            ):
                return ThreatLevel.INFO
            case (
                EventCategory.COURIER_DELIVERY
                | EventCategory.FOOD_DELIVERY
                | EventCategory.PACKAGE_EVENT
                | EventCategory.VEHICLE
                | EventCategory.ANIMAL_WILDLIFE
            ):
                return ThreatLevel.LOW
            case EventCategory.UNKNOWN_VISITOR:
                return ThreatLevel.MEDIUM
            case EventCategory.ACOUSTIC_ANOMALY:
                return ThreatLevel.HIGH
            case EventCategory.SECURITY_INTRUSION:
                return ThreatLevel.CRITICAL
            case _:
                return ThreatLevel.LOW

    async def ingest_raw_event(
        self, camera_id: str, payload: bytes | dict[str, Any]
    ) -> CameraEvent:
        """Process incoming raw trigger into fully evaluated and persisted CameraEvent."""
        image_bytes = b""
        audio_bytes: bytes | None = None
        metadata: dict[str, Any] = {}

        if isinstance(payload, dict):
            raw_img = payload.get("image")
            if isinstance(raw_img, bytes):
                image_bytes = raw_img
            raw_audio = payload.get("audio")
            if isinstance(raw_audio, bytes):
                audio_bytes = raw_audio
            raw_meta = payload.get("metadata")
            if isinstance(raw_meta, dict):
                metadata = raw_meta
        elif isinstance(payload, bytes):
            image_bytes = payload

        snapshot_uri: str | None = None
        media_uri: str | None = None
        if self.video_recorder is not None:
            snapshot_uri = await self.video_recorder.capture_snapshot(camera_id)
            media_uri = await self.video_recorder.record_clip(camera_id, 10.0)

        category = EventCategory.UNKNOWN_VISITOR
        metrics = DetectionMetrics()
        provenance: list[InferenceProvenance] = []
        resident_id: str | None = None
        pet_id: str | None = None
        zone_id: str | None = None
        vehicle_id: str | None = None
        matched_entities: list[str] = []

        # Stage 1: Illumination pre-filter
        is_glare = False
        if self.illumination_filter is not None and image_bytes:
            glare_res = self.illumination_filter.evaluate_frame(image_bytes)
            if getattr(glare_res, "is_glare", False):
                is_glare = True
                category = EventCategory.FALSE_POSITIVE
                reason = getattr(glare_res, "rejection_reason", "excessive glare")
                summary = f"False trigger rejected: {reason}"

        if not is_glare:
            # Stage 1: Light ML Object Detector
            if self.light_ml_detector is not None and image_bytes:
                boxes, latency_ms = self.light_ml_detector.detect_objects(image_bytes)
                metrics.bounding_boxes = boxes
                metrics.total_latency_ms = latency_ms

                # Pet elimination posture classifier
                if self.pet_classifier is not None:
                    for b in boxes:
                        if b.label in ("dog", "cat", "pet"):
                            posture_res = self.pet_classifier.classify_posture(b, image_bytes)
                            if getattr(posture_res, "is_elimination", False):
                                category = EventCategory.PET_ELIMINATION
                                metrics.posture_telemetry = getattr(posture_res, "telemetry", None)
                                break

                # Spatial zone evaluator
                if self.zone_evaluator is not None and boxes:
                    ref_box = next((b for b in boxes if b.label in ("dog", "cat", "pet")), boxes[0])
                    resolved_zone = self.zone_evaluator.resolve_zone(ref_box, camera_id=camera_id)
                    if resolved_zone is not None:
                        zone_id = getattr(resolved_zone, "id", None)
                        metrics.zone_id = zone_id

                # Entity catalog matcher
                if self.entity_matcher is not None and boxes:
                    (
                        ann_boxes,
                        m_names,
                        r_id,
                        p_id,
                        v_id,
                    ) = self.entity_matcher.match_entities_for_boxes(boxes, frame_bytes=image_bytes)
                    metrics.bounding_boxes = ann_boxes
                    matched_entities = list(m_names)
                    metrics.tracked_entities = list(m_names)
                    if r_id:
                        resident_id = r_id
                    if p_id:
                        pet_id = p_id
                    if v_id:
                        vehicle_id = v_id

            # Stage 2: Heavy VLM Cascade (if still unclassified or not resolved by Light ML)
            if (
                self.vision_inference is not None
                and image_bytes
                and category == EventCategory.UNKNOWN_VISITOR
            ):
                vis_cat, vis_metrics, vis_prov = await self.vision_inference.analyze_scene(
                    image_bytes
                )
                category = vis_cat
                if not metrics.bounding_boxes:
                    metrics = vis_metrics
                provenance.append(vis_prov)

            if self.face_recognition is not None and image_bytes and not resident_id:
                faces = await self.face_recognition.identify_faces(image_bytes)
                if faces:
                    best_face, _ = faces[0]
                    resident_id = best_face
                    category = EventCategory.VERIFIED_RESIDENT
                    if best_face not in matched_entities:
                        matched_entities.append(best_face)

            if self.audio_classifier is not None and audio_bytes:
                label, conf, audio_prov = await self.audio_classifier.classify_audio(audio_bytes)
                provenance.append(audio_prov)
                if label in ("glass_break", "alarm", "gunshot", "scream"):
                    category = EventCategory.ACOUSTIC_ANOMALY

            threat_level = self._evaluate_threat_level(category)

            # Targeted summary generation
            if category == EventCategory.PET_ELIMINATION:
                pet_name = next(
                    (b.entity_name for b in metrics.bounding_boxes if b.entity_name), None
                ) or (pet_id or "Pet")
                zone_label = "Backyard (Lawn)" if zone_id == "lawn" else (zone_id or "yard")
                summary = f"{pet_name} pooped in {zone_label}"
            elif matched_entities:
                summary = f"Detected {', '.join(matched_entities)} on camera {camera_id}"
            else:
                summary = f"Detected {category.value.replace('_', ' ')} on camera {camera_id}"
        else:
            threat_level = ThreatLevel.INFO

        event = CameraEvent(
            id=str(uuid4()),
            camera_id=camera_id,
            timestamp=datetime.now(UTC),
            category=category,
            threat_level=threat_level,
            summary=summary,
            metrics=metrics,
            provenance=provenance,
            resident_id=resident_id,
            pet_id=pet_id,
            media_uri=media_uri,
            snapshot_uri=snapshot_uri,
            metadata=metadata,
            zone_id=zone_id,
            vehicle_id=vehicle_id,
            matched_entities=matched_entities,
        )

        return await self.process_event(event)

    async def process_event(self, event: CameraEvent) -> CameraEvent:
        """Persist event to repository and dispatch notification if threshold met."""
        await self.repository.save(event)

        if self.notification is not None and event.threat_level >= self.notification_threshold:
            await self.notification.send_notification(
                event=event,
                target=self.notification_target,
                message=f"[{event.threat_level.name}] {event.summary}",
            )

        return event
