/**
 * Resilient, cross-browser WebRTC, MSE, and Snapshot Live Player component.
 */
import { resolveUrl } from "../utils/url.js";

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
  }

  setCamera(camera) {
    this.stopLive();
    this.activeCamera = camera;
    this.showSnapshotFallback();
  }

  showSnapshotFallback() {
    this.isLive = false;
    if (this.video) {
      this.video.style.display = "none";
      this.video.srcObject = null;
    }
    if (this.overlay && this.activeCamera) {
      this.overlay.style.display = "flex";
      const snapUrl = resolveUrl(
        `/api/cameras/${encodeURIComponent(this.activeCamera.id)}/snapshot?t=${Date.now()}`
      );
      this.overlay.style.backgroundImage = `url("${snapUrl}")`;
    }
    if (this.onLiveStateChange) {
      this.onLiveStateChange(false);
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

  async startLive() {
    if (!this.activeCamera) return;

    this.stopSnapshotPolling();

    if (
      this.activeCamera &&
      this.activeCamera.timestamp_overlay !== false &&
      (this.activeCamera.provider_badge === "ring" ||
        this.activeCamera.camera_type === "ring" ||
        this.activeCamera.timestamp_overlay === true)
    ) {
      this.startDurationTimer();
    }

    // Keep snapshot overlay visible while WebRTC is negotiating to prevent black screen
    if (this.overlay) {
      this.overlay.style.display = "flex";
    }
    if (this.video) {
      this.video.style.display = "block";
      const hideOverlayWhenReady = () => {
        if (this.overlay) {
          this.overlay.style.display = "none";
        }
        if (
          this.activeCamera &&
          this.activeCamera.timestamp_overlay !== false &&
          (this.activeCamera.provider_badge === "ring" ||
            this.activeCamera.camera_type === "ring" ||
            this.activeCamera.timestamp_overlay === true)
        ) {
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
        this.showSnapshotFallback();
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
          this.showSnapshotFallback();
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
        this.video.muted = true;
        this.video.play().catch(() => {});
      };

      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Determine WebSocket endpoint
      const wsUrl = resolveWsUrl(`/api/ws?src=${encodeURIComponent(this.activeCamera.id)}`);

      await new Promise((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        this.websocket = ws;

        const timeoutId = setTimeout(() => {
          try { ws.close(); } catch {}
          if (this.overlay) this.overlay.style.display = "flex";
          this.showSnapshotFallback();
          reject(new Error("WebRTC WebSocket signaling timeout"));
        }, 8000);

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
          this.showSnapshotFallback();
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
      this.showSnapshotFallback();
      await this._attemptFallbackStream();
    }
  }

  async _attemptFallbackStream() {
    // 2. Secondary fallback: MSE / MP4 fragmented stream
    try {
      if (this.video && this.activeCamera) {
        const mp4Url = resolveUrl(`/api/stream.mp4?src=${encodeURIComponent(this.activeCamera.id)}`);
        this.video.src = mp4Url;
        this.video.muted = true;
        await this.video.play();
        this.isLive = true;
        if (this.onLiveStateChange) {
          this.onLiveStateChange(true);
        }
        return;
      }
    } catch (e) {
      console.warn("MSE/MP4 live fallback failed:", e);
    }

    // 3. Quaternary fallback: Rapid snapshot polling
    this.showSnapshotFallback();
    this.startSnapshotPolling(2000);
  }

  stopLive() {
    this.stopSnapshotPolling();
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

  startDurationTimer() {
    this.stopDurationTimer();
    if (!this.durationBadge || !this.durationTimer) return;
    this.sessionStartTime = Date.now();
    this.durationTimer.textContent = "00:00:00";
    this.durationBadge.style.display = "inline-flex";

    this.durationInterval = setInterval(() => {
      const elapsedSec = Math.floor((Date.now() - this.sessionStartTime) / 1000);
      const hrs = Math.floor(elapsedSec / 3600);
      const mins = Math.floor((elapsedSec % 3600) / 60);
      const secs = elapsedSec % 60;
      this.durationTimer.textContent = `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
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
