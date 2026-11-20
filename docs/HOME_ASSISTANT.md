# Home Assistant Integration

Lookout can publish cameras to Home Assistant via MQTT auto-discovery and reconcile state over the HA REST API.

## Configuration

Global settings live under `home_assistant:` in `config.yaml`:

```yaml
home_assistant:
  enabled: true
  base_url: "http://localhost:8123"
  access_token: "long-lived-access-token"
  ingress_url: "http://localhost:8123/api/hassio_ingress/lookout"
```

Per-camera settings are available on each camera's settings page (enabled, entity name, unique id, device class, icon, event granularity).

## MQTT broker

Configure an MQTT broker (Lookout's `mqtt:` block or the override fields under `home_assistant:`). Lookout publishes retained discovery topics under `homeassistant/...`.

## Ingress deep links

Camera and event entities carry `configuration_url` / `link` / `url` attributes pointing at the Lookout ingress panel for the camera or event.
