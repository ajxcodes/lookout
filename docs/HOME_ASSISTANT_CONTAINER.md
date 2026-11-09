# Home Assistant Container (Docker / Podman) Deployment Guide

This guide details turnkey deployment of **Lookout** alongside **Home Assistant Container** (standalone Docker or Rootless Podman) installations.

---

## Architectural Constraint: Container vs. Supervised Add-Ons

> [!IMPORTANT]
> **Home Assistant Supervised Add-ons are NOT supported on Home Assistant Container or Core installations.**  
> Add-ons require the Home Assistant Supervisor docker control daemon. In Container deployments, Lookout is deployed as an adjacent standalone container with reverse-proxy TLS termination and embedded via Lovelace iFrame dashboards.

---

## 1. Standalone Docker Compose Deployment

Deploy Lookout and `go2rtc` alongside Home Assistant:

```yaml
services:
  lookout:
    image: ghcr.io/ajxcodes/lookout:v0.3.0
    container_name: lookout
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - LOOKOUT_HOST=0.0.0.0
      - LOOKOUT_PORT=8000
    volumes:
      - /opt/lookout/data:/app/data
      - /opt/lookout/config.yaml:/app/config.yaml:ro
      - /dev/shm:/dev/shm
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128 # Intel VAAPI QSV offload

  go2rtc:
    image: alexxit/go2rtc:v1.9.14
    container_name: lookout-go2rtc
    restart: unless-stopped
    network_mode: host
    volumes:
      - /opt/lookout/go2rtc.yaml:/config/go2rtc.yaml:ro
```

---

## 2. Reverse Proxy TLS & CSP Configuration (Traefik)

To embed Lookout securely within Home Assistant without browser mixed-content (HTTPS -> HTTP) or clickjacking blocks, Traefik must supply required `Content-Security-Policy: frame-ancestors` headers and route duplex WebSocket and streaming endpoints.

```yaml
# traefik/dynamic/lookout.yml
http:
  middlewares:
    lookout-headers:
      headers:
        customResponseHeaders:
          Content-Security-Policy: "frame-ancestors 'self' https://*.home.ajx.codes https://ha.home.ajx.codes"
          X-Frame-Options: ""

  routers:
    # Main Web Viewer UI & REST API router
    lookout-web:
      rule: "Host(`lookout.home.ajx.codes`)"
      entryPoints:
        - "websecure"
      tls: {}
      middlewares:
        - "lookout-headers"
      service: "lookout-service"

    # WebRTC Signaling, Stream MP4, and Live Frame Ingress router terminating at go2rtc
    lookout-webrtc:
      rule: "Host(`lookout.home.ajx.codes`) && (PathPrefix(`/api/ws`) || PathPrefix(`/api/webrtc`) || PathPrefix(`/api/frame.jpeg`) || PathPrefix(`/api/stream.mp4`) || PathPrefix(`/api/streams`))"
      entryPoints:
        - "websecure"
      tls: {}
      middlewares:
        - "lookout-headers"
      service: "go2rtc-service"

  services:
    lookout-service:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8000"

    go2rtc-service:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:1984"
```

---

## 3. Lovelace iFrame Dashboard Embedding

Add Lookout to your Home Assistant sidebar via `configuration.yaml`:

```yaml
panel_iframe:
  lookout:
    title: "Lookout"
    icon: "mdi:cctv"
    url: "https://lookout.home.ajx.codes"
    require_admin: false
```

---

## 4. Native HACS Custom Component Setup

Even in Container mode, the native custom component can be installed into `custom_components/lookout`:
1. Copy `custom_components/lookout` to your HA `config/custom_components/lookout`.
2. Restart Home Assistant Container.
3. In Home Assistant, go to **Settings** → **Devices & Services** → **Add Integration** → **Lookout**.
4. Enter Host (`lookout.home.ajx.codes` or local IP) and Port (`8000`).
5. All `camera.*`, `binary_sensor.*`, `sensor.*`, and `select.*` entities will automatically be registered!
