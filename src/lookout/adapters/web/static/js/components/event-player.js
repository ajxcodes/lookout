/**
 * Recorded event video player with playback controls.
 */
export class EventPlayer {
  constructor({ videoElement, controlsContainer, onTimeUpdate, onEnded }) {
    this.video = videoElement;
    this.controlsContainer = controlsContainer;
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
  }

  loadEvent(eventItem) {
    this.currentEvent = eventItem;
    if (!this.video) return;

    // Use normalized MP4 media_uri or snapshot
    const videoUrl = eventItem.media_uri || `/media/${eventItem.camera_id}/${eventItem.id}.mp4`;
    this.video.src = videoUrl;
    this.video.playbackRate = this.currentSpeed;
    this.video.loop = this.isLooping;
    this.video.play().catch(() => {
      // Autoplay with audio might be blocked by browser policy
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
    const url = this.video.src;
    const a = document.createElement("a");
    a.href = url;
    a.download = `${this.currentEvent.camera_id}_${this.currentEvent.id}.mp4`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
}
