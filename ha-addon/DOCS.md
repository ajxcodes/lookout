# Lookout Home Assistant Add-On

Lookout is a lightweight, multi-modal, privacy-first smart home camera intelligence platform.

## Features
- **Zero-Config Setup**: One-click installation through Home Assistant Add-on Store.
- **Native Ingress Support**: Integrated directly into Home Assistant's sidebar with authenticated reverse proxy.
- **AI-Powered Event Intelligence**: Multi-tier vision cascade, face recognition, and acoustic anomaly detection.
- **Local Privacy**: Runs 100% locally with zero cloud dependencies.

## Installation
1. In Home Assistant, navigate to **Settings** -> **Add-ons** -> **Add-on Store**.
2. Click the three dots in the top-right corner and select **Repositories**.
3. Add this repository URL: `https://github.com/ajxcodes/lookout` and click **Add**.
4. Scroll to **Lookout** in the store list and click **Install**.
5. Once installed, start the add-on and enable **Show in sidebar**.

## Configuration
Lookout reads its configuration from `/homeassistant/lookout/config.yaml`.
If no configuration file is present on initial launch, Lookout opens its built-in interactive setup wizard at the root Ingress screen.

```yaml
system_name: "Lookout Home"
storage:
  base_path: "/media/lookout"
  database_path: "/homeassistant/lookout/events.db"

cameras:
  - id: "front_door"
    name: "Front Door"
    stream_url: "rtsp://camera.local:8554/front_door"
    enabled: true
```

## Ingress Access
With `ingress: true` enabled, clicking **Lookout** in the Home Assistant sidebar will directly open the modern Web Viewer SPA.
No additional port forwarding or external authentication is required.
