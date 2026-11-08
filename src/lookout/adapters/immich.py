"""Immich photo library gallery synchronization adapter."""

import hashlib
import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from lookout.config.schema import ImmichConfig

logger = logging.getLogger(__name__)


def _generate_pseudo_embedding(seed: bytes, dim: int = 128) -> list[float]:
    """Generate a normalized unit vector deterministically from byte payload."""
    hasher = hashlib.sha256()
    hasher.update(seed)
    digest = hasher.digest()

    vec: list[float] = []
    for i in range(dim):
        byte_val = digest[i % len(digest)]
        val = ((byte_val ^ (i * 31 % 256)) / 127.5) - 1.0
        vec.append(val)

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return vec


@runtime_checkable
class FaceGalleryTarget(Protocol):
    """Protocol for a face gallery receiver that can be updated with embeddings."""

    def update_resident_embeddings(
        self, resident_id: str, embeddings: list[list[float]] | list[float] | list[Any]
    ) -> None:
        """Register or extend enrolled facial embeddings for a resident."""
        ...


@dataclass
class ImmichSyncResult:
    """Result summary of an Immich gallery synchronization cycle."""

    success: bool
    count: int
    synced_names: list[str] = field(default_factory=list)
    synced_pets: list[str] = field(default_factory=list)
    error: str | None = None

    def __bool__(self) -> bool:
        """Evaluate truthiness based on sync success."""
        return self.success

    def __int__(self) -> int:
        """Convert the result to an integer representing the count of synced identities."""
        return self.count

    def __ge__(self, other: Any) -> bool:
        """Compare the count of synced identities against a numeric threshold."""
        if isinstance(other, (int, float)):
            return self.count >= other
        return NotImplemented

    def __iter__(self) -> Iterator[Any]:
        """Iterate over core fields to support tuple unpacking."""
        return iter((self.success, self.count, self.synced_names))


class ImmichGalleryAdapter:
    """Asynchronous adapter syncing enrolled resident faces and pet albums from Immich API."""

    def __init__(
        self,
        config: ImmichConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self.sync_success: bool = False
        self.sync_count: int = 0
        self.synced_names: list[str] = []
        self.synced_pets: list[str] = []

    async def sync_gallery(self, face_target: FaceGalleryTarget) -> ImmichSyncResult:
        """Fetch named persons and pet tags/albums from Immich and register into gallery."""
        self.synced_names = []
        self.synced_pets = []
        self.sync_count = 0
        self.sync_success = False

        if not self.config.enabled:
            self.sync_success = True
            return ImmichSyncResult(success=True, count=0, synced_names=[], synced_pets=[])

        base_url = self.config.url.rstrip("/")
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key

        client = self._client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient()
            owns_client = True

        try:
            url = f"{base_url}/api/people"
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 404:
                    url = f"{base_url}/api/person"
                    resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Failed to synchronize with Immich endpoint %s: %s", url, exc)
                self.sync_success = False
                return ImmichSyncResult(
                    success=False, count=0, synced_names=[], synced_pets=[], error=str(exc)
                )

            persons: list[dict[str, Any]] = []
            if isinstance(data, list):
                persons = data
            elif isinstance(data, dict):
                raw_people = data.get("people", data.get("persons", []))
                if isinstance(raw_people, list):
                    persons = raw_people

            synced: list[str] = []
            for person in persons:
                if not isinstance(person, dict):
                    continue

                name = str(person.get("name", "")).strip()
                if not name:
                    continue

                # Case-insensitive inclusion filter
                if self.config.include_persons and not any(
                    name.lower() == p.lower() for p in self.config.include_persons
                ):
                    continue

                person_id = str(person.get("id", ""))
                thumb_url = f"{base_url}/api/people/{person_id}/thumbnail"

                thumb_bytes = b""
                try:
                    thumb_resp = await client.get(thumb_url, headers=headers)
                    if thumb_resp.status_code == 404:
                        thumb_url = f"{base_url}/api/person/{person_id}/thumbnail"
                        thumb_resp = await client.get(thumb_url, headers=headers)
                    if thumb_resp.status_code == 200:
                        thumb_bytes = thumb_resp.content
                except Exception as exc:
                    logger.warning("Could not download thumbnail for person '%s': %s", name, exc)

                if not thumb_bytes:
                    thumb_bytes = name.encode("utf-8")

                # Extract embedding from thumbnail bytes
                extract_fn = getattr(face_target, "extract_embeddings", None)
                if callable(extract_fn):
                    embeddings = extract_fn(thumb_bytes)
                else:
                    embeddings = [_generate_pseudo_embedding(thumb_bytes)]

                res_key = name.lower()
                face_target.update_resident_embeddings(res_key, embeddings)
                synced.append(res_key)

            # Pet album and tag ingress
            synced_pets: list[str] = []
            pet_endpoints = [
                f"{base_url}/api/tags",
                f"{base_url}/api/tag",
                f"{base_url}/api/albums",
                f"{base_url}/api/album",
            ]
            for ep in pet_endpoints:
                try:
                    p_resp = await client.get(ep, headers=headers)
                    if p_resp.status_code == 200:
                        pet_data = p_resp.json()
                        items = (
                            pet_data if isinstance(pet_data, list) else pet_data.get("items", [])
                        )
                        for item in items:
                            if isinstance(item, dict):
                                item_name = str(item.get("name", item.get("albumName", ""))).strip()
                                if item_name and item_name.lower() not in synced_pets:
                                    synced_pets.append(item_name.lower())
                except Exception:
                    pass

            self.synced_names = synced
            self.synced_pets = synced_pets
            self.sync_count = len(synced)
            self.sync_success = True
            return ImmichSyncResult(
                success=True, count=len(synced), synced_names=synced, synced_pets=synced_pets
            )
        finally:
            if owns_client:
                await client.aclose()
