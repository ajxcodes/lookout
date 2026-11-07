/**
 * Camera switcher toolbar component.
 */
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
      tab.className = `cam-tab ${cam.id === this.activeCameraId ? "active" : ""}`;
      tab.dataset.cameraId = cam.id;

      const nameSpan = document.createElement("span");
      nameSpan.textContent = cam.name || cam.id;
      tab.appendChild(nameSpan);

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
