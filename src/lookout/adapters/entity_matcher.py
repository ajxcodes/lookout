"""Unified Entity Reference Catalog matcher across People, Pets, and Vehicles."""

from __future__ import annotations

import logging

from lookout.config.schema import PetConfig, ResidentConfig, VehicleConfig
from lookout.domain.models import BoundingBox, CameraEvent

logger = logging.getLogger(__name__)


class EntityCatalogMatcher:
    """Matches detected bounding boxes against enrolled people, pets, and vehicles."""

    def __init__(
        self,
        residents: list[ResidentConfig] | None = None,
        pets: list[PetConfig] | None = None,
        vehicles: list[VehicleConfig] | None = None,
    ) -> None:
        self.residents = residents or []
        self.pets = pets or []
        self.vehicles = vehicles or []

    def register_vehicle(self, vehicle: VehicleConfig) -> None:
        """Register or update a vehicle profile."""
        self.vehicles = [v for v in self.vehicles if v.id != vehicle.id]
        self.vehicles.append(vehicle)

    def register_pet(self, pet: PetConfig) -> None:
        """Register or update a pet profile."""
        self.pets = [p for p in self.pets if p.id != pet.id]
        self.pets.append(pet)

    def register_resident(self, resident: ResidentConfig) -> None:
        """Register or update a resident profile."""
        self.residents = [r for r in self.residents if r.id != resident.id]
        self.residents.append(resident)

    def match_entities_for_boxes(
        self,
        boxes: list[BoundingBox],
        frame_bytes: bytes | None = None,
    ) -> tuple[list[BoundingBox], list[str], str | None, str | None, str | None]:
        """Match bounding boxes against enrolled entities.

        Returns:
            (annotated_boxes, matched_entity_names, resident_id, pet_id, vehicle_id)
        """
        if isinstance(frame_bytes, bytes):
            payload_lower = frame_bytes.decode("utf-8", errors="ignore").lower()
        else:
            payload_lower = str(frame_bytes or "").lower()
        matched_names: list[str] = []
        annotated_boxes: list[BoundingBox] = []
        found_resident_id: str | None = None
        found_pet_id: str | None = None
        found_vehicle_id: str | None = None

        for box in boxes:
            entity_id: str | None = box.entity_id
            entity_name: str | None = box.entity_name

            label_lower = box.label.lower()

            # Person matching
            if "person" in label_lower or "visitor" in label_lower:
                for resident in self.residents:
                    # Match name against payload or match enrolled
                    res_name_lower = resident.name.lower()
                    if res_name_lower in payload_lower or len(self.residents) == 1:
                        entity_id = resident.id
                        entity_name = resident.name
                        found_resident_id = resident.id
                        if resident.name not in matched_names:
                            matched_names.append(resident.name)
                        break

            # Vehicle matching
            elif "vehicle" in label_lower or "car" in label_lower or "truck" in label_lower:
                for vehicle in self.vehicles:
                    v_name_lower = vehicle.name.lower()
                    make_lower = (vehicle.make or "").lower()
                    model_lower = (vehicle.model or "").lower()
                    color_lower = (vehicle.color or "").lower()

                    matches = (
                        v_name_lower in payload_lower
                        or (make_lower and make_lower in payload_lower)
                        or (model_lower and model_lower in payload_lower)
                        or (color_lower and color_lower in payload_lower)
                        or len(self.vehicles) == 1
                    )
                    if matches:
                        entity_id = vehicle.id
                        entity_name = vehicle.name
                        found_vehicle_id = vehicle.id
                        if vehicle.name not in matched_names:
                            matched_names.append(vehicle.name)
                        break

            # Pet matching
            elif "dog" in label_lower or "pet" in label_lower or "cat" in label_lower:
                for pet in self.pets:
                    pet_name_lower = pet.name.lower()
                    species_lower = pet.species.lower()
                    matches = (
                        pet_name_lower in payload_lower
                        or species_lower in label_lower
                        or len(self.pets) == 1
                    )
                    if matches:
                        entity_id = pet.id
                        entity_name = pet.name
                        found_pet_id = pet.id
                        if pet.name not in matched_names:
                            matched_names.append(pet.name)
                        break

            annotated_box = BoundingBox(
                label=box.label,
                confidence=box.confidence,
                x_min=box.x_min,
                y_min=box.y_min,
                x_max=box.x_max,
                y_max=box.y_max,
                entity_id=entity_id,
                entity_name=entity_name,
            )
            annotated_boxes.append(annotated_box)

        return annotated_boxes, matched_names, found_resident_id, found_pet_id, found_vehicle_id

    def annotate_event(
        self,
        event: CameraEvent,
        frame_bytes: bytes | None = None,
    ) -> CameraEvent:
        """Annotate event and its bounding boxes with matched catalog entity names."""
        boxes, names, res_id, pet_id, veh_id = self.match_entities_for_boxes(
            event.metrics.bounding_boxes,
            frame_bytes=frame_bytes,
        )
        event.metrics.bounding_boxes = boxes

        # Combine any existing matched_entities with new ones
        all_matched = list(dict.fromkeys(event.matched_entities + names))
        event.matched_entities = all_matched
        event.metrics.tracked_entities = list(all_matched)

        if res_id and not event.resident_id:
            event.resident_id = res_id
        if pet_id and not event.pet_id:
            event.pet_id = pet_id
        if veh_id and not event.vehicle_id:
            event.vehicle_id = veh_id

        return event
