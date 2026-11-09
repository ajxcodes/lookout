"""Storage service: telemetry reporting, quota monitoring, and FIFO looping retention daemon."""

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.config.schema import StorageConfig
from lookout.domain.models import CameraEvent, EvictionResult, StorageStatus
from lookout.ports.egress import MediaArchivalPort, StorageServicePort

logger = logging.getLogger(__name__)


class NoOpMediaArchivalAdapter(MediaArchivalPort):
    """Default pass-through implementation of MediaArchivalPort."""

    async def on_pre_evict(self, event: CameraEvent, file_paths: list[Path]) -> None:
        """No-op pre-eviction hook."""
        return None


class StorageService(StorageServicePort):
    """Storage telemetry, volume capacity monitoring, and two-tier FIFO looping retention daemon."""

    def __init__(
        self,
        config: StorageConfig,
        repository: Any,
        audit_logger: AuditLogger | None = None,
        archival_adapter: MediaArchivalPort | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.audit_logger = audit_logger
        self.archival_adapter = archival_adapter or NoOpMediaArchivalAdapter()
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False

    def get_storage_status(self) -> StorageStatus:
        """Calculate and return formatted storage metrics, quota limits, and dual-tier telemetry."""
        base_path = Path(self.config.base_path)

        # 1. Local Tier directory breakdown and total
        media_breakdown: dict[str, int] = {"recordings": 0, "snapshots": 0, "hls": 0}
        media_total = 0
        if base_path.exists():
            for sub in ("recordings", "snapshots", "hls"):
                sub_dir = base_path / sub
                if sub_dir.is_dir():
                    size = sum(f.stat().st_size for f in sub_dir.rglob("*") if f.is_file())
                    media_breakdown[sub] = size
                    media_total += size
                elif sub_dir.is_file():
                    size = sub_dir.stat().st_size
                    media_breakdown[sub] = size
                    media_total += size

        # 2. Local volume capacity
        vol_total = 0
        vol_used = 0
        vol_avail = 0
        vol_used_pct = 0.0
        try:
            stat_target = base_path if base_path.exists() else base_path.parent
            if stat_target.exists():
                disk = shutil.disk_usage(str(stat_target))
                vol_total = disk.total
                vol_used = disk.used
                vol_avail = disk.free
                vol_used_pct = round((disk.used / disk.total) * 100.0, 2) if disk.total > 0 else 0.0
        except Exception:
            pass

        max_storage_bytes = int(self.config.max_storage_gb * (1024**3))
        if vol_total > 0 and self.config.max_storage_percent:
            pct_bytes = int(vol_total * (self.config.max_storage_percent / 100.0))
            if pct_bytes < max_storage_bytes:
                max_storage_bytes = pct_bytes

        media_quota_used_pct = (
            round((media_total / max_storage_bytes) * 100.0, 2) if max_storage_bytes > 0 else 0.0
        )
        is_warning = (
            media_quota_used_pct >= self.config.warning_threshold_percent
            or vol_used_pct >= self.config.warning_threshold_percent
        )

        # 3. Archive Tier telemetry
        arch_enabled = bool(self.config.archive_enabled)
        arch_path_str = self.config.archive_path
        arch_available = False
        arch_vol_total = 0
        arch_vol_used = 0
        arch_vol_avail = 0
        arch_vol_used_pct = 0.0
        arch_media_total = 0
        arch_breakdown: dict[str, int] = {"recordings": 0, "snapshots": 0}
        arch_max_storage_bytes = 0
        arch_quota_used_pct = 0.0
        arch_is_warning = False

        if arch_enabled and arch_path_str:
            arch_p = Path(arch_path_str)
            if arch_p.exists():
                arch_available = os.access(str(arch_p), os.W_OK)
                try:
                    arch_disk = shutil.disk_usage(str(arch_p))
                    arch_vol_total = arch_disk.total
                    arch_vol_used = arch_disk.used
                    arch_vol_avail = arch_disk.free
                    arch_vol_used_pct = (
                        round((arch_disk.used / arch_disk.total) * 100.0, 2)
                        if arch_disk.total > 0
                        else 0.0
                    )
                except Exception:
                    pass

                for sub in ("recordings", "snapshots"):
                    s_dir = arch_p / sub
                    if s_dir.is_dir():
                        size = sum(f.stat().st_size for f in s_dir.rglob("*") if f.is_file())
                        arch_breakdown[sub] = size
                        arch_media_total += size
                    elif s_dir.is_file():
                        size = s_dir.stat().st_size
                        arch_breakdown[sub] = size
                        arch_media_total += size

                if self.config.archive_max_storage_gb is not None:
                    arch_max_storage_bytes = int(self.config.archive_max_storage_gb * (1024**3))
                elif self.config.archive_max_storage_percent is not None and arch_vol_total > 0:
                    arch_max_storage_bytes = int(
                        arch_vol_total * (self.config.archive_max_storage_percent / 100.0)
                    )

                if (
                    self.config.archive_max_storage_percent is not None
                    and arch_vol_total > 0
                    and arch_max_storage_bytes > 0
                ):
                    pct_bytes = int(
                        arch_vol_total * (self.config.archive_max_storage_percent / 100.0)
                    )
                    if pct_bytes < arch_max_storage_bytes:
                        arch_max_storage_bytes = pct_bytes

                arch_quota_used_pct = (
                    round((arch_media_total / arch_max_storage_bytes) * 100.0, 2)
                    if arch_max_storage_bytes > 0
                    else 0.0
                )
                arch_is_warning = (
                    arch_quota_used_pct >= self.config.warning_threshold_percent
                    if arch_max_storage_bytes > 0
                    else False
                ) or (
                    arch_vol_used_pct >= (self.config.archive_max_storage_percent or 90.0)
                    if arch_vol_total > 0
                    else False
                )

        # 4. Runway estimation
        remaining = max_storage_bytes - media_total
        if remaining <= 0:
            runway = 0.0
        else:
            rate = max(media_total / 24.0, 50 * 1024 * 1024)
            runway = round(remaining / rate, 1)

        return StorageStatus(
            volume_total_bytes=vol_total,
            volume_used_bytes=vol_used,
            volume_available_bytes=vol_avail,
            volume_used_percent=vol_used_pct,
            media_total_bytes=media_total,
            media_breakdown=media_breakdown,
            max_storage_bytes=max_storage_bytes,
            max_storage_gb=self.config.max_storage_gb,
            max_storage_percent=self.config.max_storage_percent,
            media_quota_used_percent=media_quota_used_pct,
            warning_threshold_percent=self.config.warning_threshold_percent,
            is_warning=is_warning,
            retention_mode=self.config.retention_mode,
            preserve_bookmarked=self.config.preserve_bookmarked,
            cleanup_interval_seconds=self.config.cleanup_interval_seconds,
            retention_runway_hours=runway,
            archive_enabled=arch_enabled,
            archive_path=arch_path_str,
            archive_available=arch_available,
            archive_volume_total_bytes=arch_vol_total,
            archive_volume_used_bytes=arch_vol_used,
            archive_volume_available_bytes=arch_vol_avail,
            archive_volume_used_percent=arch_vol_used_pct,
            archive_media_total_bytes=arch_media_total,
            archive_media_breakdown=arch_breakdown,
            archive_max_storage_bytes=arch_max_storage_bytes,
            archive_max_storage_gb=self.config.archive_max_storage_gb,
            archive_max_storage_percent=self.config.archive_max_storage_percent,
            archive_quota_used_percent=arch_quota_used_pct,
            archive_is_warning=arch_is_warning,
        )

    async def execute_fifo_eviction(self, target_bytes: int | None = None) -> EvictionResult:
        """Execute two-tier FIFO eviction down to safe buffer across local and archive storage."""
        status = self.get_storage_status()

        files_migrated = 0
        bytes_migrated = 0
        files_deleted = 0
        bytes_freed = 0
        events_pruned = 0

        # --- Stage 1: Hot Tier (Local) Overflow Handling ---
        needed_to_reclaim = 0
        if target_bytes is not None:
            needed_to_reclaim = target_bytes
        else:
            effective_local_quota = status.max_storage_bytes
            if (
                status.media_total_bytes > effective_local_quota
                or status.volume_used_percent > self.config.max_storage_percent
            ):
                target_local = int(
                    effective_local_quota * (self.config.low_watermark_percent / 100.0)
                )
                needed_to_reclaim = max(0, status.media_total_bytes - target_local)

        reclaimed_local = 0
        if needed_to_reclaim > 0:
            candidates: list[dict[str, Any]] = []
            if hasattr(self.repository, "get_eviction_candidates"):
                candidates = await self.repository.get_eviction_candidates(
                    limit=200,
                    preserve_bookmarked=self.config.preserve_bookmarked,
                    retention_mode=self.config.retention_mode,
                    tier="local",
                )

            loc_base = Path(self.config.base_path).resolve()
            arch_base = (
                Path(self.config.archive_path).resolve()
                if (self.config.archive_enabled and self.config.archive_path)
                else None
            )

            for cand in candidates:
                if reclaimed_local >= needed_to_reclaim:
                    break

                event: CameraEvent = cand.get("event", cand)
                # Find physical files for this event
                files_found: list[Path] = []
                for p_str in (event.media_uri, event.snapshot_uri):
                    if p_str:
                        raw = p_str[7:] if p_str.startswith("file://") else p_str
                        p = Path(raw)
                        if p.exists() and p.is_file():
                            files_found.append(p)

                if self.config.archive_enabled and arch_base and status.archive_available:
                    # Migrate to warm archive tier
                    event_moved_bytes = 0
                    new_media_uri = None
                    new_snap_uri = None

                    for p_str, is_snap in [(event.media_uri, False), (event.snapshot_uri, True)]:
                        if not p_str:
                            continue
                        raw = p_str[7:] if p_str.startswith("file://") else p_str
                        p = Path(raw)
                        if p.exists() and p.is_file():
                            try:
                                rel = p.resolve().relative_to(loc_base)
                            except ValueError:
                                sub = "snapshots" if is_snap else "recordings"
                                rel = Path(sub) / p.name
                            dest = arch_base / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            f_size = p.stat().st_size
                            shutil.move(str(p), str(dest))
                            event_moved_bytes += f_size
                            files_migrated += 1
                            bytes_migrated += f_size
                            if is_snap:
                                new_snap_uri = str(dest)
                            else:
                                new_media_uri = str(dest)

                    if not new_media_uri and event.media_uri:
                        try:
                            rel = Path(event.media_uri).relative_to(loc_base)
                            new_media_uri = str(arch_base / rel)
                        except Exception:
                            new_media_uri = str(
                                arch_base / "recordings" / Path(event.media_uri).name
                            )

                    if hasattr(self.repository, "migrate_event_media"):
                        await self.repository.migrate_event_media(
                            event_id=event.id,
                            new_media_uri=new_media_uri,
                            new_snapshot_uri=new_snap_uri,
                            new_tier="archive",
                        )
                    reclaimed_local += event_moved_bytes
                    events_pruned += 1
                else:
                    # Permanent eviction: call archival extension hook and unlink
                    await self.archival_adapter.on_pre_evict(event, files_found)
                    event_freed = 0
                    for f in files_found:
                        f_size = f.stat().st_size
                        f.unlink(missing_ok=True)
                        event_freed += f_size
                        files_deleted += 1
                        bytes_freed += f_size

                    if hasattr(self.repository, "nullify_media_references"):
                        await self.repository.nullify_media_references(
                            event.id,
                            tombstone_metadata={"media_purged": True, "purged_from": "local"},
                        )
                    reclaimed_local += event_freed
                    events_pruned += 1

        # --- Stage 2: Archive Tier Quota Enforcement & Remote Full Handling ---
        if self.config.archive_enabled and self.config.archive_path:
            arch_quota = status.archive_max_storage_bytes
            current_archive_bytes = status.archive_media_total_bytes + bytes_migrated
            if arch_quota > 0 and (
                current_archive_bytes > arch_quota
                or status.archive_volume_used_percent
                > (self.config.archive_max_storage_percent or 90.0)
            ):
                target_archive = int(arch_quota * (self.config.low_watermark_percent / 100.0))
                arch_candidates: list[dict[str, Any]] = []
                if hasattr(self.repository, "get_eviction_candidates"):
                    arch_candidates = await self.repository.get_eviction_candidates(
                        limit=200,
                        preserve_bookmarked=self.config.preserve_bookmarked,
                        retention_mode=self.config.retention_mode,
                        tier="archive",
                    )

                for cand in arch_candidates:
                    if current_archive_bytes <= target_archive:
                        break

                    event = cand.get("event", cand)
                    files_to_purge: list[Path] = []
                    for p_str in (event.media_uri, event.snapshot_uri):
                        if p_str:
                            raw = p_str[7:] if p_str.startswith("file://") else p_str
                            p = Path(raw)
                            if p.exists() and p.is_file():
                                files_to_purge.append(p)

                    await self.archival_adapter.on_pre_evict(event, files_to_purge)
                    event_freed = 0
                    for f in files_to_purge:
                        f_size = f.stat().st_size
                        f.unlink(missing_ok=True)
                        event_freed += f_size
                        files_deleted += 1
                        bytes_freed += f_size

                    if hasattr(self.repository, "nullify_media_references"):
                        await self.repository.nullify_media_references(
                            event.id,
                            tombstone_metadata={"media_purged": True, "purged_from": "archive"},
                        )
                    current_archive_bytes -= event_freed
                    events_pruned += 1

        # Audit logging
        if files_migrated > 0 and self.audit_logger:
            await self.audit_logger.log(
                action="storage_fifo_migration",
                entity_type="storage",
                details={"files_migrated": files_migrated, "bytes_migrated": bytes_migrated},
            )
        if files_deleted > 0 and self.audit_logger:
            await self.audit_logger.log(
                action="storage_fifo_purge",
                entity_type="storage",
                details={
                    "files_deleted": files_deleted,
                    "bytes_freed": bytes_freed,
                    "events_pruned": events_pruned,
                },
            )

        return EvictionResult(
            files_migrated=files_migrated,
            bytes_migrated=bytes_migrated,
            files_deleted=files_deleted,
            bytes_freed=bytes_freed,
            events_pruned=events_pruned,
        )

    async def _run_loop(self) -> None:
        """Periodic background audit loop checking quotas and running eviction."""
        while self._running:
            try:
                await self.execute_fifo_eviction()
            except Exception as exc:
                logger.warning("Error during periodic storage eviction: %s", exc)
            try:
                await asyncio.sleep(self.config.cleanup_interval_seconds)
            except asyncio.CancelledError:
                break

    def start(self) -> None:
        """Start the background audit loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background audit loop gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
