"""Temporary file and memory-backed scratch space management."""

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)


class TempMediaManager:
    """Manages temporary scratch space with RAM-backed tmpfs and fallback."""

    def __init__(
        self,
        preferred_temp_dir: str | Path | None = "/dev/shm",
        fallback_temp_dir: str | Path | None = None,
    ) -> None:
        self._preferred_temp_dir = Path(preferred_temp_dir) if preferred_temp_dir else None
        self._fallback_temp_dir = (
            Path(fallback_temp_dir) if fallback_temp_dir else Path(tempfile.gettempdir())
        )
        self.base_dir = self._resolve_base_dir()

    def _resolve_base_dir(self) -> Path:
        """Resolve writable base temporary directory with tmpfs fallback."""
        if self._preferred_temp_dir and self._is_writable_dir(self._preferred_temp_dir):
            return self._preferred_temp_dir

        if not self._fallback_temp_dir.exists():
            try:
                self._fallback_temp_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return Path(tempfile.gettempdir())

        if self._is_writable_dir(self._fallback_temp_dir):
            return self._fallback_temp_dir

        return Path(tempfile.gettempdir())

    @staticmethod
    def _is_writable_dir(path: Path) -> bool:
        """Check if a path exists, is a directory, and is writable."""
        try:
            return path.is_dir() and os.access(path, os.W_OK)
        except OSError:
            return False

    def create_temp_dir(self, prefix: str = "lookout_") -> Path:
        """Create a dedicated temporary directory under base scratch space."""
        dir_path = tempfile.mkdtemp(prefix=prefix, dir=str(self.base_dir))
        return Path(dir_path)

    def create_temp_file(self, suffix: str = "", prefix: str = "lookout_") -> Path:
        """Create a temporary file under base scratch space and return its path."""
        fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=str(self.base_dir))
        os.close(fd)
        return Path(path)

    def cleanup_path(self, path: str | Path) -> bool:
        """Safely delete a file or directory tree."""
        target = Path(path)
        if not target.exists():
            return False
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            return True
        except OSError as err:
            logger.warning("Failed to remove temporary path %s: %s", target, err)
            return False

    def cleanup_directory(self, directory: str | Path, max_age_seconds: float) -> int:
        """Remove files in directory older than max_age_seconds."""
        target_dir = Path(directory)
        if not target_dir.exists() or not target_dir.is_dir():
            return 0

        now = time.time()
        removed_count = 0

        for item in list(target_dir.iterdir()):
            try:
                stat = item.stat()
                if now - stat.st_mtime > max_age_seconds:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink(missing_ok=True)
                    removed_count += 1
            except OSError as err:
                logger.warning("Error inspecting/cleaning %s: %s", item, err)

        return removed_count

    def enforce_retention_and_storage_limit(
        self,
        directory: str | Path,
        retention_days: int | None = None,
        max_storage_bytes: int | None = None,
        max_storage_gb: float | None = None,
    ) -> int:
        """Prune directory based on age retention and maximum storage limits."""
        target_dir = Path(directory)
        if not target_dir.exists() or not target_dir.is_dir():
            return 0

        removed_count = 0

        # 1. Retention days pruning
        if retention_days is not None and retention_days > 0:
            removed_count += self.cleanup_directory(
                target_dir, max_age_seconds=retention_days * 86400.0
            )

        # 2. Maximum storage quota enforcement
        quota_bytes: float | None = None
        if max_storage_bytes is not None:
            quota_bytes = float(max_storage_bytes)
        elif max_storage_gb is not None:
            quota_bytes = max_storage_gb * 1024 * 1024 * 1024

        if quota_bytes is not None and quota_bytes > 0:
            # Collect remaining files with stats
            files_with_stats: list[tuple[Path, float, int]] = []
            total_size = 0

            for root, _, files in os.walk(target_dir):
                for f in files:
                    file_path = Path(root) / f
                    try:
                        stat = file_path.stat()
                        files_with_stats.append((file_path, stat.st_mtime, stat.st_size))
                        total_size += stat.st_size
                    except OSError:
                        continue

            # If quota exceeded, delete oldest first
            if total_size > quota_bytes:
                files_with_stats.sort(key=lambda x: x[1])  # oldest mtime first
                for file_path, _, file_size in files_with_stats:
                    if total_size <= quota_bytes:
                        break
                    try:
                        file_path.unlink(missing_ok=True)
                        total_size -= file_size
                        removed_count += 1
                    except OSError as err:
                        logger.warning(
                            "Failed to delete %s during quota pruning: %s", file_path, err
                        )

        return removed_count


class TempMediaContext:
    """Context manager for temporary media scratch workspace with auto-cleanup."""

    def __init__(
        self,
        manager: TempMediaManager | None = None,
        prefix: str = "transcode_",
    ) -> None:
        self.manager = manager or TempMediaManager()
        self.prefix = prefix
        self.dir_path: Path | None = None

    def __enter__(self) -> Path:
        self.dir_path = self.manager.create_temp_dir(prefix=self.prefix)
        return self.dir_path

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.dir_path:
            self.manager.cleanup_path(self.dir_path)

    async def __aenter__(self) -> Path:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)
