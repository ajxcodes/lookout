/**
 * Dual-mode Live WebRTC and Snapshot player component.
 */
export class LivePlayer {
  constructor({ videoElement, overlayElement, onLiveStateChange }) {
    this.video = videoElement;
    this.overlay = overlayElement;
    this.onLiveStateChange = onLiveStateChange;
    this.peerConnection = null;
    this.activeCamera = null;
    this.isLive = false;
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
      const snapUrl = `/api/cameras/${encodeURIComponent(this.activeCamera.id)}/snapshot?t=${Date.now()}`;
      this.overlay.style.backgroundImage = `url("${snapUrl}")`;
    }
    if (this.onLiveStateChange) {
      this.onLiveStateChange(false);
    }
  }

  async startLive() {
    if (!this.activeCamera) return;

    if (this.overlay) {
      this.overlay.style.display = "none";
    }
    if (this.video) {
      this.video.style.display = "block";
    }

    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      this.peerConnection = pc;

      pc.ontrack = (event) => {
        if (this.video && event.streams && event.streams[0]) {
          this.video.srcObject = event.streams[0];
          this.video.play().catch(() => {});
        }
      };

      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Fetch WebRTC metadata from backend
      const metaRes = await fetch(`/api/cameras/${encodeURIComponent(this.activeCamera.id)}/webrtc`);
      if (!metaRes.ok) throw new Error("Failed to get WebRTC metadata");
      const meta = await metaRes.json();

      // Exchange SDP via go2rtc HTTP/WebSocket or proxy
      const sdpRes = await fetch(meta.go2rtc_url.replace("/ws", "/webrtc"), {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          src: this.activeCamera.id,
          sdp: offer.sdp,
        }),
      });

      if (sdpRes.ok) {
        const answerSdp = await sdpRes.text();
        await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
      }

      this.isLive = true;
      if (this.onLiveStateChange) {
        this.onLiveStateChange(true);
      }
    } catch (err) {
      console.warn("WebRTC live stream connection failed:", err);
      // Fallback gracefully to snapshot
      this.showSnapshotFallback();
    }
  }

  stopLive() {
    if (this.peerConnection) {
      this.peerConnection.close();
      this.peerConnection = null;
    }
    if (this.video) {
      this.video.srcObject = null;
    }
    this.isLive = false;
  }
}
