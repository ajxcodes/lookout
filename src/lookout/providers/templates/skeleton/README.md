# Implementing a New Camera Stream Provider in Lookout

This directory provides a starter template driver enabling rapid addition of new camera ecosystems (such as Eufy, Reolink, TP-Link Tapo, Blink, or RTSP/ONVIF generic devices) without modifying Lookout core.

---

## 5-Step Implementation Guide

### Step 1: Copy the Template
Copy `src/lookout/providers/templates/skeleton/` to `src/lookout/providers/<brand_name>/`:
```bash
cp -r src/lookout/providers/templates/skeleton src/lookout/providers/tapo
```

### Step 2: Define UI Manifest Metadata (`manifest.json`)
Configure your provider's presentation metadata, badge, brand colors, default RTSP/HTTP ports, and interactive login input fields:
```json
{
  "id": "tapo",
  "name": "tapo",
  "display_name": "TP-Link Tapo",
  "description": "Integration with TP-Link Tapo cameras via RTSP bridge",
  "icon": "video",
  "brand_color": "#20b2aa",
  "badge": "Tapo RTSP",
  "default_ports": { "rtsp": 554, "http": 80 },
  "default_stream_url": "rtsp://localhost:554/stream1",
  "auth_fields": [
    { "key": "username", "label": "Camera Account Username", "type": "text", "required": true },
    { "key": "password", "label": "Camera Account Password", "type": "password", "required": true }
  ]
}
```

### Step 3: Implement Authentication & 2FA (`driver.py`)
Subclass `BaseProviderDriver` and customize authentication logic:
```python
class TapoProviderDriver(BaseProviderDriver):
    provider_id = "tapo"
    display_name = "TP-Link Tapo"
    default_ports = {"http": 80, "rtsp": 554}

    def authenticate(self, credentials: dict[str, Any]) -> AuthResult:
        # Perform authentication or return 2FA challenge
        ...
```

### Step 4: Implement Discovery & Health Probing (`discovery.py`)
Implement socket or HTTP probes to verify active bridges, running containers, or credentials:
```python
def probe_tapo(
    driver: TapoProviderDriver, config: LookoutConfig | None = None
) -> DiscoveryResult: ...
```

### Step 5: Implement Container Environment Synchronization (`sync.py`)
Generate secure environment configuration files (e.g. `data/sidecars/tapo.env` with `0o600` permissions) for Docker Compose bridge sidecars:
```python
def sync_tapo_environment(config: LookoutConfig, target_dir: Path) -> list[Path]: ...
```

---

## Automatic Discovery
`ProviderRegistry` will automatically discover and register your new driver upon application startup, mounting its sub-router at `/api/providers/<brand_name>/*` and exposing its manifest in `/api/providers`.
