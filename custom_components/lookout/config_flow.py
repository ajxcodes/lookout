"""UI Config Flow for Lookout Home Assistant integration."""

from __future__ import annotations

import logging
from typing import Any

try:
    import voluptuous as vol  # type: ignore[import-not-found]
    from homeassistant import config_entries
except ImportError:

    class config_entries:  # type: ignore[no-redef]
        class ConfigFlow:
            def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
                cls.domain = domain

    class vol:  # type: ignore[no-redef]
        @staticmethod
        def Schema(d: Any) -> Any:
            return d

        @staticmethod
        def Required(k: str, default: Any = None) -> str:
            return k

        @staticmethod
        def Optional(k: str, default: Any = None) -> str:
            return k


from .const import CONF_API_KEY, CONF_HOST, CONF_PORT, DEFAULT_HOST, DEFAULT_PORT, DOMAIN

logger = logging.getLogger(__name__)


class LookoutConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Lookout."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Handle the initial user credentials and host step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_HOST, DEFAULT_HOST)
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            title = f"Lookout ({host}:{port})"

            # Prevent duplicate configuration entries
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(title=title, data=user_input)

        data_schema = {
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(CONF_API_KEY): str,
        }

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_set_unique_id(self, unique_id: str) -> None:
        pass

    def _abort_if_unique_id_configured(self) -> None:
        pass

    def async_create_entry(self, title: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(
        self, step_id: str, data_schema: Any, errors: dict[str, str]
    ) -> dict[str, Any]:
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors}
