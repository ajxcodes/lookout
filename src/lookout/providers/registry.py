"""Pluggable ProviderRegistry engine with auto-discovery and router mounting."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

import lookout.providers
from lookout.config.schema import LookoutConfig
from lookout.providers.base import (
    BaseProviderDriver,
    DiscoveryResult,
    ProviderManifest,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class _GenericVerifyRequest(BaseModel):
    session_id: str = Field(..., description="Active 2FA session ID")
    code: str = Field(..., description="Verification code")


class ProviderRegistry:
    """Registry engine maintaining and discovering stream provider drivers."""

    def __init__(self, auto_discover: bool = True) -> None:
        self._drivers: dict[str, BaseProviderDriver] = {}
        if auto_discover:
            self.discover_providers()

    def discover_providers(self) -> None:
        """Scan src/lookout/providers/ packages for driver implementations."""
        provider_pkg_path = lookout.providers.__path__
        for module_info in pkgutil.iter_modules(provider_pkg_path):
            name = module_info.name
            if name in ("base", "registry", "templates") or name.startswith("_"):
                continue

            try:
                mod = importlib.import_module(f"lookout.providers.{name}")
                found_driver = False

                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        inspect.isclass(attr)
                        and issubclass(attr, BaseProviderDriver)
                        and attr is not BaseProviderDriver
                    ):
                        self.register_driver(attr())
                        found_driver = True
                        break

                if not found_driver:
                    try:
                        driver_mod = importlib.import_module(f"lookout.providers.{name}.driver")
                        for attr_name in dir(driver_mod):
                            attr = getattr(driver_mod, attr_name)
                            if (
                                inspect.isclass(attr)
                                and issubclass(attr, BaseProviderDriver)
                                and attr is not BaseProviderDriver
                            ):
                                self.register_driver(attr())
                                break
                    except ImportError:
                        pass
            except Exception as exc:
                logger.warning("Failed to auto-discover provider '%s': %s", name, exc)

    def register_driver(self, driver: BaseProviderDriver) -> None:
        """Register a provider driver instance."""
        pid = driver.provider_id.lower()
        self._drivers[pid] = driver
        logger.debug("Registered provider driver: %s (%s)", pid, driver.display_name)

    def get_driver(self, provider_id: str) -> BaseProviderDriver:
        """Retrieve driver by provider id (case-insensitive)."""
        key = provider_id.lower()
        if key not in self._drivers:
            raise KeyError(f"Stream provider '{provider_id}' is not registered.")
        return self._drivers[key]

    def list_drivers(self) -> list[BaseProviderDriver]:
        """Return list of all registered driver instances."""
        return list(self._drivers.values())

    def list_manifests(self) -> list[ProviderManifest]:
        """Return UI manifests for all registered drivers."""
        manifests: list[ProviderManifest] = []
        for driver in self._drivers.values():
            try:
                manifests.append(driver.get_ui_manifest())
            except Exception as exc:
                logger.warning(
                    "Error getting manifest for provider '%s': %s", driver.provider_id, exc
                )
        return manifests

    def probe_all(self, config: LookoutConfig | None = None) -> dict[str, DiscoveryResult]:
        """Probe all registered drivers for existing credentials and bridges."""
        results: dict[str, DiscoveryResult] = {}
        for pid, driver in self._drivers.items():
            try:
                results[pid] = driver.probe_existing(config)
            except Exception as exc:
                logger.warning("Error probing provider '%s': %s", pid, exc)
                results[pid] = DiscoveryResult(
                    status="error",
                    provider=pid,
                    details=str(exc),
                )
        return results

    def sync_all(self, config: LookoutConfig, target_dir: Path) -> dict[str, list[Path]]:
        """Synchronize environment files for all registered providers."""
        target_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, list[Path]] = {}
        for pid, driver in self._drivers.items():
            try:
                paths = driver.sync_environment(config, target_dir)
                results[pid] = paths
                logger.info("Synchronized environment for provider '%s': %s", pid, paths)
            except Exception as exc:
                logger.warning("Failed to sync environment for provider '%s': %s", pid, exc)
                results[pid] = []
        return results

    def create_combined_router(self) -> APIRouter:
        """Create a combined APIRouter mounting all provider sub-routers."""
        combined = APIRouter()
        for pid, driver in self._drivers.items():
            sub = driver.get_router()
            if sub is None:
                sub = APIRouter()

                def _bind(d: BaseProviderDriver, r: APIRouter) -> None:
                    @r.get("/manifest")
                    async def _manifest() -> dict[str, Any]:
                        return d.get_ui_manifest().model_dump()

                    @r.get("/status")
                    async def _status(request: Request) -> dict[str, Any]:
                        cfg = getattr(request.app.state, "config", None)
                        return d.probe_existing(cfg).model_dump()

                    @r.post("/login")
                    async def _login(credentials: dict[str, Any]) -> dict[str, Any]:
                        return d.authenticate(credentials).model_dump()

                    @r.post("/verify")
                    async def _verify(payload: _GenericVerifyRequest) -> dict[str, Any]:
                        return d.verify_2fa(payload.session_id, payload.code).model_dump()

                    @r.post("/test")
                    async def _test(payload: dict[str, Any] | None = None) -> dict[str, Any]:
                        ep = payload.get("endpoint") if payload else None
                        return d.test_connection(ep).model_dump()

                _bind(driver, sub)

            combined.include_router(sub, prefix=f"/{pid}", tags=[f"provider-{pid}"])
        return combined

    def __iter__(self) -> Iterator[BaseProviderDriver]:
        return iter(self._drivers.values())

    def __len__(self) -> int:
        return len(self._drivers)

    # Legacy SidecarProviderRegistry compatibility aliases:
    def register_provider(self, adapter: Any) -> None:
        """Backward compatibility alias for register_driver."""
        if isinstance(adapter, BaseProviderDriver):
            self.register_driver(adapter)
        else:
            self._drivers[adapter.provider_id.lower()] = adapter

    def get_provider(self, provider_id: str) -> Any:
        """Backward compatibility alias for get_driver."""
        return self.get_driver(provider_id)

    def list_providers(self) -> list[dict[str, Any]]:
        """Backward compatibility alias returning dictionary list."""
        return [
            {
                "id": driver.provider_id,
                "name": driver.display_name,
                "display_name": driver.display_name,
                "default_ports": getattr(driver, "default_ports", {}),
            }
            for driver in self._drivers.values()
        ]
