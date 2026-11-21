/**
 * Multi-camera Live Grid View component with density selector and lifecycle governance.
 */
import { infer_camera_icon } from "./switcher.js";
import { resolveUrl } from "../utils/url.js";
import { buildCameraManagementControls } from "./camera-management.js";

function resolveWsUrl(path) {
  if (!path) return path;
  if (path.startsWith("ws://") || path.startsWith("wss://")) {
    return path;
  }
  const resolved = resolveUrl(path);
  const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
  if (resolved.startsWith("http://")) {
    return "ws://" + resolved.slice("http://".length);
  }
  if (resolved.startsWith("https://")) {
    return "wss://" + resolved.slice("https://".length);
  }
  const host = window.location.host;
  const clean = resolved.startsWith("/") ? resolved : "/" + resolved;
  return `${wsProto}//${host}${clean}`;
}

export class LiveGrid {
  constructor({
    container,
    densitySelector,
    onFocusCamera,
    onRemoveCamera,
    onReauthenticateCamera,
    onOpenCameraSettings,
    cameraManagementLabels,
  }) {
    this.container = container;
    this.densitySelector = densitySelector;
    this.onFocusCamera = onFocusCamera;
    // AC10: management bar callbacks are injected by the app so the controls
    // are built directly into each rendered tile (no fragile post-hoc selector).
    this.onRemoveCamera = onRemoveCamera;
    this.onReauthenticateCamera = onReauthenticateCamera;
    this.onOpenCameraSettings = onOpenCameraSettings;
    this.cameraManagementLabels = cameraManagementLabels;
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
      this.selectedCameraIds !== null
        ? this.cameras.filter((c) => this.selectedCameraIds.includes(c.id))
        : this.cameras;

    if (visibleCameras.length === 0) {
      const emptyDiv = document.createElement("div");
      emptyDiv.className = "text-muted grid-empty-message";
      emptyDiv.style.padding = "var(--space-xl)";
      emptyDiv.style.textAlign = "center";
      emptyDiv.textContent = "No camera feeds selected in Live Grid";
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
          <span class="cam-icon" title="Camera icon (decorative)">${icon}</span>
          <span class="cam-name">${cam.name || cam.id}</span>
          <button type="button" class="camera-gear-btn" title="Camera settings">⚙</button>
        </div>
        <div class="tile-badges">
          <span class="badge-provider ${provider}" title="Provider badge">${provider.charAt(0).toUpperCase() + provider.slice(1)}</span>
          <span class="badge-stream ${streamMode}" title="Stream mode badge">${streamMode === "live_rtc" ? "● Live RTC" : "● On-Demand"}</span>
        </div>
      `;
      tile.appendChild(header);

      // Wire the per-tile header gear icon to open camera settings.
      const headerGear = header.querySelector(".camera-gear-btn");
      if (headerGear) {
        headerGear.addEventListener("click", (e) => {
          e.stopPropagation();
          if (typeof this.onOpenCameraSettings === "function") {
            this.onOpenCameraSettings(cam);
          }
        });
      }

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
          if (cam.provider_badge === "ring" || cam.camera_type === "ring") {
            if (
              cam.live_stream_url ||
              cam.live_source ||
              cam.has_live_stream ||
              cam.has_live_source
            ) {
              this.startTileLiveWebRTC(cam, video, overlay, `${cam.id}_live`);
            } else {
              this.startTileLiveWebRTC(cam, video, overlay, cam.id);
            }
          } else {
            this.startTileLiveWebRTC(cam, video, overlay);
          }
        });
        overlay.appendChild(liveBtn);
      }

      const clockBadge = document.createElement("div");
      clockBadge.className = "live-duration-badge tile-clock-badge";
      clockBadge.style.display = "none";
      const clockSpan = document.createElement("span");
      clockSpan.className = "duration-timer";
      clockBadge.innerHTML = `<span class="live-indicator-dot" title="Live indicator dot">🔴</span> LIVE `;
      clockBadge.appendChild(clockSpan);
      stage.appendChild(clockBadge);

      stage.appendChild(overlay);

      // Manual + PTZ controls (gated on ptz_enabled for PTZ)
      const controls = document.createElement("div");
      controls.className = "tile-controls";
      controls.style.display = "flex";
      controls.style.gap = "4px";
      controls.style.padding = "4px";
      controls.style.flexWrap = "wrap";

      const mkBtn = (label, onClick, title) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.title = title || label;
        b.style.fontSize = "0.7rem";
        b.style.padding = "2px 6px";
        b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
        return b;
      };

      const ptzPost = (body) => fetch(resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/ptz`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      // Manual snapshot/record for all cameras
      controls.appendChild(mkBtn("📸", () => fetch(resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot`), { method: "POST" }), "Manual snapshot"));
      controls.appendChild(mkBtn("⏺", () => fetch(resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/record`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ duration: cam.record_duration || 10 }) }), "Record clip"));

      if (cam.ptz_enabled) {
        const ptzDetails = document.createElement("details");
        ptzDetails.className = "ptz-controls";
        const ptzSummary = document.createElement("summary");
        ptzSummary.textContent = "PTZ";
        ptzDetails.appendChild(ptzSummary);
        ptzDetails.appendChild(mkBtn("◀", () => ptzPost({ action: "step", direction: "left" }), "PTZ left"));
        ptzDetails.appendChild(mkBtn("▲", () => ptzPost({ action: "step", direction: "up" }), "PTZ up"));
        ptzDetails.appendChild(mkBtn("▼", () => ptzPost({ action: "step", direction: "down" }), "PTZ down"));
        ptzDetails.appendChild(mkBtn("▶", () => ptzPost({ action: "step", direction: "right" }), "PTZ right"));
        ptzDetails.appendChild(mkBtn("➕", () => ptzPost({ action: "step", direction: "zoom_in" }), "Zoom in"));
        ptzDetails.appendChild(mkBtn("➖", () => ptzPost({ action: "step", direction: "zoom_out" }), "Zoom out"));
        ptzDetails.appendChild(mkBtn("⏹", () => ptzPost({ action: "stop" }), "PTZ stop"));
        controls.appendChild(ptzDetails);
      }

      tile.appendChild(controls);

      // Drilldown click action
      tile.addEventListener("click", () => {
        if (this.onFocusCamera) {
          this.onFocusCamera(cam);
        }
      });

      tile.appendChild(stage);

      // AC10: render the management bar as part of the tile so every camera
      // (any provider, any auth state) exposes "Remove camera", and
      // unauthorized native cameras show Reauthenticate / auth-required.
      tile.appendChild(
        buildCameraManagementControls(cam, {
          onRemoveCamera: this.onRemoveCamera,
          onReauthenticateCamera: this.onReauthenticateCamera,
          onOpenCameraSettings: this.onOpenCameraSettings,
          labels: this.cameraManagementLabels,
        })
      );

      this.container.appendChild(tile);

      if (this.intersectionObserver) {
        this.intersectionObserver.observe(tile);
      }

      // Store player record
      this.streamPlayers.set(cam.id, {
        camera: cam,
        video,
        overlay,
        clockBadge,
        clockSpan,
        clockInterval: null,
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

  _formatCurrentClock() {
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");
    const hh = String(now.getHours()).padStart(2, "0");
    const min = String(now.getMinutes()).padStart(2, "0");
    const ss = String(now.getSeconds()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
  }

  startRingLive(cam, video, overlay, liveBtn) {
    const record = this.streamPlayers.get(cam.id);
    if (!record) return;
    if (record.snapshotPoll) {
      clearInterval(record.snapshotPoll);
      record.snapshotPoll = null;
      if (liveBtn) {
        liveBtn.disabled = false;
        liveBtn.textContent = "▶ Start Live";
      }
      if (overlay) {
        overlay.style.display = "flex";
        const snapUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);
        overlay.style.backgroundImage = `url("${snapUrl}")`;
      }
      if (video) video.style.display = "none";
      return;
    }
    if (liveBtn) {
      liveBtn.disabled = false;
      liveBtn.textContent = "⏹ Stop";
    }
    if (video) video.style.display = "none";
    if (overlay) overlay.style.display = "flex";
    const poll = () => {
      if (!overlay) return;
      const snapUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);
      overlay.style.backgroundImage = `url("${snapUrl}")`;
    };
    poll();
    record.snapshotPoll = setInterval(poll, 1000);
  }

  async startTileLiveWebRTC(cam, video, overlay, streamSrc = null) {
    const record = this.streamPlayers.get(cam.id);
    if (!record) return;

    const liveBtn = overlay ? overlay.querySelector(".tile-live-btn") : null;
    if (liveBtn) {
      liveBtn.disabled = true;
      liveBtn.textContent = "⏳ Connecting...";
    }

    // Ensure snapshot overlay is visible initially during WebRTC negotiation
    if (overlay) {
      overlay.style.display = "flex";
      const snapUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);
      overlay.style.backgroundImage = `url("${snapUrl}")`;
    }

    const restoreSnapshotFallback = (reason) => {
      if (record) record.isLive = false;
      const why = typeof reason === "string" ? reason : "unknown";
      console.warn(`Live tile ${cam.id} fell back to snapshots: ${why}`);
      if (overlay) overlay.dataset.liveFallbackReason = why;
      if (video) video.dataset.liveFallbackReason = why;
      if (record.clockInterval) {
        clearInterval(record.clockInterval);
        record.clockInterval = null;
      }
      if (record.clockBadge) {
        record.clockBadge.style.display = "none";
      }
      if (liveBtn) {
        liveBtn.disabled = false;
        liveBtn.textContent = "▶ Start Live";
      }
      if (video) video.style.display = "none";
      if (overlay) {
        overlay.style.display = "flex";
        const snapUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);
        overlay.style.backgroundImage = `url("${snapUrl}")`;
        if (record.snapshotPoll) clearInterval(record.snapshotPoll);
        record.snapshotPoll = setInterval(() => {
          if (!overlay) return;
          const nextUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);
          overlay.style.backgroundImage = `url("${nextUrl}")`;
        }, 2000);
      }
    };

    const showClockIfNeeded = () => {
      const shouldShow =
        cam.timestamp_overlay !== false &&
        (cam.provider_badge === "ring" ||
          cam.camera_type === "ring" ||
          cam.timestamp_overlay === true);
      if (shouldShow && record.clockBadge && record.clockSpan && !record.clockInterval) {
        record.clockSpan.textContent = this._formatCurrentClock();
        record.clockBadge.style.display = "inline-flex";
        record.clockInterval = setInterval(() => {
          record.clockSpan.textContent = this._formatCurrentClock();
        }, 1000);
      }
    };

    if (video) {
      const hideOverlayWhenReady = () => {
        if (!video || (video.readyState < 2 && video.currentTime === 0)) return;
        if (record && record.snapshotPoll) {
          clearInterval(record.snapshotPoll);
          record.snapshotPoll = null;
        }
        if (overlay) overlay.style.display = "none";
        if (liveBtn) {
          liveBtn.disabled = false;
          liveBtn.textContent = "▶ Start Live";
        }
        if (record) record.isLive = true;
        showClockIfNeeded();
      };
      video.onplaying = hideOverlayWhenReady;
      video.onloadeddata = hideOverlayWhenReady;
      video.ontimeupdate = () => {
        if (video && video.currentTime > 0) {
          hideOverlayWhenReady();
        }
      };
      video.onerror = () => restoreSnapshotFallback("video.onerror");
    }

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
        video.play().catch(() => {});
        video.style.display = "block";
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          restoreSnapshotFallback(`pc ${pc.connectionState}`);
        }
      };

      const videoTransceiver = pc.addTransceiver("video", { direction: "recvonly" });
      try {
        const caps = window.RTCRtpReceiver && RTCRtpReceiver.getCapabilities
          ? RTCRtpReceiver.getCapabilities("video")
          : null;
        if (caps && caps.codecs && videoTransceiver.setCodecPreferences) {
          const h264 = caps.codecs.filter((c) => /H264/i.test(c.mimeType));
          if (h264.length > 0) {
            const rest = caps.codecs.filter((c) => !/H264/i.test(c.mimeType));
            videoTransceiver.setCodecPreferences([...h264, ...rest]);
          }
        }
      } catch (codecErr) {
        console.warn("H264 codec preference unavailable:", codecErr);
      }

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Prefer substream if available to conserve bandwidth
      const streamId = streamSrc || cam.id;
      const wsUrl = resolveWsUrl(`/api/ws?src=${encodeURIComponent(streamId)}`);

      const ws = new WebSocket(wsUrl);
      record.ws = ws;

      let timedOut = false;
      const timeoutId = setTimeout(() => {
        timedOut = true;
        try { ws.close(); } catch {}
        restoreSnapshotFallback("15s timeout");
      }, 15000);

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
            clearTimeout(timeoutId);
            while (iceCandidates.length > 0) {
              const c = iceCandidates.shift();
              await pc.addIceCandidate(c).catch(() => {});
            }
          } else if (data.type === "webrtc/candidate") {
            if (!data.value || (typeof data.value === "string" && !data.value.trim())) return;
            try {
              const candidateInit =
                typeof data.value === "object" && data.value !== null
                  ? data.value
                  : {
                      candidate: data.value,
                      sdpMid:
                        data.sdpMid !== undefined && data.sdpMid !== null ? String(data.sdpMid) : "0",
                      sdpMLineIndex:
                        data.sdpMLineIndex !== undefined && data.sdpMLineIndex !== null
                          ? Number(data.sdpMLineIndex)
                          : 0,
                    };
              const candidate = new RTCIceCandidate(candidateInit);
              if (!hasRemote) {
                iceCandidates.push(candidate);
              } else {
                await pc.addIceCandidate(candidate).catch(() => {});
              }
            } catch {}
          }
        } catch {}
      };

      pc.onicecandidate = (iceEvent) => {
        if (iceEvent.candidate && ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({
              type: "webrtc/candidate",
              value: iceEvent.candidate.candidate,
            })
          );
        }
      };

      ws.onerror = (err) => {
        clearTimeout(timeoutId);
        restoreSnapshotFallback("ws error");
      };

      ws.onclose = () => {
        clearTimeout(timeoutId);
        if (!hasRemote && !timedOut) restoreSnapshotFallback("ws close");
      };

      pc.onicecandidate = (e) => {
        if (e.candidate && ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({
              type: "webrtc/candidate",
              value: e.candidate.candidate,
              sdpMid: e.candidate.sdpMid,
              sdpMLineIndex: e.candidate.sdpMLineIndex,
            })
          );
        }
      };

      // Do not claim live success until media actually plays (H265/WebRTC may fail).
    } catch (err) {
      console.warn(`Live Grid tile connection failed for ${cam.id}:`, err);
      restoreSnapshotFallback("exception");
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

  updateCameraSnapshot(cameraId, snapshotUri = null) {
    const record = this.streamPlayers.get(cameraId);
    if (!record) return;
    const rawUrl = snapshotUri || `/api/cameras/${encodeURIComponent(cameraId)}/snapshot?t=${Date.now()}`;
    const snapUrl = resolveUrl(rawUrl.includes("?") ? rawUrl : `${rawUrl}?t=${Date.now()}`);
    if (record.video) {
      record.video.poster = snapUrl;
    }
    if (record.overlay && !record.isLive) {
      record.overlay.style.backgroundImage = `url("${snapUrl}")`;
    }
  }

  cleanup() {
    this.streamPlayers.forEach((record) => {
      if (record.clockInterval) {
        clearInterval(record.clockInterval);
        record.clockInterval = null;
      }
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
