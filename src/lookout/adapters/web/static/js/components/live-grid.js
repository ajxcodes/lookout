/**
 * Multi-camera Live Grid View component with density selector and lifecycle governance.
 */
import { infer_camera_icon } from "./switcher.js";
import { resolveUrl } from "../utils/url.js";

export class LiveGrid {
  constructor({ container, densitySelector, onFocusCamera }) {
    this.container = container;
    this.densitySelector = densitySelector;
    this.onFocusCamera = onFocusCamera;
    this.cameras = [];
    try {
      this.density = localStorage?.getItem("lookout_grid_density") || "auto";
    } catch (_) {
      this.density = "auto";
    }
    try {
      const stored = localStorage?.getItem("lookout_grid_selected_cameras");
      this.selectedCameraIds = stored ? JSON.parse(stored) : null;
    } catch (_) {
      this.selectedCameraIds = null;
    }
    this.streamPlayers = new Map(); // cameraId -> { pc, ws, video, overlay, isLive }
    this.intersectionObserver = null;
    this.isTabVisible = !document.hidden;

    this.initLifecycle();
  }

  initLifecycle() {
    // Visibility state tab freeze
    document.addEventListener("visibilitychange", () => {
      this.isTabVisible = !document.hidden;
      if (!this.isTabVisible) {
        this.freezeAllStreams();
      } else {
        this.resumeVisibleStreams();
      }
    });

    // Viewport culling via IntersectionObserver
    if ("IntersectionObserver" in window) {
      this.intersectionObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            const cameraId = entry.target.dataset.cameraId;
            if (!cameraId) return;
            if (entry.isIntersecting) {
              this.resumeCameraStream(cameraId);
            } else {
              this.pauseCameraStream(cameraId);
            }
          });
        },
        { threshold: 0.1 }
      );
    }

    if (this.densitySelector) {
      this.densitySelector.value = this.density;
      this.densitySelector.addEventListener("change", (e) => {
        this.setDensity(e.target.value);
      });
    }
  }

  setDensity(density) {
    this.density = density;
    try {
      localStorage?.setItem("lookout_grid_density", density);
    } catch (_) {}
    if (this.container) {
      this.container.className = `live-grid-container density-${density}`;
    }
  }

  setSelectedCameras(cameraIds) {
    this.selectedCameraIds = cameraIds;
    try {
      localStorage?.setItem("lookout_grid_selected_cameras", JSON.stringify(cameraIds));
    } catch (_) {}
    this.render();
  }

  setCameras(cameras) {
    this.cameras = cameras.filter((c) => c.enabled !== false);
    this.render();
  }

  render() {
    if (!this.container) return;
    this.cleanup();
    this.container.innerHTML = "";
    this.container.className = `live-grid-container density-${this.density}`;

    const visibleCameras =
      this.selectedCameraIds && this.selectedCameraIds.length > 0
        ? this.cameras.filter((c) => this.selectedCameraIds.includes(c.id))
        : this.cameras;

    if (visibleCameras.length === 0) {
      const emptyDiv = document.createElement("div");
      emptyDiv.className = "text-muted grid-empty-message";
      emptyDiv.style.padding = "var(--space-xl)";
      emptyDiv.style.textAlign = "center";
      emptyDiv.textContent = "No camera feeds available in Live Grid";
      this.container.appendChild(emptyDiv);
      return;
    }

    visibleCameras.forEach((cam) => {
      const tile = document.createElement("div");
      tile.className = "camera-grid-tile";
      tile.dataset.cameraId = cam.id;

      const icon = cam.icon || infer_camera_icon(cam.name || cam.location);
      const provider = (cam.provider_badge || (cam.camera_type === "ring" ? "ring" : (cam.camera_type === "wyze" ? "wyze" : "generic"))).toLowerCase();
      const streamMode = cam.stream_mode || (provider === "ring" ? "on_demand" : "live_rtc");

      const header = document.createElement("div");
      header.className = "tile-header";
      header.innerHTML = `
        <div class="tile-title">
          <span class="cam-icon">${icon}</span>
          <span class="cam-name">${cam.name || cam.id}</span>
        </div>
        <div class="tile-badges">
          <span class="badge-provider ${provider}">${provider.charAt(0).toUpperCase() + provider.slice(1)}</span>
          <span class="badge-stream ${streamMode}">${streamMode === "live_rtc" ? "● Live RTC" : "● On-Demand"}</span>
        </div>
      `;
      tile.appendChild(header);

      const stage = document.createElement("div");
      stage.className = "tile-stage";

      const snapUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);

      const video = document.createElement("video");
      video.className = "tile-video";
      video.autoplay = true;
      video.playsInline = true;
      video.muted = true;
      video.poster = snapUrl;
      video.style.display = streamMode === "live_rtc" ? "block" : "none";
      stage.appendChild(video);

      const overlay = document.createElement("div");
      overlay.className = "tile-overlay";
      overlay.style.backgroundImage = `url("${snapUrl}")`;
      // Poster background shown initially during negotiation or for on_demand
      overlay.style.display = "flex";

      if (streamMode === "on_demand") {
        const liveBtn = document.createElement("button");
        liveBtn.className = "tile-live-btn";
        liveBtn.textContent = "▶ Start Live";
        liveBtn.type = "button";
        liveBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.startTileLiveWebRTC(cam, video, overlay);
        });
        overlay.appendChild(liveBtn);
      }

      stage.appendChild(overlay);

      // Drilldown click action
      tile.addEventListener("click", () => {
        if (this.onFocusCamera) {
          this.onFocusCamera(cam);
        }
      });

      tile.appendChild(stage);
      this.container.appendChild(tile);

      if (this.intersectionObserver) {
        this.intersectionObserver.observe(tile);
      }

      // Store player record
      this.streamPlayers.set(cam.id, {
        camera: cam,
        video,
        overlay,
        pc: null,
        ws: null,
        isLive: false,
        streamMode,
      });

      // Auto-start continuous mains streams
      if (streamMode === "live_rtc" && this.isTabVisible) {
        this.startTileLiveWebRTC(cam, video, overlay);
      }
    });
  }

  async startTileLiveWebRTC(cam, video, overlay) {
    const record = this.streamPlayers.get(cam.id);
    if (!record) return;

    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      record.pc = pc;

      pc.ontrack = (event) => {
        if (!video) return;
        if (event.streams && event.streams[0]) {
          video.srcObject = event.streams[0];
        } else {
          if (!video.srcObject) video.srcObject = new MediaStream();
          video.srcObject.addTrack(event.track);
        }
        video.muted = true;
        video.play().then(() => {
          overlay.style.display = "none";
        }).catch(() => {});
        video.style.display = "block";
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          video.style.display = "none";
          overlay.style.display = "flex";
        }
      };

      pc.addTransceiver("video", { direction: "recvonly" });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsHost = window.location.host;
      // Prefer substream if available to conserve bandwidth
      const wsUrl = `${wsProtocol}//${wsHost}/api/ws?src=${encodeURIComponent(cam.id)}`;

      const ws = new WebSocket(wsUrl);
      record.ws = ws;

      const iceCandidates = [];
      let hasRemote = false;

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: "webrtc/offer", value: offer.sdp }));
      };

      ws.onmessage = async (msgEvent) => {
        try {
          const data = JSON.parse(msgEvent.data);
          if (data.type === "webrtc/answer") {
            await pc.setRemoteDescription({ type: "answer", sdp: data.value });
            hasRemote = true;
            while (iceCandidates.length > 0) {
              const c = iceCandidates.shift();
              await pc.addIceCandidate(c).catch(() => {});
            }
          } else if (data.type === "webrtc/candidate") {
            const candidate = new RTCIceCandidate({ candidate: data.value });
            if (!hasRemote) {
              iceCandidates.push(candidate);
            } else {
              await pc.addIceCandidate(candidate).catch(() => {});
            }
          }
        } catch {}
      };

      pc.onicecandidate = (e) => {
        if (e.candidate && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "webrtc/candidate", value: e.candidate.candidate }));
        }
      };

      record.isLive = true;
    } catch (err) {
      console.warn(`Live Grid tile connection failed for ${cam.id}:`, err);
      if (video) video.style.display = "none";
      if (overlay) overlay.style.display = "flex";
    }
  }

  freezeAllStreams() {
    this.streamPlayers.forEach((record) => {
      if (record.video) record.video.pause();
    });
  }

  resumeVisibleStreams() {
    this.streamPlayers.forEach((record) => {
      if (record.video && record.isLive) {
        record.video.play().catch(() => {});
      }
    });
  }

  pauseCameraStream(cameraId) {
    const record = this.streamPlayers.get(cameraId);
    if (record && record.video) {
      record.video.pause();
    }
  }

  resumeCameraStream(cameraId) {
    const record = this.streamPlayers.get(cameraId);
    if (record && record.video && record.isLive && this.isTabVisible) {
      record.video.play().catch(() => {});
    }
  }

  cleanup() {
    this.streamPlayers.forEach((record) => {
      if (record.ws) {
        try { record.ws.close(); } catch {}
      }
      if (record.pc) {
        try { record.pc.close(); } catch {}
      }
      if (record.video) {
        record.video.srcObject = null;
      }
    });
    this.streamPlayers.clear();
  }
}
