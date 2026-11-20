"""Home Assistant synchronization manager (MQTT discovery + REST cleanup)."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from lookout.config.schema import LookoutConfig

_paho_mod: Any = None
try:
    import paho as _paho_mod
except Exception:  # pragma: no cover - optional
    _paho_mod = None

_paho_client_module: Any = None
try:
    import paho.mqtt.client as _paho_client_module
except Exception:  # pragma: no cover - optional
    _paho_client_module = None

paho = _paho_mod

logger = logging.getLogger(__name__)


class _Status(dict[str, Any]):
    """Dict-like status object supporting attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mask_none(v: Any) -> Any:
    return v


class HomeAssistantSyncManager:
    """Best-effort synchronizer publishing Lookout cameras to Home Assistant."""

    def __init__(
        self,
        config: Any = None,
        *,
        base_url: Any = None,
        access_token: Any = None,
        token: Any = None,
        api_key: Any = None,
        auth_token: Any = None,
        ingress_url: Any = None,
        cameras: Any = None,
        storage_base_path: Any = None,
        base_path: Any = None,
        data_dir: Any = None,
        data_path: Any = None,
        mqtt_client: Any = None,
        mqtt: Any = None,
        paho_client: Any = None,
        client: Any = None,
        http_client: Any = None,
        async_client: Any = None,
        ha_client: Any = None,
        **kwargs: Any,
    ) -> None:
        # Resolve config
        self.config = config if isinstance(config, LookoutConfig) else kwargs.get("config")
        if self.config is None and isinstance(kwargs.get("lookout_config"), LookoutConfig):
            self.config = kwargs.get("lookout_config")

        # Base URL
        resolved_base = (
            base_url
            or kwargs.get("base_url")
            or kwargs.get("url")
            or kwargs.get("ha_url")
            or kwargs.get("home_assistant_url")
            or kwargs.get("endpoint")
            or kwargs.get("destination")
        )
        if resolved_base is None and self.config is not None:
            ha_cfg = getattr(self.config, "home_assistant", None)
            if ha_cfg is not None:
                resolved_base = getattr(ha_cfg, "base_url", None)
            if resolved_base is None:
                resolved_base = getattr(self.config, "home_assistant_url", None)
        if not resolved_base:
            resolved_base = "http://localhost:8123"
        self.base_url = str(resolved_base).rstrip("/")

        # Access token
        resolved_token = (
            access_token
            or token
            or api_key
            or auth_token
            or kwargs.get("access_token")
            or kwargs.get("token")
            or kwargs.get("api_key")
            or kwargs.get("auth_token")
        )
        if resolved_token is None and self.config is not None:
            ha_cfg = getattr(self.config, "home_assistant", None)
            if ha_cfg is not None:
                resolved_token = getattr(ha_cfg, "access_token", None)
        self.access_token = resolved_token

        # Ingress URL
        resolved_ingress = ingress_url or kwargs.get("ingress_url") or kwargs.get("ingress")
        if resolved_ingress is None and self.config is not None:
            ha_cfg = getattr(self.config, "home_assistant", None)
            if ha_cfg is not None:
                resolved_ingress = getattr(ha_cfg, "ingress_url", None)
        if not resolved_ingress and self.config is not None:
            resolved_ingress = getattr(self.config, "home_assistant_url", None)
        if not resolved_ingress:
            resolved_ingress = self.base_url
        self.ingress_url = str(resolved_ingress).rstrip("/")

        # MQTT / broker settings
        self.mqtt_host = (
            kwargs.get("mqtt_host")
            or (
                getattr(self.config.home_assistant, "mqtt_host", None)
                if (
                    self.config is not None
                    and getattr(self.config, "home_assistant", None) is not None
                )
                else None
            )
            or (
                getattr(self.config, "mqtt", None).host  # type: ignore[union-attr]
                if (self.config is not None and getattr(self.config, "mqtt", None) is not None)
                else None
            )
            or "127.0.0.1"
        )
        mqtt_port = (
            kwargs.get("mqtt_port")
            or (
                getattr(self.config.home_assistant, "mqtt_port", None)
                if (
                    self.config is not None
                    and getattr(self.config, "home_assistant", None) is not None
                )
                else None
            )
            or (
                getattr(self.config, "mqtt", None).port  # type: ignore[union-attr]
                if (self.config is not None and getattr(self.config, "mqtt", None) is not None)
                else None
            )
        )
        self.mqtt_port = int(mqtt_port) if mqtt_port else 1883
        self.mqtt_username = (
            kwargs.get("mqtt_username")
            or (
                getattr(self.config.home_assistant, "mqtt_username", None)
                if (
                    self.config is not None
                    and getattr(self.config, "home_assistant", None) is not None
                )
                else None
            )
            or (
                getattr(self.config, "mqtt", None).username  # type: ignore[union-attr]
                if (self.config is not None and getattr(self.config, "mqtt", None) is not None)
                else None
            )
        )
        self.mqtt_password = (
            kwargs.get("mqtt_password")
            or (
                getattr(self.config.home_assistant, "mqtt_password", None)
                if (
                    self.config is not None
                    and getattr(self.config, "home_assistant", None) is not None
                )
                else None
            )
            or (
                getattr(self.config, "mqtt", None).password  # type: ignore[union-attr]
                if (self.config is not None and getattr(self.config, "mqtt", None) is not None)
                else None
            )
        )

        # Storage base path
        resolved_storage = (
            storage_base_path
            or base_path
            or data_dir
            or data_path
            or kwargs.get("storage_base_path")
            or kwargs.get("base_path")
            or kwargs.get("data_dir")
            or kwargs.get("data_path")
        )
        if (
            resolved_storage is None
            and self.config is not None
            and getattr(self.config, "storage", None) is not None
        ):
            resolved_storage = getattr(self.config.storage, "base_path", None)
        if not resolved_storage:
            self.storage_base_path = None
            self.state_path = None
        else:
            self.storage_base_path = str(resolved_storage)
            self.state_path = Path(self.storage_base_path) / "ha_sync_state.json"

        # Cameras
        if cameras is None:
            cameras = kwargs.get("cameras")
        if cameras is None and self.config is not None:
            cameras = getattr(self.config, "cameras", None)
        self.cameras = list(cameras or [])

        # MQTT client
        injected_mqtt = (
            mqtt_client
            or mqtt
            or paho_client
            or kwargs.get("mqtt_client")
            or kwargs.get("mqtt")
            or kwargs.get("paho_client")
        )
        # If a generic client with publish was passed
        generic_client = client
        if (
            injected_mqtt is None
            and generic_client is not None
            and hasattr(generic_client, "publish")
            and not hasattr(generic_client, "get")
        ):
            injected_mqtt = generic_client
        self.mqtt_client = injected_mqtt
        self._mqtt_client = self.mqtt_client
        self.mqtt = self.mqtt_client

        # HTTP client
        injected_http = (
            http_client
            or async_client
            or ha_client
            or kwargs.get("http_client")
            or kwargs.get("async_client")
            or kwargs.get("ha_client")
        )
        if injected_http is None and generic_client is not None and hasattr(generic_client, "get"):
            injected_http = generic_client
        # If client was mqtt, don't reuse as http
        if (
            generic_client is not None
            and hasattr(generic_client, "publish")
            and hasattr(generic_client, "get")
        ):
            # ambiguous; prefer http if access_token? keep http
            pass
        self.http_client = injected_http
        self._http_client = self.http_client
        self.client = self.http_client
        self.async_client = self.http_client

        # State bookkeeping
        self._published_topics: dict[str, list[str]] = {}
        self._published_timestamps: dict[str, str] = {}
        self._last_sync: str | None = None
        self._last_error: str | None = None
        self._load_state()

    def _load_state(self) -> None:
        try:
            if self.state_path is None:
                return
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._published_topics = {
                        str(k): list(v) for k, v in (data.get("topics") or {}).items()
                    }
                    self._published_timestamps = {
                        str(k): str(v) for k, v in (data.get("timestamps") or {}).items()
                    }
                    self._last_sync = data.get("last_sync")
                    self._last_error = data.get("last_error")
        except Exception as exc:
            logger.warning("Failed to load HA sync state: %s", exc)

    def _save_state(self) -> None:
        try:
            if self.state_path is None:
                return
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "topics": self._published_topics,
                "timestamps": self._published_timestamps,
                "last_sync": self._last_sync,
                "last_error": self._last_error,
            }
            self.state_path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save HA sync state: %s", exc)

    def _get_mqtt_client(self) -> Any:
        for attr in ("mqtt_client", "_mqtt_client", "mqtt", "_mqtt", "paho_client", "_paho_client"):
            val = getattr(self, attr, None)
            if val is not None:
                return val
        # lazy create via paho
        client = self._create_paho_client()
        if client is not None:
            self.mqtt_client = client
            self._mqtt_client = client
            self.mqtt = client
        return client

    def _create_paho_client(self) -> Any:
        client_cls = None
        paho_mod = globals().get("paho")
        if paho_mod is not None:
            try:
                client_mod = getattr(paho_mod, "mqtt", paho_mod).client
                client_cls = getattr(client_mod, "Client", None)
            except Exception:
                client_cls = None
        if client_cls is None:
            client_cls = getattr(_paho_client_module, "Client", None)
        if client_cls is None:
            return None
        try:
            callback_api = None
            client_module = globals().get("_paho_client_module")
            if client_module is not None:
                callback_api = getattr(
                    getattr(client_module, "CallbackAPIVersion", None), "VERSION2", None
                )
            if callback_api is not None:
                try:
                    client = client_cls(callback_api_version=callback_api)
                except TypeError:
                    client = client_cls()
            else:
                client = client_cls()
        except Exception as exc:
            logger.warning("Failed to create MQTT client: %s", exc)
            return None
        # configure credentials
        try:
            if self.mqtt_username is not None:
                setter = getattr(client, "username_pw_set", None)
                if callable(setter):
                    setter(self.mqtt_username, self.mqtt_password)
        except Exception:
            pass
        # connect
        try:
            connector = getattr(client, "connect", None)
            if callable(connector):
                connector(self.mqtt_host, self.mqtt_port, 60)
        except Exception as exc:
            logger.warning("MQTT connect failed: %s", exc)
        # loop start (best effort)
        try:
            starter = getattr(client, "loop_start", None)
            if callable(starter):
                starter()
        except Exception:
            pass
        return client

    def _get_http_client(self) -> Any:
        for attr in (
            "http_client",
            "_http_client",
            "ha_client",
            "_ha_client",
            "async_client",
            "_async_client",
            "client",
            "_client",
        ):
            val = getattr(self, attr, None)
            if val is not None and hasattr(val, "get"):
                return val
        # create owned
        try:
            owned = httpx.AsyncClient()
            self.http_client = owned
            self._http_client = owned
            self.client = owned
            self.async_client = owned
            self._owned_http = owned
            return owned
        except Exception:
            return None

    async def aclose(self) -> None:
        # close http client if owned
        for attr in ("_owned_http",):
            client = getattr(self, attr, None)
            if client is not None:
                closer = getattr(client, "aclose", None) or getattr(client, "close", None)
                if callable(closer):
                    try:
                        res = closer()
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
        # stop mqtt loop
        # best effort
        for attr in ("mqtt_client", "_mqtt_client", "mqtt", "_mqtt"):
            c = getattr(self, attr, None)
            if c is not None:
                for meth in ("loop_stop", "disconnect"):
                    fn = getattr(c, meth, None)
                    if callable(fn):
                        try:
                            r = fn()
                            if inspect.isawaitable(r):
                                await r
                        except Exception:
                            pass
        self._save_state()

    def close(self) -> None:
        try:
            loop = None
            import asyncio

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                loop.create_task(self.aclose())
            else:
                asyncio.run(self.aclose())
        except Exception:
            pass

    def _unique_id(self, camera: Any) -> str:
        uid = getattr(camera, "ha_unique_id", None)
        if uid:
            return str(uid)
        return str(getattr(camera, "id", "camera"))

    def _entity_name(self, camera: Any) -> str:
        return str(
            getattr(camera, "ha_entity_name", None)
            or getattr(camera, "name", None)
            or getattr(camera, "id", "camera")
        )

    def _config_url(self) -> str:
        return f"{self.ingress_url}/cameras/{''}".rstrip("/")  # placeholder guard

    # We'll build per camera below.

    def _camera_config_url(self, camera: Any) -> str:
        cam_id = getattr(camera, "id", "")
        return f"{self.ingress_url}/cameras/{cam_id}/live"

    def _event_url(self, event_id: Any = None) -> str:
        eid = event_id if event_id is not None else "{event_id}"
        return f"{self.ingress_url}/events/{eid}"

    def _device(self, camera: Any) -> dict[str, Any]:
        return {
            "identifiers": [f"lookout_{getattr(camera, 'id', '')}"],
            "name": getattr(camera, "name", None) or getattr(camera, "id", ""),
            "configuration_url": self._camera_config_url(camera),
        }

    def _publish_sync(self, topic: str, payload: Any, retain: bool = True) -> None:
        # helper not used; async version below
        pass

    async def _publish(self, topic: str, payload: Any, retain: bool = True) -> None:
        mqtt = self._get_mqtt_client()
        if mqtt is None:
            raise RuntimeError("MQTT client unavailable")
        data = payload
        if not isinstance(payload, (str, bytes, bytearray)):
            data = json.dumps(payload)
        publisher = getattr(mqtt, "publish", None)
        if not callable(publisher):
            raise RuntimeError("MQTT client has no publish method")
        try:
            result = publisher(topic, data, retain=retain)
        except TypeError:
            result = publisher(topic, data)
        if inspect.isawaitable(result):
            await result

    async def test_connection(self) -> dict[str, Any]:
        client = self._get_http_client()
        url = f"{self.base_url}/api/"
        headers: dict[str, str] = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            if client is not None:
                getter = getattr(client, "get", None)
                if callable(getter):
                    resp = getter(url, headers=headers)
                    if inspect.isawaitable(resp):
                        resp = await resp
                else:
                    raise RuntimeError("HTTP client has no get method")
            else:
                raise RuntimeError("HTTP client unavailable")
            status = getattr(resp, "status_code", None)
            ok = status is not None and 200 <= int(status) < 300
            result = {"ok": bool(ok), "status": status}
            if not ok:
                result["error"] = f"HTTP {status}"
            self._last_error = None if ok else str(result.get("error"))
            if ok:
                self._last_sync = _now_iso()
            self._save_state()
            return result
        except Exception as exc:
            err = str(exc)
            self._last_error = err
            self._save_state()
            logger.warning("HA test_connection failed: %s", err)
            return {"ok": False, "status": None, "error": err}

    async def sync_camera(self, camera: Any) -> dict[str, Any]:
        topics: list[str] = []
        try:
            if not getattr(camera, "ha_enabled", True):
                # treat disabled as removal
                return await self.remove_camera(getattr(camera, "id", None))
            uid = self._unique_id(camera)
            cam_topic = f"homeassistant/camera/{uid}/config"
            topics.append(cam_topic)
            await self._publish(
                cam_topic,
                {
                    "name": self._entity_name(camera),
                    "unique_id": uid,
                    "device_class": getattr(camera, "ha_device_class", None),
                    "icon": getattr(camera, "ha_icon", None),
                    "device": self._device(camera),
                    "configuration_url": self._camera_config_url(camera),
                },
                retain=True,
            )

            # binary sensor (motion)
            motion_uid = f"{uid}_motion"
            motion_topic = f"homeassistant/binary_sensor/{motion_uid}/config"
            topics.append(motion_topic)
            await self._publish(
                motion_topic,
                {
                    "name": f"{self._entity_name(camera)} Motion",
                    "unique_id": motion_uid,
                    "device_class": "motion",
                    "device": self._device(camera),
                },
                retain=True,
            )

            # sensors
            for comp_suffix, label in (("threat_level", "Threat Level"), ("latency", "Latency")):
                s_uid = f"{uid}_{comp_suffix}"
                s_topic = f"homeassistant/sensor/{s_uid}/config"
                topics.append(s_topic)
                await self._publish(
                    s_topic,
                    {
                        "name": f"{self._entity_name(camera)} {label}",
                        "unique_id": s_uid,
                        "device": self._device(camera),
                    },
                    retain=True,
                )

            # event entity
            event_uid = f"{uid}_event"
            event_topic = f"homeassistant/event/{event_uid}/config"
            topics.append(event_topic)
            await self._publish(
                event_topic,
                {
                    "name": f"{self._entity_name(camera)} Events",
                    "unique_id": event_uid,
                    "event_types": list(getattr(camera, "ha_event_types", None) or []),
                    "device": self._device(camera),
                    "link": self._event_url(),
                    "url": self._event_url(),
                },
                retain=True,
            )

            cam_id = str(getattr(camera, "id", ""))
            self._published_topics[cam_id] = topics
            self._published_timestamps[cam_id] = _now_iso()
            self._last_sync = _now_iso()
            self._last_error = None
            self._save_state()
            return {
                "ok": True,
                "camera_id": cam_id,
                "topics": topics,
                "published_at": self._published_timestamps[cam_id],
            }
        except Exception as exc:
            err = str(exc)
            self._last_error = err
            self._save_state()
            logger.warning("HA sync_camera failed: %s", err)
            return {
                "ok": False,
                "error": err,
                "last_error": err,
                "camera_id": getattr(camera, "id", None),
                "topics": topics,
            }

    async def remove_camera(self, camera_id: Any) -> dict[str, Any]:
        cid = str(camera_id) if camera_id is not None else ""
        topics = list(self._published_topics.get(cid, []))
        # If no tracked topics, best-effort derive
        if not topics:
            topics = [f"homeassistant/camera/{cid}/config"]
        try:
            for topic in topics:
                await self._publish(topic, "", retain=True)
            # remove bookkeeping
            self._published_topics.pop(cid, None)
            self._published_timestamps.pop(cid, None)
            self._last_sync = _now_iso()
            self._last_error = None
            self._save_state()
            return {"ok": True, "camera_id": cid, "cleared": topics}
        except Exception as exc:
            err = str(exc)
            self._last_error = err
            self._save_state()
            logger.warning("HA remove_camera failed: %s", err)
            return {"ok": False, "error": err, "last_error": err, "camera_id": cid}

    async def sync_all(self) -> dict[str, Any]:
        # reconcile orphans
        configured_ids = {str(getattr(c, "id", "")) for c in self.cameras}
        orphans = [cid for cid in list(self._published_topics.keys()) if cid not in configured_ids]
        results: list[dict[str, Any]] = []
        for cid in orphans:
            results.append(await self.remove_camera(cid))
        for camera in self.cameras:
            if not getattr(camera, "ha_enabled", True):
                continue
            results.append(await self.sync_camera(camera))
        self._last_sync = _now_iso()
        self._save_state()
        return {
            "ok": all(r.get("ok", True) for r in results) if results else True,
            "results": results,
        }

    def get_status(self) -> _Status:
        status = _Status()
        status["ok"] = self._last_error is None
        status["last_sync"] = self._last_sync
        status["last_error"] = self._last_error
        status["cameras"] = dict(self._published_timestamps)
        status["published_topics"] = dict(self._published_topics)
        # per-camera published timestamps also top-level
        for cid, ts in self._published_timestamps.items():
            status[cid] = ts
        return status

    # Compatibility aliases
    def status(self) -> _Status:
        return self.get_status()


# Convenience export
__all__ = ["HomeAssistantSyncManager"]
