# Home Assistant Integration

Lookout publishes cameras to Home Assistant (HA) through MQTT auto-discovery and can use the HA REST API for optional cleanup/reconciliation. The goal is **zero-config onboarding**: in the common Traefik-proxied / HA-ingress deployment you do not need to enter any credential to get entities into HA.

## Zero-config auto-detection

Lookout derives its HA connection settings from configuration that already exists:

| Setting | Auto-detection order |
| --- | --- |
| HA Base URL | explicit `home_assistant.base_url` (when set to something other than the default `http://localhost:8123`) → top-level `home_assistant_url` → `http://localhost:8123` |
| MQTT broker host/port | explicit `home_assistant.mqtt_host` / `home_assistant.mqtt_port` → top-level `mqtt.host` / `mqtt.port` |
| MQTT credentials | explicit `home_assistant.mqtt_username` / `home_assistant.mqtt_password` → top-level `mqtt.username` / `mqtt.password` |
| Ingress URL | explicit `home_assistant.ingress_url` → top-level `home_assistant_url` → detected base URL |

The effective values are returned by `GET /api/home-assistant/config` so the settings screen can prefill them. This means a typical deployment only needs:

```yaml
home_assistant_url: "https://ha.example.com"   # also used for notification links
mqtt:
  enabled: true
  host: "mqtt.example.com"
  port: 1883
  username: "lookout"
  password: "********"

home_assistant:
  enabled: true
  # base_url / mqtt_* are auto-detected — no need to repeat them here
```

You can still override any of the detected values explicitly under `home_assistant:`:

```yaml
home_assistant:
  enabled: true
  base_url: "http://homeassistant:8123"
  ingress_url: "https://ha.example.com/api/hassio_ingress/lookout"
  mqtt_host: "mqtt.example.com"
  mqtt_port: 1883
  mqtt_username: "lookout"
  mqtt_password: "********"
  access_token: "long-lived-access-token"   # optional; see modes below
```

Per-camera settings (enabled, entity name, unique id, device class, icon, event granularity) are available on each camera's settings page and are saved with the existing camera-settings PATCH.

## Operating modes

`GET /api/home-assistant/status` reports a `mode` field:

- **`mqtt-only`** — no HA access token (or trusted proxy credential) is configured. Lookout publishes and clears MQTT discovery topics only; it makes **no** HA REST call. `POST /api/home-assistant/test` returns an actionable success message (`"MQTT-only mode — no HA credentials configured"`) instead of an HTTP 401. This is the default and recommended mode for the common deployment.
- **`rest`** — a long-lived access token is configured under `home_assistant.access_token`, or a proxy/Remote-User credential is supplied to the sync manager. In this mode `test_connection()` performs a real `GET /api/` health check against the HA REST API and REST-based cleanup/reconciliation is available.

The access token and MQTT password live in a collapsed **Advanced (access token)** disclosure in the UI, closed by default. Masked values are never returned unmasked, and submitting a masked or empty secret does not overwrite the stored value.

## Proxy / Remote-User mode

When Lookout is embedded in HA (ingress) behind a reverse proxy such as Traefik, HA authenticates the browser session and forwards the identity as `Remote-User` / `Remote-*` headers. In that topology the browser-facing side is already authenticated by HA, so no per-user Lookout credential is needed for deep links. If the deployment also supplies a trusted proxy credential to the sync manager (e.g. `remote_user` / `use_proxy_auth`), the manager runs in `rest` mode and can call the HA REST API without embedding a long-lived token in `config.yaml`. Otherwise the safe `mqtt-only` fallback applies.

## MQTT broker

Lookout publishes retained discovery topics under `homeassistant/<component>/<unique_id>/config` for `camera`, `binary_sensor` (motion), `sensor` (threat level, latency) and `event` entities, grouped under one HA device per camera. Removing or disabling a camera clears those retained topics (and, in `rest` mode, cleans up via the REST API). Put the broker settings in the existing `mqtt:` block and Lookout will reuse them automatically.

## Ingress deep links

Camera entities carry a `configuration_url` pointing at the Lookout ingress live view for that camera; event entities carry `link` / `url` attributes pointing at the corresponding Lookout event page. Inside HA these open in the Lookout ingress panel within the HA chrome. Set `home_assistant.ingress_url` (or `home_assistant_url`) to the HA ingress base URL so the links are correct.

## Status API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/home-assistant/status` | `mode`, last sync, last error, per-camera published timestamps/topics |
| `GET /api/home-assistant/config` | Effective (auto-detected) connection values with masked secrets, for UI prefill |
| `PUT /api/home-assistant/config` | Save connection settings (masked/empty secrets are ignored) |
| `POST /api/home-assistant/test` | Connection test; returns the `mqtt-only` message when no token is configured |
| `POST /api/home-assistant/sync` | Trigger an immediate best-effort reconcile |
| `GET /api/home-assistant/cameras/{id}` | Per-camera sync status and last published time |
