"""Unit tests for Immich gallery synchronization adapter."""

import httpx
import pytest
import respx
from httpx import Response

from lookout.adapters.face_recognition import FaceRecognitionAdapter
from lookout.adapters.immich import ImmichGalleryAdapter, ImmichSyncResult
from lookout.config.schema import ImmichConfig


@pytest.mark.asyncio
@respx.mock
async def test_immich_gallery_sync_named_persons() -> None:
    """TR-23: Sync named persons and face crops from Immich REST endpoint."""
    respx.get("http://localhost:2283/api/person").mock(
        return_value=Response(
            200,
            json=[
                {"id": "person-1", "name": "Alice"},
                {"id": "person-2", "name": "Bob"},
            ],
        )
    )
    respx.get("http://localhost:2283/api/person/person-1/thumbnail").mock(
        return_value=Response(200, content=b"alice-crop-bytes")
    )
    respx.get("http://localhost:2283/api/person/person-2/thumbnail").mock(
        return_value=Response(200, content=b"bob-crop-bytes")
    )

    face_adapter = FaceRecognitionAdapter()
    adapter = ImmichGalleryAdapter(ImmichConfig(url="http://localhost:2283"))
    result = await adapter.sync_gallery(face_adapter)
    sync_count = result.count

    assert sync_count >= 1
    assert "alice" in face_adapter.gallery
    assert "bob" in face_adapter.gallery


@pytest.mark.asyncio
@respx.mock
async def test_immich_gallery_sync_filter_include_persons() -> None:
    """TR-24: Filter synced persons against configured include_persons list."""
    respx.get("http://localhost:2283/api/person").mock(
        return_value=Response(
            200,
            json=[
                {"id": "person-1", "name": "Alice"},
                {"id": "person-2", "name": "Charlie"},
            ],
        )
    )
    respx.get("http://localhost:2283/api/person/person-1/thumbnail").mock(
        return_value=Response(200, content=b"alice-crop-bytes")
    )

    face_adapter = FaceRecognitionAdapter()
    adapter = ImmichGalleryAdapter(
        ImmichConfig(url="http://localhost:2283", include_persons=["Alice"])
    )
    result = await adapter.sync_gallery(face_adapter)
    synced_names = result.synced_names

    assert "alice" in synced_names
    assert "charlie" not in synced_names
    assert "charlie" not in face_adapter.gallery


@pytest.mark.asyncio
@respx.mock
async def test_immich_gallery_sync_network_failure_fallback() -> None:
    """TR-25: Network failure during sync is caught and gracefully reported as failure."""
    respx.get("http://localhost:2283/api/person").mock(
        side_effect=httpx.ConnectError("Connection to Immich host refused")
    )

    face_adapter = FaceRecognitionAdapter()
    adapter = ImmichGalleryAdapter(ImmichConfig(url="http://localhost:2283"))
    result = await adapter.sync_gallery(face_adapter)
    sync_success = result.success

    assert sync_success is False
    assert adapter.sync_success is False
    assert adapter.sync_count == 0


@pytest.mark.asyncio
async def test_immich_gallery_sync_disabled_noop() -> None:
    """Verify disabled Immich adapter performs no sync operations."""
    face_adapter = FaceRecognitionAdapter()
    adapter = ImmichGalleryAdapter(ImmichConfig(enabled=False))
    result = await adapter.sync_gallery(face_adapter)

    assert result.success is True
    assert result.count == 0


def test_immich_sync_result_magic_methods_and_docstrings() -> None:
    """Verify ImmichSyncResult magic methods and presence of descriptive docstrings."""
    # Ensure docstrings exist on class and methods
    assert ImmichSyncResult.__doc__ is not None
    assert ImmichSyncResult.__bool__.__doc__ is not None
    assert ImmichSyncResult.__int__.__doc__ is not None
    assert ImmichSyncResult.__ge__.__doc__ is not None
    assert ImmichSyncResult.__iter__.__doc__ is not None

    success_result = ImmichSyncResult(
        success=True, count=3, synced_names=["alice", "bob", "charlie"]
    )
    failure_result = ImmichSyncResult(success=False, count=0, error="Network error")

    # __bool__
    assert bool(success_result) is True
    assert bool(failure_result) is False

    # __int__
    assert int(success_result) == 3
    assert int(failure_result) == 0

    # __ge__
    assert success_result >= 3
    assert success_result >= 2.5
    assert not (success_result >= 4)
    assert (success_result.__ge__("not-a-number")) is NotImplemented

    # __iter__
    success, count, names = success_result
    assert success is True
    assert count == 3
    assert names == ["alice", "bob", "charlie"]
