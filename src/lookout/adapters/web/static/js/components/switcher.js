/**
 * Rich Camera Switcher component featuring provider and stream badges.
 */

export function infer_camera_icon(nameOrLocation) {
  if (!nameOrLocation) return "📹";
  const s = nameOrLocation.toLowerCase();
  if (s.includes("door") || s.includes("front") || s.includes("entry")) return "🚪";
  if (s.includes("backyard") || s.includes("yard") || s.includes("garden")) return "🌲";
  if (s.includes("crate") || s.includes("kennel") || s.includes("mochi crate")) return "🐾";
  if (s.includes("pan") || s.includes("dog") || s.includes("cat")) return "🐶";
  if (s.includes("office") || s.includes("desk") || s.includes("work")) return "💼";
  if (s.includes("driveway") || s.includes("garage") || s.includes("car")) return "🚗";
  if (s.includes("living") || s.includes("couch") || s.includes("lounge")) return "🛋️";
  if (s.includes("porch") || s.includes("patio") || s.includes("deck")) return "🏡";
  return "📹";
}

export class CameraSwitcher {
  constructor({ container, onSelectCamera }) {
    this.container = container;
    this.onSelectCamera = onSelectCamera;
    this.cameras = [];
    this.activeCameraId = null;
    this.eventCounts = {};
  }

  setCameras(cameras) {
    this.cameras = cameras;
    if (cameras.length > 0 && !this.activeCameraId) {
      this.activeCameraId = cameras[0].id;
    }
    this.render();
  }

  setEventCounts(counts) {
    this.eventCounts = counts;
    this.render();
  }

  setActiveCamera(cameraId) {
    this.activeCameraId = cameraId;
    this.render();
  }

  render() {
    if (!this.container) return;
    this.container.innerHTML = "";

    if (this.cameras.length === 0) {
      const emptySpan = document.createElement("span");
      emptySpan.className = "text-muted";
      emptySpan.style.fontSize = "0.75rem";
      emptySpan.textContent = "No cameras discovered";
      this.container.appendChild(emptySpan);
      return;
    }

    this.cameras.forEach((cam) => {
      const tab = document.createElement("button");
      tab.className = `cam-tab camera-chip ${cam.id === this.activeCameraId ? "active" : ""}`;
      tab.dataset.cameraId = cam.id;

      const icon = cam.icon || infer_camera_icon(cam.name || cam.location);
      const provider = (cam.provider_badge || (cam.camera_type === "ring" ? "ring" : (cam.camera_type === "wyze" ? "wyze" : "generic"))).toLowerCase();
      const streamMode = cam.stream_mode || (provider === "ring" ? "on_demand" : "live_rtc");

      const iconSpan = document.createElement("span");
      iconSpan.className = "cam-icon";
      iconSpan.textContent = icon;
      tab.appendChild(iconSpan);

      const nameSpan = document.createElement("span");
      nameSpan.className = "cam-name";
      nameSpan.textContent = cam.name || cam.id;
      tab.appendChild(nameSpan);

      // Provider badge (Ring cyan, Wyze emerald, Generic slate)
      const provBadge = document.createElement("span");
      provBadge.className = `badge-provider ${provider}`;
      provBadge.textContent = provider.charAt(0).toUpperCase() + provider.slice(1);
      tab.appendChild(provBadge);

      // Stream delivery mode badge (On-Demand amber dot vs Live RTC green dot)
      const streamBadge = document.createElement("span");
      streamBadge.className = `badge-stream ${streamMode}`;
      streamBadge.textContent = streamMode === "live_rtc" ? "● Live RTC" : "● On-Demand";
      tab.appendChild(streamBadge);

      const count = this.eventCounts[cam.id] || 0;
      if (count > 0) {
        const countBadge = document.createElement("span");
        countBadge.className = "badge-count";
        countBadge.textContent = count;
        tab.appendChild(countBadge);
      }

      if (!cam.enabled) {
        const pausedBadge = document.createElement("span");
        pausedBadge.className = "badge-paused";
        pausedBadge.textContent = "paused";
        tab.appendChild(pausedBadge);
      }

      tab.addEventListener("click", () => {
        this.activeCameraId = cam.id;
        this.render();
        if (this.onSelectCamera) {
          this.onSelectCamera(cam);
        }
      });

      this.container.appendChild(tab);
    });
  }
}
