/**
 * Multi-camera Live Grid View component with density selector and lifecycle governance.
 */
import { infer_camera_icon } from "./switcher.js";
import { resolveUrl } from "../utils/url.js";
import { buildCameraManagementControls } from "./camera-management.js";
import {
  LIVE_MAX_ATTEMPTS,
  LIVE_MEDIA_READY_TIMEOUT_MS,
  liveUnavailableControlTitle,
  liveUnavailableMessage,
  retryDelayForAttempt,
  SNAPSHOT_FALLBACK_POLL_MS,
} from "../utils/live-health.js";
import { formatClock, formatLocalClockTitle } from "../utils/timezone.js";

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

// camera-gear-btn moved to focus view per AC20
export class LiveGrid {
  constructor({
    container,
    densitySelector,
    onFocusCamera,
    onRemoveCamera,
    onReauthenticateCamera,
    cameraManagementLabels,
    onLiveStateChange,
  }) {
    this.container = container;
    this.densitySelector = densitySelector;
    this.onFocusCamera = onFocusCamera;
    // AC10: management bar callbacks are injected by the app so the controls
    // are built directly into each rendered tile (no fragile post-hoc selector).
    this.onRemoveCamera = onRemoveCamera;
    this.onReauthenticateCamera = onReauthenticateCamera;
    this.cameraManagementLabels = cameraManagementLabels;
    // Notified when a tile gains or loses playable media so the header's
    // global pause button can disable itself when no tile can be paused.
    this.onLiveStateChange = onLiveStateChange || null;
    this.cameras = [];
    // Close PTZ <details> when clicking outside (critique suggestion).
    if (!LiveGrid._ptzOutsideBound) {
      LiveGrid._ptzOutsideBound = true;
      document.addEventListener("click", (e) => {
        document.querySelectorAll("details.ptz-controls[open]").forEach((d) => {
          if (!d.contains(e.target)) d.open = false;
        });
      });
    }
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
    // Session-only playback state. Effective paused = globalPaused
    //   ? !resumedSet.has(id)
    //   : explicitlyPausedSet.has(id).
    // A global toggle clears both override sets; a per-tile resume while
    // globally paused exempts only that camera. No persistence, no renegotiation.
    this.globalPaused = false;
    this.explicitlyPausedSet = new Set();
    this.resumedSet = new Set();
    // Tiles start muted; the map records only user changes (memory only).
    this.cameraMutedState = new Map();

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
      tile.tabIndex = 0;
      tile.setAttribute("aria-label", `${cam.name || cam.id} live tile (Space to pause or resume)`);

      const icon = cam.icon || infer_camera_icon(cam.name || cam.location);
      const provider = (cam.provider_badge || (cam.camera_type === "ring" ? "ring" : (cam.camera_type === "wyze" ? "wyze" : "generic"))).toLowerCase();
      const streamMode = cam.stream_mode || (provider === "ring" ? "on_demand" : "live_rtc");

      const header = document.createElement("div");
      header.className = "tile-header";
      header.innerHTML = `
        <div class="tile-title">
          <span class="cam-icon" title="Camera icon (decorative)">${icon}</span>
          <span class="cam-name">${cam.name || cam.id}</span>
        </div>
        <div class="tile-badges">
          <span class="badge-provider ${provider}" title="Provider badge">${provider.charAt(0).toUpperCase() + provider.slice(1)}</span>
          <span class="badge-stream ${streamMode}" title="Stream mode badge">${streamMode === "live_rtc" ? "● Live RTC" : "● On-Demand"}</span>
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
      video.muted = this.isMuted(cam.id);
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
          // A user-initiated Retry live click always gets a fresh attempt budget.
          const clickRecord = this.streamPlayers.get(cam.id);
          if (clickRecord) clickRecord.liveAttempts = 0;
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

      // Per-tile playback controls. Visible on hover/tap/focus (CSS handles
      // hover/focus); a tap on the video surface toggles them. The buttons
      // act on their own stream and never hide the overlay.
      const streamControls = document.createElement("div");
      streamControls.className = "tile-stream-controls";
      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.className = "tile-stream-btn tile-play-btn";
      playBtn.textContent = "⏸";
      playBtn.setAttribute("aria-label", "Pause stream");
      playBtn.title = "Pause stream";
      playBtn.setAttribute("aria-pressed", "false");
      const muteBtn = document.createElement("button");
      muteBtn.type = "button";
      muteBtn.className = "tile-stream-btn tile-mute-btn";
      muteBtn.textContent = "🔇";
      muteBtn.setAttribute("aria-label", "Mute stream");
      muteBtn.title = "Mute stream";
      muteBtn.setAttribute("aria-pressed", "true");
      streamControls.appendChild(playBtn);
      streamControls.appendChild(muteBtn);
      playBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.setPaused(cam.id, !this.isPaused(cam.id));
        this._revealTileControls(cam.id, false);
        this._syncTileControls(cam.id);
      });
      muteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.setMuted(cam.id, !this.isMuted(cam.id));
        this._revealTileControls(cam.id, false);
      });
      stage.addEventListener("click", (e) => {
        if (e.target.closest(".tile-stream-controls, .tile-live-btn")) return;
        e.stopPropagation();
        const record = this.streamPlayers.get(cam.id);
        if (record && record.controlsVisible) {
          record.controlsVisible = false;
          if (record.controlsHideTimer) {
            clearTimeout(record.controlsHideTimer);
            record.controlsHideTimer = null;
          }
          streamControls.classList.remove("controls-visible");
        } else {
          this._revealTileControls(cam.id, true);
        }
      });
      stage.appendChild(streamControls);

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
        ptzDetails.open = true;
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
        stage,
        clockBadge,
        clockSpan,
        clockInterval: null,
        pc: null,
        ws: null,
        isLive: false,
        streamMode,
        streamControls,
        playBtn,
        muteBtn,
        controlsVisible: false,
        controlsHideTimer: null,
        liveAttempts: 0,
        liveRetryTimer: null,
        mediaReadyTimer: null,
        liveFailureHandled: false,
        lastLiveFailureReason: "",
        liveUnavailable: false,
        liveStatus: null,
      });
      // Keep re-rendered tiles consistent with the session pause/mute state.
      this._syncTileControls(cam.id);
      stage.dataset.paused = String(this.isPaused(cam.id));

      // Auto-start continuous mains streams
      if (streamMode === "live_rtc" && this.isTabVisible) {
        this.startTileLiveWebRTC(cam, video, overlay, null, { automatic: true });
      }
    });
  }

  /** Clear a tile's bounded-retry and no-media timers. */
  _clearLiveTimers(record) {
    if (!record) return;
    if (record.liveRetryTimer) {
      clearTimeout(record.liveRetryTimer);
      record.liveRetryTimer = null;
    }
    if (record.mediaReadyTimer) {
      clearTimeout(record.mediaReadyTimer);
      record.mediaReadyTimer = null;
    }
  }

  /** A tile counts as playable only when real video frames arrived. */
  _hasPlayableMedia(cameraId) {
    const record = this.streamPlayers.get(cameraId);
    if (!record || !record.video || !record.isLive) return false;
    return record.video.videoWidth > 0 || record.video.currentTime > 0;
  }

  hasPlayableCamera() {
    for (const cameraId of this.streamPlayers.keys()) {
      if (this._hasPlayableMedia(cameraId)) return true;
    }
    return false;
  }

  _notifyLiveState() {
    if (this.onLiveStateChange) this.onLiveStateChange(this.hasPlayableCamera());
  }

  /** Render the user-facing "Live unavailable — showing snapshot" status. */
  _setLiveStatus(cam, record, reason, overlay, liveBtn) {
    if (!overlay) return;
    let el = record.liveStatus;
    if (!el || !el.isConnected || !overlay.contains(el)) {
      el = document.createElement("div");
      el.className = "tile-live-status";
      el.setAttribute("role", "status");
      overlay.appendChild(el);
      record.liveStatus = el;
    }
    const delay = retryDelayForAttempt(record.liveAttempts || 0);
    el.textContent = liveUnavailableMessage(cam, reason);
    el.style.display = "block";
    overlay.dataset.liveUnavailable = "true";
    if (liveBtn) {
      liveBtn.disabled = false;
      liveBtn.textContent = "↻ Retry live";
      liveBtn.setAttribute("aria-label", `Retry live stream for ${cam.name || cam.id}`);
      liveBtn.title = "Live unavailable — retry the live stream";
      if (delay !== null) {
        liveBtn.dataset.retryPending = "true";
      } else {
        delete liveBtn.dataset.retryPending;
      }
    }
  }

  _hideLiveStatus(record, overlay) {
    if (record && record.liveStatus) record.liveStatus.style.display = "none";
    if (overlay) overlay.dataset.liveUnavailable = "false";
  }

  _formatCurrentClock() {
    return formatClock(new Date());
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
    record.snapshotPoll = setInterval(poll, SNAPSHOT_FALLBACK_POLL_MS);
  }

  async startTileLiveWebRTC(cam, video, overlay, streamSrc = null, { automatic = false } = {}) {
    const record = this.streamPlayers.get(cam.id);
    if (!record) return;
    // Automatic retries stop after the bounded budget; only a user-initiated
    // Retry live click (or a fresh render) can start another sequence.
    if (automatic && (record.liveAttempts || 0) >= LIVE_MAX_ATTEMPTS) return;

    if (record.liveAttempts === undefined) record.liveAttempts = 0;
    if (!automatic) record.liveAttempts = 0;
    record.liveAttempts += 1;
    record.liveFailureHandled = false;
    record.liveUnavailable = false;
    this._clearLiveTimers(record);
    this._hideLiveStatus(record, overlay);

    const liveBtn = overlay ? overlay.querySelector(".tile-live-btn") : null;
    if (liveBtn) {
      liveBtn.disabled = true;
      liveBtn.textContent = "⏳ Connecting...";
    }

    // Catch attempts that negotiate but never deliver a frame (videoWidth=0).
    record.mediaReadyTimer = setTimeout(() => {
      if (!this._hasPlayableMedia(cam.id)) {
        restoreSnapshotFallback("no playable video within 8s");
      }
    }, LIVE_MEDIA_READY_TIMEOUT_MS);

    // Ensure snapshot overlay is visible initially during WebRTC negotiation
    if (overlay) {
      overlay.style.display = "flex";
      const snapUrl = resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/snapshot?t=${Date.now()}`);
      overlay.style.backgroundImage = `url("${snapUrl}")`;
    }

    const restoreSnapshotFallback = (reason, { scheduleRetry = true } = {}) => {
      if (record) record.isLive = false;
      const why = typeof reason === "string" ? reason : "unknown";
      if (
        record.liveFailureHandled &&
        record.liveUnavailable &&
        record.lastLiveFailureReason === why
      ) {
        return;
      }
      record.liveFailureHandled = true;
      record.lastLiveFailureReason = why;
      record.liveUnavailable = true;
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
      this._clearLiveTimers(record);
      this._setLiveStatus(cam, record, why, overlay, liveBtn);
      if (scheduleRetry) {
        const delay = retryDelayForAttempt(record.liveAttempts || 0);
        if (delay !== null) {
          record.liveRetryTimer = setTimeout(() => {
            record.liveRetryTimer = null;
            this.startTileLiveWebRTC(cam, video, overlay, streamSrc, { automatic: true });
          }, delay);
        }
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
        }, SNAPSHOT_FALLBACK_POLL_MS);
      }
      this._syncTileControls(cam.id);
      this._notifyLiveState();
    };

    const showClockIfNeeded = () => {
      const shouldShow =
        cam.timestamp_overlay !== false &&
        (cam.provider_badge === "ring" ||
          cam.camera_type === "ring" ||
          cam.timestamp_overlay === true);
      if (shouldShow && record.clockBadge && record.clockSpan && !record.clockInterval) {
        record.clockSpan.textContent = this._formatCurrentClock();
        record.clockBadge.title = formatLocalClockTitle();
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
        this._clearLiveTimers(record);
        if (record) {
          record.isLive = true;
          record.liveAttempts = 0;
          record.liveUnavailable = false;
          record.liveFailureHandled = false;
          record.lastLiveFailureReason = "";
        }
        this._hideLiveStatus(record, overlay);
        if (overlay) overlay.style.display = "none";
        if (liveBtn) {
          liveBtn.disabled = false;
          liveBtn.textContent = "▶ Start Live";
          delete liveBtn.dataset.retryPending;
        }
        this._syncTileControls(cam.id);
        this._notifyLiveState();
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
        video.muted = this.isMuted(cam.id);
        if (!this.isPaused(cam.id)) {
          video.play().catch(() => {});
        }
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

  /** Effective paused state for one camera (global toggle + per-camera overrides).
   *  A tile without playable live media never reports a paused stream. */
  isPaused(cameraId) {
    if (!this._hasPlayableMedia(cameraId)) return false;
    return this.globalPaused
      ? !this.resumedSet.has(cameraId)
      : this.explicitlyPausedSet.has(cameraId);
  }

  /** Pause/resume one camera. While globally paused this creates/removes a
   *  single-camera resume exemption; otherwise it sets an explicit override.
   *  Only video.pause()/play() are used - the WebRTC connection stays open. */
  setPaused(cameraId, paused) {
    if (!this._hasPlayableMedia(cameraId)) return;
    if (this.globalPaused) {
      if (paused) this.resumedSet.delete(cameraId);
      else this.resumedSet.add(cameraId);
    } else if (paused) {
      this.explicitlyPausedSet.add(cameraId);
    } else {
      this.explicitlyPausedSet.delete(cameraId);
    }
    this._applyCameraPause(cameraId);
    this._syncTileControls(cameraId);
  }

  /** Global pause freezes every live tile. Toggling clears all overrides, so
   *  a global resume returns to the normal (all-playing) state. */
  setGlobalPaused(paused) {
    this.globalPaused = Boolean(paused);
    this.explicitlyPausedSet.clear();
    this.resumedSet.clear();
    this.streamPlayers.forEach((_record, cameraId) => {
      // The global toggle must not pretend to pause a snapshot-only tile.
      if (!this._hasPlayableMedia(cameraId)) {
        this._syncTileControls(cameraId);
        return;
      }
      this._applyCameraPause(cameraId);
      this._syncTileControls(cameraId);
    });
    this._notifyLiveState();
  }

  _applyCameraPause(cameraId) {
    const record = this.streamPlayers.get(cameraId);
    if (!record || !record.video || !this._hasPlayableMedia(cameraId)) return;
    if (this.isPaused(cameraId)) {
      record.video.pause();
    } else if (record.isLive && this.isTabVisible) {
      record.video.play().catch(() => {});
    }
    if (record.stage) {
      record.stage.dataset.paused = String(this.isPaused(cameraId));
    }
  }

  /** Tiles start muted; isMuted() reflects explicit user toggles only. */
  isMuted(cameraId) {
    return this.cameraMutedState.get(cameraId) !== false;
  }

  setMuted(cameraId, muted) {
    this.cameraMutedState.set(cameraId, Boolean(muted));
    const record = this.streamPlayers.get(cameraId);
    if (record && record.video) record.video.muted = Boolean(muted);
    this._syncTileControls(cameraId);
  }

  _syncTileControls(cameraId) {
    const record = this.streamPlayers.get(cameraId);
    if (!record) return;
    // Snapshot fallback has no media to pause or mute. Keep the controls
    // visible but explicitly disabled, with an explanatory tooltip.
    const playable = this._hasPlayableMedia(cameraId);
    const paused = this.isPaused(cameraId);
    const muted = this.isMuted(cameraId);
    const disabledTitle = liveUnavailableControlTitle();
    if (record.playBtn) {
      record.playBtn.textContent = paused ? "▶" : "⏸";
      const playLabel = paused ? "Resume stream" : "Pause stream";
      record.playBtn.setAttribute("aria-label", playable ? playLabel : disabledTitle);
      record.playBtn.title = playable ? playLabel : disabledTitle;
      record.playBtn.setAttribute("aria-pressed", String(paused));
      record.playBtn.setAttribute("aria-disabled", String(!playable));
      record.playBtn.disabled = !playable;
    }
    if (record.muteBtn) {
      record.muteBtn.textContent = muted ? "🔇" : "🔊";
      const muteLabel = muted ? "Unmute stream" : "Mute stream";
      record.muteBtn.setAttribute("aria-label", playable ? muteLabel : disabledTitle);
      record.muteBtn.title = playable ? muteLabel : disabledTitle;
      record.muteBtn.setAttribute("aria-pressed", String(muted));
      record.muteBtn.setAttribute("aria-disabled", String(!playable));
      record.muteBtn.disabled = !playable;
    }
  }

  _revealTileControls(cameraId, autoHide) {
    const record = this.streamPlayers.get(cameraId);
    if (!record || !record.streamControls) return;
    record.controlsVisible = true;
    record.streamControls.classList.add("controls-visible");
    if (record.controlsHideTimer) {
      clearTimeout(record.controlsHideTimer);
      record.controlsHideTimer = null;
    }
    if (autoHide) {
      record.controlsHideTimer = setTimeout(() => {
        record.controlsVisible = false;
        record.streamControls.classList.remove("controls-visible");
        record.controlsHideTimer = null;
      }, 3000);
    }
  }

  freezeAllStreams() {
    this.streamPlayers.forEach((record) => {
      if (record.video) record.video.pause();
    });
  }

  resumeVisibleStreams() {
    this.streamPlayers.forEach((record, cameraId) => {
      if (record.video && record.isLive && !this.isPaused(cameraId)) {
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
    if (
      record &&
      record.video &&
      record.isLive &&
      this.isTabVisible &&
      !this.isPaused(cameraId)
    ) {
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
      if (record.controlsHideTimer) {
        clearTimeout(record.controlsHideTimer);
        record.controlsHideTimer = null;
      }
      this._clearLiveTimers(record);
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
