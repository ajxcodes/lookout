"""Unit tests for Unified Entity Reference Catalog matcher and visual feature resolution."""

from datetime import UTC, datetime

from lookout.adapters.entity_matcher import EntityCatalogMatcher
from lookout.config.schema import PetConfig, ResidentConfig, VehicleConfig
from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    ThreatLevel,
)


def test_standalone_entity_catalog_crud_vehicles_pets_people() -> None:
    """TR-18: Verify entity catalog supports CRUD for vehicles, pets, and people."""
    matcher = EntityCatalogMatcher()

    vehicle = VehicleConfig(
        id="veh-honda",
        name="Silver Honda Civic",
        make="Honda",
        model="Civic",
        color="silver",
        license_plate="7XYZ123",
    )
    pet = PetConfig(
        id="pet-mochi",
        name="Mochi",
        species="dog",
        breed="Corgi",
    )
    resident = ResidentConfig(
        id="res-alex",
        name="Alex",
    )

    matcher.register_vehicle(vehicle)
    matcher.register_pet(pet)
    matcher.register_resident(resident)

    vehicles = matcher.vehicles
    pets = matcher.pets
    residents = matcher.residents

    assert len(vehicles) == 1 and vehicles[0].name == "Silver Honda Civic"
    assert len(pets) == 1 and pets[0].name == "Mochi"
    assert len(residents) == 1 and residents[0].name == "Alex"


def test_entity_visual_feature_matching_replaces_generic_labels() -> None:
    """TR-19: Verify matching replaces generic 'car' or 'dog' labels with enrolled catalog names."""
    matcher = EntityCatalogMatcher(
        vehicles=[
            VehicleConfig(
                id="veh-1",
                name="Silver Honda Civic",
                make="Honda",
                model="Civic",
                color="silver",
            )
        ],
        pets=[
            PetConfig(
                id="pet-1",
                name="Mochi",
                species="dog",
            )
        ],
    )

    event = CameraEvent(
        id="evt-multi-entity",
        camera_id="cam-driveway",
        timestamp=datetime.now(UTC),
        category=EventCategory.VEHICLE,
        threat_level=ThreatLevel.LOW,
        summary="Detected car and dog in driveway",
        metrics=DetectionMetrics(
            bounding_boxes=[
                BoundingBox(
                    label="car", confidence=0.91, x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6
                ),
                BoundingBox(
                    label="dog", confidence=0.88, x_min=0.6, y_min=0.6, x_max=0.8, y_max=0.9
                ),
            ]
        ),
    )

    annotated_event = matcher.annotate_event(
        event,
        frame_bytes=b"Silver Honda Civic car parked in driveway while Mochi dog plays",
    )

    assert "Silver Honda Civic" in annotated_event.matched_entities
    assert "Mochi" in annotated_event.matched_entities
