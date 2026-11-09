/**
 * Interactive Touch & Pointer Zoom Controller for Lookout Video & Snapshot Views.
 * Supports:
 * - Mobile 2-finger pinch-to-zoom (1.0x to 5.0x) on both live stream, recorded clips, and snapshots
 * - 1-finger panning when zoomed in
 * - Double-tap / double-click to toggle 1.0x <-> 2.5x
 * - Desktop mouse wheel zoom & click-drag pan
 * - Floating HUD pill showing current zoom factor with 1-click Reset button
 * - Programmatic zoomIn, zoomOut, and resetZoom methods
 */

export class ZoomController {
  constructor(containerEl, { customTargetEl = null, onZoomChange = null, showHud = true } = {}) {
    this.container = containerEl;
    this.customTarget = customTargetEl;
    this.onZoomChange = onZoomChange;
    this.showHud = showHud;

    this.zoom = 1.0;
    this.panX = 0;
    this.panY = 0;

    this.minZoom = 1.0;
    this.maxZoom = 5.0;

    // Gesture tracking state
    this.isDragging = false;
    this.startX = 0;
    this.startY = 0;
    this.initialPinchDist = 0;
    this.initialZoom = 1.0;
    this.lastTapTime = 0;

    if (this.showHud) {
      this.createHudBadge();
    }
    this.bindEvents();
  }

  createHudBadge() {
    this.hudBadge = document.createElement("button");
    this.hudBadge.className = "video-zoom-badge";
    this.hudBadge.type = "button";
    this.hudBadge.style.cssText = `
      position: absolute;
      top: 14px;
      right: 14px;
      z-index: 25;
      display: none;
      align-items: center;
      gap: 6px;
      background: rgba(15, 23, 42, 0.75);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      color: #f8fafc;
      font-size: 0.75rem;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 9999px;
      border: 1px solid rgba(255, 255, 255, 0.15);
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      transition: background 0.15s ease, transform 0.15s ease;
    `;
    this.hudBadge.innerHTML = `<span>🔍</span> <span class="zoom-text">1.0x</span> <span style="font-size:0.65rem;opacity:0.8;">↺ Reset</span>`;
    this.hudBadge.addEventListener("click", (e) => {
      e.stopPropagation();
      this.resetZoom(true);
    });

    if (this.container) {
      this.container.appendChild(this.hudBadge);
    }
  }

  getActiveTarget() {
    if (this.customTarget) return this.customTarget;
    if (!this.container) return null;

    const snapImg = document.getElementById("snapshot-card-img");
    const snapCard = document.getElementById("event-snapshot-card");
    const liveVideo = document.getElementById("live-video");
    const eventVideo = document.getElementById("event-video");
    const snapOverlay = document.getElementById("snapshot-overlay");

    // 1. Snapshot card in focus mode (event playback)
    if (snapCard && snapCard.style.display !== "none" && snapImg) {
      return snapImg;
    }
    // 2. Live WebRTC video stream
    if (liveVideo && liveVideo.style.display !== "none") {
      return liveVideo;
    }
    // 3. Recorded MP4 event video
    if (eventVideo && eventVideo.style.display !== "none") {
      return eventVideo;
    }
    // 4. On-demand camera live snapshot overlay
    if (snapOverlay && snapOverlay.style.display !== "none") {
      return snapOverlay;
    }
    return null;
  }

  applyTransform(animate = false) {
    const target = this.getActiveTarget();
    if (!target) return;

    // Clamp pan based on current zoom level and container dimensions
    const rect = this.container.getBoundingClientRect();
    const maxPanX = Math.max(0, ((this.zoom - 1) * rect.width) / 2);
    const maxPanY = Math.max(0, ((this.zoom - 1) * rect.height) / 2);

    this.panX = Math.max(-maxPanX, Math.min(maxPanX, this.panX));
    this.panY = Math.max(-maxPanY, Math.min(maxPanY, this.panY));

    target.style.transformOrigin = "center center";
    target.style.transition = animate ? "transform 0.2s cubic-bezier(0.16, 1, 0.3, 1)" : "none";
    target.style.transform = `translate(${this.panX}px, ${this.panY}px) scale(${this.zoom})`;

    // Update HUD indicator
    if (this.hudBadge) {
      if (this.zoom > 1.05) {
        this.hudBadge.style.display = "inline-flex";
        const textEl = this.hudBadge.querySelector(".zoom-text");
        if (textEl) textEl.textContent = `${this.zoom.toFixed(1)}x`;
      } else {
        this.hudBadge.style.display = "none";
      }
    }

    if (typeof this.onZoomChange === "function") {
      this.onZoomChange(this.zoom);
    }
  }

  zoomIn(step = 0.5) {
    this.zoom = Math.min(this.maxZoom, this.zoom + step);
    this.applyTransform(true);
  }

  zoomOut(step = 0.5) {
    this.zoom = Math.max(this.minZoom, this.zoom - step);
    if (this.zoom <= 1.05) {
      this.resetZoom(true);
    } else {
      this.applyTransform(true);
    }
  }

  resetZoom(animate = true) {
    this.zoom = 1.0;
    this.panX = 0;
    this.panY = 0;
    this.applyTransform(animate);

    const targets = this.customTarget
      ? [this.customTarget]
      : [
          document.getElementById("live-video"),
          document.getElementById("event-video"),
          document.getElementById("snapshot-card-img"),
          document.getElementById("snapshot-overlay"),
        ];

    targets.forEach((t) => {
      if (t) {
        t.style.transition = animate ? "transform 0.2s cubic-bezier(0.16, 1, 0.3, 1)" : "none";
        t.style.transform = "translate(0px, 0px) scale(1)";
      }
    });

    if (this.hudBadge) this.hudBadge.style.display = "none";
    if (typeof this.onZoomChange === "function") {
      this.onZoomChange(1.0);
    }
  }

  bindEvents() {
    if (!this.container) return;

    // --- Touch Gestures (Mobile Phone / Tablet) ---
    this.container.addEventListener(
      "touchstart",
      (e) => {
        if (e.touches.length === 2) {
          // Pinch start
          this.initialPinchDist = Math.hypot(
            e.touches[0].clientX - e.touches[1].clientX,
            e.touches[0].clientY - e.touches[1].clientY
          );
          this.initialZoom = this.zoom;
          e.preventDefault();
        } else if (e.touches.length === 1) {
          // Check for double-tap
          const now = Date.now();
          if (now - this.lastTapTime < 300) {
            e.preventDefault();
            this.handleDoubleTap(e.touches[0].clientX, e.touches[0].clientY);
            this.lastTapTime = 0;
            return;
          }
          this.lastTapTime = now;

          if (this.zoom > 1.0) {
            this.isDragging = true;
            this.startX = e.touches[0].clientX - this.panX;
            this.startY = e.touches[0].clientY - this.panY;
            e.preventDefault();
          }
        }
      },
      { passive: false }
    );

    this.container.addEventListener(
      "touchmove",
      (e) => {
        if (e.touches.length === 2 && this.initialPinchDist > 0) {
          // Pinch zoom
          const currentDist = Math.hypot(
            e.touches[0].clientX - e.touches[1].clientX,
            e.touches[0].clientY - e.touches[1].clientY
          );
          const factor = currentDist / this.initialPinchDist;
          this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, this.initialZoom * factor));
          this.applyTransform(false);
          e.preventDefault();
        } else if (e.touches.length === 1 && this.isDragging && this.zoom > 1.0) {
          // Pan while zoomed
          this.panX = e.touches[0].clientX - this.startX;
          this.panY = e.touches[0].clientY - this.startY;
          this.applyTransform(false);
          e.preventDefault();
        }
      },
      { passive: false }
    );

    this.container.addEventListener("touchend", (e) => {
      if (e.touches.length < 2) {
        this.initialPinchDist = 0;
      }
      if (e.touches.length === 0) {
        this.isDragging = false;
        if (this.zoom < 1.05) {
          this.resetZoom(true);
        } else {
          this.applyTransform(true);
        }
      }
    });

    // --- Mouse & Pointer Events (Desktop / Trackpad) ---
    this.container.addEventListener("mousedown", (e) => {
      if (e.button !== 0 || this.zoom <= 1.0) return;
      this.isDragging = true;
      this.startX = e.clientX - this.panX;
      this.startY = e.clientY - this.panY;
      this.container.style.cursor = "grabbing";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!this.isDragging || this.zoom <= 1.0) return;
      this.panX = e.clientX - this.startX;
      this.panY = e.clientY - this.startY;
      this.applyTransform(false);
    });

    window.addEventListener("mouseup", () => {
      if (this.isDragging) {
        this.isDragging = false;
        if (this.container) {
          this.container.style.cursor = this.zoom > 1.0 ? "grab" : "default";
        }
        this.applyTransform(true);
      }
    });

    this.container.addEventListener("dblclick", (e) => {
      e.preventDefault();
      this.handleDoubleTap(e.clientX, e.clientY);
    });

    // Mouse wheel zoom
    this.container.addEventListener(
      "wheel",
      (e) => {
        if (!e.ctrlKey && Math.abs(e.deltaY) < 5) return;
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.3 : -0.3;
        const newZoom = Math.max(this.minZoom, Math.min(this.maxZoom, this.zoom + delta));
        if (newZoom !== this.zoom) {
          this.zoom = newZoom;
          if (this.zoom <= 1.05) {
            this.resetZoom(true);
          } else {
            this.container.style.cursor = "grab";
            this.applyTransform(true);
          }
        }
      },
      { passive: false }
    );
  }

  handleDoubleTap(clientX, clientY) {
    if (this.zoom > 1.2) {
      this.resetZoom(true);
    } else {
      const rect = this.container.getBoundingClientRect();
      const offsetX = clientX - (rect.left + rect.width / 2);
      const offsetY = clientY - (rect.top + rect.height / 2);

      this.zoom = 2.5;
      this.panX = -offsetX * 1.2;
      this.panY = -offsetY * 1.2;
      if (this.container) this.container.style.cursor = "grab";
      this.applyTransform(true);
    }
  }
}
