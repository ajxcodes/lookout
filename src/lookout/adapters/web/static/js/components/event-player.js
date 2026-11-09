/**
 * Recorded event video player and dedicated snapshot card presentation component.
 */
import { resolveUrl } from "../utils/url.js";

export class EventPlayer {
  constructor({
    videoElement,
    controlsContainer,
    snapshotCardElement,
    snapshotImgElement,
    snapshotMetaElement,
    videoControlsElement,
    snapshotControlsElement,
    onTimeUpdate,
    onEnded,
  }) {
    this.video = videoElement;
    this.controlsContainer = controlsContainer;
    this.snapshotCard = snapshotCardElement || document.getElementById("event-snapshot-card");
    this.snapshotImg = snapshotImgElement || document.getElementById("snapshot-card-img");
    this.snapshotMeta = snapshotMetaElement || document.getElementById("snapshot-card-meta");
    this.videoControls = videoControlsElement || document.getElementById("video-playback-controls");
    this.snapshotControls = snapshotControlsElement || document.getElementById("snapshot-playback-controls");
    this.onTimeUpdate = onTimeUpdate;
    this.onEnded = onEnded;
    this.currentSpeed = 1.0;
    this.isLooping = false;
    this.currentEvent = null;

    this.bindEvents();
  }

  bindEvents() {
    if (!this.video) return;

    this.video.addEventListener("timeupdate", () => {
      if (this.onTimeUpdate) {
        this.onTimeUpdate(this.video.currentTime, this.video.duration);
      }
    });

    this.video.addEventListener("ended", () => {
      if (this.onEnded) {
        this.onEnded();
      }
    });

    const downloadSnapBtn = document.getElementById("download-snapshot-btn");
    if (downloadSnapBtn) {
      downloadSnapBtn.addEventListener("click", () => this.downloadSnapshot());
    }

    const inspectBtn = document.getElementById("inspect-frame-btn");
    if (inspectBtn) {
      inspectBtn.addEventListener("click", () => this.inspectFrame());
    }
  }

  loadEvent(eventItem) {
    this.currentEvent = eventItem;

    const isSnapshotOnly = !eventItem.media_uri || (eventItem.metadata && eventItem.metadata.media_purged);

    if (isSnapshotOnly) {
      // 1. Stage Presentation: Render dedicated high-resolution Snapshot Card
      if (this.video) {
        this.video.pause();
        this.video.style.display = "none";
        this.video.removeAttribute("src");
      }

      if (this.snapshotCard) {
        this.snapshotCard.style.display = "flex";

        if (this.snapshotImg) {
          const rawThumb = eventItem.snapshot_uri || `/media/${eventItem.camera_id}/${eventItem.id}.jpg`;
          this.snapshotImg.src = resolveUrl(rawThumb);
        }

        if (this.snapshotMeta) {
          const timeStr = new Date(eventItem.timestamp).toLocaleString();
          const categoryStr = (eventItem.category || "Motion").replace(/_/g, " ");
          const entities = (eventItem.matched_entities || []).join(", ");
          this.snapshotMeta.innerHTML = `
            <span class="snapshot-badge">📸 Snapshot</span>
            <div class="snapshot-info-overlay">
              <span class="meta-camera">${eventItem.camera_id}</span>
              <span class="meta-time">${timeStr}</span>
              <span class="meta-category">${categoryStr}</span>
              ${entities ? `<span class="meta-entities">Entities: ${entities}</span>` : ""}
            </div>
          `;
        }
      }

      // 2. Controls Bar Adaptation: Hide video controls, display snapshot actions
      if (this.videoControls) {
        this.videoControls.style.display = "none";
      }
      const videoDownloadWrap = document.getElementById("video-download-wrap");
      if (videoDownloadWrap) {
        videoDownloadWrap.style.display = "none";
      }
      if (this.snapshotControls) {
        this.snapshotControls.style.display = "flex";
      }
      return;
    }

    // Event has playable MP4 media
    if (this.snapshotCard) {
      this.snapshotCard.style.display = "none";
    }
    if (this.snapshotControls) {
      this.snapshotControls.style.display = "none";
    }
    if (this.videoControls) {
      this.videoControls.style.display = "flex";
    }
    const videoDownloadWrap = document.getElementById("video-download-wrap");
    if (videoDownloadWrap) {
      videoDownloadWrap.style.display = "block";
    }

    if (!this.video) return;
    this.video.style.display = "block";

    const rawUrl = eventItem.media_uri || `/media/${eventItem.camera_id}/${eventItem.id}.mp4`;
    const videoUrl = resolveUrl(rawUrl);
    this.video.src = videoUrl;
    this.video.playbackRate = this.currentSpeed;
    this.video.loop = this.isLooping;
    this.video.play().catch(() => {
      this.video.muted = true;
      this.video.play().catch(() => {});
    });
  }

  setSpeed(speed) {
    this.currentSpeed = speed;
    if (this.video) {
      this.video.playbackRate = speed;
    }
  }

  toggleLoop() {
    this.isLooping = !this.isLooping;
    if (this.video) {
      this.video.loop = this.isLooping;
    }
    return this.isLooping;
  }

  downloadCurrent() {
    if (!this.currentEvent) return;
    const url = this.video ? this.video.src : null;
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = `${this.currentEvent.camera_id}_${this.currentEvent.id}.mp4`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  downloadSnapshot() {
    if (!this.currentEvent) return;
    const rawThumb = this.currentEvent.snapshot_uri || `/media/${this.currentEvent.camera_id}/${this.currentEvent.id}.jpg`;
    const a = document.createElement("a");
    a.href = resolveUrl(rawThumb);
    a.download = `${this.currentEvent.camera_id}_${this.currentEvent.id}_snapshot.jpg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  inspectFrame() {
    if (!this.currentEvent) return;
    const rawThumb = this.currentEvent.snapshot_uri || `/media/${this.currentEvent.camera_id}/${this.currentEvent.id}.jpg`;
    window.open(resolveUrl(rawThumb), "_blank");
  }
}
