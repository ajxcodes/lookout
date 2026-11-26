/**
 * Resilient, cross-browser WebRTC, MSE, and Snapshot Live Player component.
 */
import { resolveUrl } from "../utils/url.js";
import {
  isLiveHttpFailure,
  LIVE_MAX_ATTEMPTS,
  LIVE_MEDIA_READY_TIMEOUT_MS,
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

export class LivePlayer {
  constructor({ videoElement, overlayElement, onLiveStateChange }) {
    this.video = videoElement;
    this.overlay = overlayElement;
    this.startBtn = document.getElementById("start-live-btn");
    this.onLiveStateChange = onLiveStateChange;
    this.peerConnection = null;
    this.websocket = null;
    this.activeCamera = null;
    this.isLive = false;
    this.iceCandidateQueue = [];
    this.hasRemoteDescription = false;
    this.snapshotInterval = null;
    this.durationBadge = document.getElementById("live-duration-badge");
    this.durationTimer = document.getElementById("live-duration-timer");
    this.sessionStartTime = null;
    this.durationInterval = null;
    // Pause/mute act on the existing video element only. WebRTC is never torn
    // down, so resuming is instant and the connection keeps its state.
    this.paused = false;
    this.muted = true; // live tiles/players start muted

    // Bounded live-start retry / graceful degradation state. At most
    // LIVE_MAX_ATTEMPTS automatic attempts run; after that the Start Live
    // button becomes an explicit "Retry live" control.
    this.liveAttempts = 0;
    this.liveRetryTimer = null;
    this.mediaReadyTimer = null;
    this.liveFailureHandled = false;
    this.liveUnavailable = false;
    this.unavailableStatusEl = document.getElementById("live-unavailable-status");
  }

  setCamera(camera) {
    this.stopLive();
    this.activeCamera = camera;
    this.liveAttempts = 0;
    this.showSnapshotFallback();
  }

  _resetStartButton() {
    if (this.startBtn) {
      this.startBtn.disabled = false;
      this.startBtn.textContent = "▶ Start Live Stream";
      this.startBtn.setAttribute("aria-label", "Start live stream");
      this.startBtn.title = "Start live stream";
    }
  }

  _clearRetryTimer() {
    if (this.liveRetryTimer) {
      clearTimeout(this.liveRetryTimer);
      this.liveRetryTimer = null;
    }
  }

  _clearMediaReadyTimer() {
    if (this.mediaReadyTimer) {
      clearTimeout(this.mediaReadyTimer);
      this.mediaReadyTimer = null;
    }
  }

  _mediaReady() {
    return Boolean(
      this.video &&
        (this.video.videoWidth > 0 ||
          (this.video.readyState >= 2 && this.video.currentTime > 0))
    );
  }

  /** True only when a live video with real dimensions is attached. Pause and
   *  mute buttons use this to avoid pretending to act on a snapshot. */
  hasPlayableMedia() {
    return Boolean(this.isLive && this._mediaReady());
  }

  _ensureUnavailableStatus() {
    if (!this.overlay) return null;
    let el = this.unavailableStatusEl;
    if (!el || !el.isConnected || !this.overlay.contains(el)) {
      el = document.createElement("div");
      el.className = "live-unavailable-status";
      el.id = "live-unavailable-status";
      el.setAttribute("role", "status");
      this.overlay.appendChild(el);
      this.unavailableStatusEl = el;
    }
    return el;
  }

  _showUnavailableStatus(reason) {
    const el = this._ensureUnavailableStatus();
    if (!el) return;
    el.textContent = liveUnavailableMessage(this.activeCamera, reason);
    el.style.display = "block";
    if (this.overlay) this.overlay.dataset.liveUnavailable = "true";
  }

  _hideUnavailableStatus() {
    if (this.unavailableStatusEl) this.unavailableStatusEl.style.display = "none";
    if (this.overlay) this.overlay.dataset.liveUnavailable = "false";
  }

  _shouldShowTimestampOverlay() {
    if (!this.activeCamera) return false;
    if (this.activeCamera.timestamp_overlay === false) return false;
    return (
      this.activeCamera.provider_badge === "ring" ||
      this.activeCamera.camera_type === "ring" ||
      this.activeCamera.timestamp_overlay === true
    );
  }

  /**
   * Enter the graceful snapshot fallback. When called with a failure reason it
   * exposes the "Live unavailable — showing snapshot" status, flips the start
   * button into a Retry live control, and schedules at most LIVE_MAX_ATTEMPTS
   * bounded automatic retries (increasing delay) for stream 5xx / no-media.
   */
  showSnapshotFallback(reason = "", { scheduleRetry = true } = {}) {
    if (reason && this.liveFailureHandled && this.liveUnavailable && this._lastFailureReason === reason) {
      return;
    }
    if (reason) {
      this.liveFailureHandled = true;
      this._lastFailureReason = reason;
    }

    this.isLive = false;
    this.liveUnavailable = Boolean(reason);
    this.stopDurationTimer();
    this._clearMediaReadyTimer();
    this._resetStartButton();
    if (reason) {
      const delay = retryDelayForAttempt(this.liveAttempts);
      this.startBtn.disabled = false;
      this.startBtn.textContent = "↻ Retry live";
      this.startBtn.setAttribute("aria-label", "Retry live stream");
      this.startBtn.title = "Live unavailable — retry the live stream";
      if (scheduleRetry && delay !== null) {
        this._clearRetryTimer();
        this.liveRetryTimer = setTimeout(() => {
          this.liveRetryTimer = null;
          this.startLive({ automatic: true });
        }, delay);
      }
      this._showUnavailableStatus(reason);
    } else {
      this._hideUnavailableStatus();
    }
    if (this.video) {
      this.video.style.display = "none";
      this.video.srcObject = null;
      this.video.pause();
    }
    if (this.overlay && this.activeCamera) {
      this.overlay.style.display = "flex";
      const snapUrl = resolveUrl(
        `/api/cameras/${encodeURIComponent(this.activeCamera.id)}/snapshot?t=${Date.now()}`
      );
      this.overlay.style.backgroundImage = `url("${snapUrl}")`;
    }
    if (this.onLiveStateChange) {
      this.onLiveStateChange(false, { unavailable: this.liveUnavailable, reason });
    }
  }

  startSnapshotPolling(intervalMs = 3000) {
    if (this.snapshotInterval) clearInterval(this.snapshotInterval);
    this.snapshotInterval = setInterval(() => {
      if (!this.isLive && this.activeCamera && this.overlay) {
        const snapUrl = resolveUrl(
          `/api/cameras/${encodeURIComponent(this.activeCamera.id)}/snapshot?t=${Date.now()}`
        );
        this.overlay.style.backgroundImage = `url("${snapUrl}")`;
      }
    }, intervalMs);
  }

  stopSnapshotPolling() {
    if (this.snapshotInterval) {
      clearInterval(this.snapshotInterval);
      this.snapshotInterval = null;
    }
  }

  updateCameraSnapshot(cameraId, snapshotUri = null) {
    if (!this.activeCamera || this.activeCamera.id !== cameraId) return;
    const rawUrl = snapshotUri || `/api/cameras/${encodeURIComponent(cameraId)}/snapshot?t=${Date.now()}`;
    const snapUrl = resolveUrl(rawUrl.includes("?") ? rawUrl : `${rawUrl}?t=${Date.now()}`);
    if (this.video) {
      this.video.poster = snapUrl;
    }
    if (this.overlay && !this.isLive) {
      this.overlay.style.backgroundImage = `url("${snapUrl}")`;
    }
  }

  async startLive({ automatic = false } = {}) {
    if (!this.activeCamera) return;
    if (automatic && this.liveAttempts >= LIVE_MAX_ATTEMPTS) return;

    if (!automatic) {
      this.liveAttempts = 0; // an explicit Start/Retry click gets a fresh budget
    }
    this.liveAttempts += 1;
    this.liveFailureHandled = false;
    this.liveUnavailable = false;
    this._clearRetryTimer();
    this._clearMediaReadyTimer();
    this._hideUnavailableStatus();

    this.stopSnapshotPolling();
    this.stopDurationTimer();

    if (this.startBtn) {
      this.startBtn.disabled = true;
      this.startBtn.textContent = "⏳ Connecting Live Stream...";
    }

    // A successful live path is unchanged; this timer only catches attempts
    // that never deliver a frame (videoWidth stays 0 on Ring/Wyze failures).
    this.mediaReadyTimer = setTimeout(() => {
      if (!this._mediaReady()) {
        this.showSnapshotFallback("no playable video within 8s");
      }
    }, LIVE_MEDIA_READY_TIMEOUT_MS);

    // Keep snapshot overlay visible while WebRTC is negotiating to prevent black screen
    if (this.overlay) {
      this.overlay.style.display = "flex";
    }
    if (this.video) {
      this.video.style.display = "block";
      const hideOverlayWhenReady = () => {
        if (!this.video || (this.video.readyState < 2 && this.video.currentTime === 0)) {
          return;
        }
        if (this.overlay) {
          this.overlay.style.display = "none";
        }
        this._clearRetryTimer();
        this._clearMediaReadyTimer();
        this.liveAttempts = 0;
        this.liveUnavailable = false;
        this._hideUnavailableStatus();
        this._resetStartButton();
        if (this._shouldShowTimestampOverlay() && !this.durationInterval) {
          this.startDurationTimer();
        }
      };
      this.video.onplaying = hideOverlayWhenReady;
      this.video.onloadeddata = hideOverlayWhenReady;
      this.video.ontimeupdate = () => {
        if (this.video && this.video.currentTime > 0) {
          hideOverlayWhenReady();
        }
      };
      this.video.onerror = () => {
        if (this.overlay) this.overlay.style.display = "flex";
        this.showSnapshotFallback("video error");
      };
    }

    this.iceCandidateQueue = [];
    this.hasRemoteDescription = false;

    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      this.peerConnection = pc;

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          if (this.video && this.video.currentTime > 0) {
            if (this.overlay) this.overlay.style.display = "none";
          }
        } else if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          if (this.overlay) this.overlay.style.display = "flex";
          this.showSnapshotFallback(`WebRTC ${pc.connectionState}`);
        }
      };

      // Cross-browser track attachment: handles Chrome/Edge/Safari missing a=msid
      pc.ontrack = (event) => {
        if (!this.video) return;
        if (event.streams && event.streams[0]) {
          this.video.srcObject = event.streams[0];
        } else {
          if (!this.video.srcObject) {
            this.video.srcObject = new MediaStream();
          }
          this.video.srcObject.addTrack(event.track);
        }
        this.video.muted = this.muted;
        if (!this.paused) {
          this.video.play().catch(() => {});
        }
      };

      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Focus view uses the HD variant for Wyze cameras when provided; snapshots
      // below keep using the base camera id so stills stay on the configured source.
      const streamId = this.activeCamera.focus_stream_id || this.activeCamera.id;
      const wsUrl = resolveWsUrl(`/api/ws?src=${encodeURIComponent(streamId)}`);

      await new Promise((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        this.websocket = ws;

        const timeoutId = setTimeout(() => {
          try { ws.close(); } catch {}
          if (this.overlay) this.overlay.style.display = "flex";
          this.showSnapshotFallback("WebRTC signaling timeout");
          reject(new Error("WebRTC WebSocket signaling timeout"));
        }, 15000);

        ws.onopen = () => {
          ws.send(JSON.stringify({ type: "webrtc/offer", value: offer.sdp }));
        };

        ws.onmessage = async (msgEvent) => {
          try {
            const data = JSON.parse(msgEvent.data);
            if (data.type === "webrtc/answer") {
              await pc.setRemoteDescription({ type: "answer", sdp: data.value });
              this.hasRemoteDescription = true;

              // Flush buffered trickle ICE candidates to avoid InvalidStateError in Chromium / Safari
              while (this.iceCandidateQueue.length > 0) {
                const candidate = this.iceCandidateQueue.shift();
                await pc.addIceCandidate(candidate).catch(() => {});
              }
              clearTimeout(timeoutId);
              resolve();
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
                if (!this.hasRemoteDescription) {
                  this.iceCandidateQueue.push(candidate);
                } else {
                  await pc.addIceCandidate(candidate).catch(() => {});
                }
              } catch (candErr) {
                console.warn("Could not add ICE candidate:", candErr);
              }
            }
          } catch (e) {
            console.warn("Error processing WebRTC signaling message:", e);
          }
        };

        ws.onerror = (err) => {
          clearTimeout(timeoutId);
          if (this.overlay) this.overlay.style.display = "flex";
          this.showSnapshotFallback("WebRTC signaling error");
          reject(err);
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
      });

      this.isLive = true;
      if (this.onLiveStateChange) {
        this.onLiveStateChange(true);
      }
    } catch (err) {
      console.warn("WebRTC primary live connection failed, attempting fallback:", err);
      if (this.overlay) this.overlay.style.display = "flex";
      // Do not schedule the retry yet: the MP4 fallback below is still allowed
      // to succeed. Its failure (or a dead media element) schedules the retry.
      this.showSnapshotFallback("WebRTC connection failed", { scheduleRetry: false });
      await this._attemptFallbackStream();
    }
  }

  async _attemptFallbackStream() {
    // 2. Secondary fallback: MSE / MP4 fragmented stream
    try {
      if (this.video && this.activeCamera) {
        const mp4Url = resolveUrl(`/api/stream.mp4?src=${encodeURIComponent(this.activeCamera.id)}`);
        // Detect a 502/5xx stream response explicitly instead of letting the
        // media element retry the same bad URL repeatedly.
        try {
          const probe = await fetch(mp4Url, { method: "HEAD" });
          if (isLiveHttpFailure(probe.status)) {
            this.showSnapshotFallback(`stream HTTP ${probe.status}`);
            this.startSnapshotPolling(SNAPSHOT_FALLBACK_POLL_MS);
            return;
          }
        } catch {
          // CORS / network probe failure: the media element's error event is
          // the authoritative fallback signal in that case.
        }
        this.video.src = mp4Url;
        this.video.muted = this.muted;
        if (!this.paused) {
          await this.video.play();
        }
        this.isLive = true;
        if (this.onLiveStateChange) {
          this.onLiveStateChange(true);
        }
        return;
      }
    } catch (e) {
      console.warn("MSE/MP4 live fallback failed:", e);
    }

    // 3. Quaternary fallback: bounded-snapshot polling (no more than once/2s)
    this.showSnapshotFallback("stream unavailable");
    this.startSnapshotPolling(SNAPSHOT_FALLBACK_POLL_MS);
  }

  stopLive() {
    this.stopSnapshotPolling();
    this._resetStartButton();
    this._clearRetryTimer();
    this._clearMediaReadyTimer();
    this.liveFailureHandled = false;
    this.liveUnavailable = false;
    this._hideUnavailableStatus();
    if (this.websocket) {
      try {
        this.websocket.close();
      } catch {}
      this.websocket = null;
    }
    if (this.peerConnection) {
      this.peerConnection.close();
      this.peerConnection = null;
    }
    if (this.video) {
      this.video.srcObject = null;
      this.video.removeAttribute("src");
    }
    this.stopDurationTimer();
    this.hasRemoteDescription = false;
    this.iceCandidateQueue = [];
    this.isLive = false;
  }

  /** Freeze/resume the currently attached video without closing the peer
   *  connection. Safe to call before a stream is live. */
  setPaused(paused) {
    this.paused = Boolean(paused);
    if (!this.video) return;
    if (this.paused) {
      this.video.pause();
    } else if (this.isLive || this.video.srcObject || this.video.src) {
      this.video.play().catch(() => {});
    }
  }

  /** Mute state lives on the media element and is remembered per active
   *  camera by the app; no stream renegotiation is involved. */
  setMuted(muted) {
    this.muted = Boolean(muted);
    if (this.video) this.video.muted = this.muted;
  }

  _formatCurrentClock() {
    return formatClock(new Date());
  }

  startDurationTimer() {
    if (this.durationInterval) return;
    if (!this.durationBadge || !this.durationTimer) return;
    this.sessionStartTime = Date.now();
    this.durationTimer.textContent = this._formatCurrentClock();
    this.durationBadge.title = formatLocalClockTitle(new Date());
    this.durationBadge.style.display = "inline-flex";

    this.durationInterval = setInterval(() => {
      this.durationTimer.textContent = this._formatCurrentClock();
    }, 1000);
  }

  stopDurationTimer() {
    if (this.durationInterval) {
      clearInterval(this.durationInterval);
      this.durationInterval = null;
    }
    if (this.durationBadge) {
      this.durationBadge.style.display = "none";
    }
  }
}
