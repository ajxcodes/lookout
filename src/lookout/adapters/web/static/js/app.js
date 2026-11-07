/**
 * Main Single Page Application controller for Lookout Web Viewer.
 */
import { CameraSwitcher } from "./components/switcher.js";
import { LivePlayer } from "./components/live-player.js";
import { EventPlayer } from "./components/event-player.js";
import { AcousticVisualizer } from "./components/acoustic-canvas.js";
import { CategoryFilter } from "./components/filters.js";
import { TimelineList } from "./components/timeline.js";
import { SettingsModal } from "./components/settings-modal.js";

class LookoutApp {
  constructor() {
    this.cameras = [];
    this.events = [];
    this.activeCamera = null;
    this.activeCategory = "all";
    this.sseEventSource = null;

    this.initElements();
    this.initComponents();
    this.bindGlobalEvents();
  }

  initElements() {
    this.switcherEl = document.getElementById("switcher-container");
    this.liveVideo = document.getElementById("live-video");
    this.eventVideo = document.getElementById("event-video");
    this.snapshotOverlay = document.getElementById("snapshot-overlay");
    this.startLiveBtn = document.getElementById("start-live-btn");
    this.playerControlsBar = document.getElementById("player-controls");
    this.canvasEl = document.getElementById("acoustic-canvas");
    this.vuBarEl = document.getElementById("vu-bar");
    this.vuTextEl = document.getElementById("vu-db-text");
    this.filterBarEl = document.getElementById("filter-bar");
    this.timelineEl = document.getElementById("timeline-list");
    this.settingsModalEl = document.getElementById("settings-modal");
    this.openSettingsBtn = document.getElementById("open-settings-btn");

    // Speed toggles
    this.speed1x = document.getElementById("speed-1x");
    this.speed15x = document.getElementById("speed-15x");
    this.speed2x = document.getElementById("speed-2x");
    this.loopToggle = document.getElementById("loop-toggle");
    this.downloadBtn = document.getElementById("download-btn");
  }

  initComponents() {
    this.acousticVis = new AcousticVisualizer({
      canvasElement: this.canvasEl,
      vuBarElement: this.vuBarEl,
      vuTextElement: this.vuTextEl,
    });

    this.livePlayer = new LivePlayer({
      videoElement: this.liveVideo,
      overlayElement: this.snapshotOverlay,
      onLiveStateChange: (isLive) => {
        if (isLive) {
          this.eventVideo.style.display = "none";
          this.eventPlayer.video.pause();
          this.acousticVis.stop();
        }
      },
    });

    this.eventPlayer = new EventPlayer({
      videoElement: this.eventVideo,
      controlsContainer: this.playerControlsBar,
      onTimeUpdate: (currentTime, duration) => {
        // Acoustic simulation if decibels present
        if (this.currentEventItem && this.currentEventItem.metrics) {
          this.acousticVis.setAudioDecibels(this.currentEventItem.metrics.audio_decibels || -18);
        }
      },
      onEnded: () => {
        this.acousticVis.stop();
      },
    });

    this.cameraSwitcher = new CameraSwitcher({
      container: this.switcherEl,
      onSelectCamera: (cam) => {
        this.activeCamera = cam;
        this.livePlayer.setCamera(cam);
        this.fetchEvents();
      },
    });

    this.categoryFilter = new CategoryFilter({
      container: this.filterBarEl,
      onFilterChange: (catId) => {
        this.activeCategory = catId;
        this.fetchEvents();
      },
    });

    this.timeline = new TimelineList({
      container: this.timelineEl,
      onSelectEvent: (eventItem) => {
        this.playEvent(eventItem);
      },
      onDeleteEvent: async (eventId) => {
        await this.deleteEvent(eventId);
      },
    });

    this.settingsModal = new SettingsModal({
      modalBackdrop: this.settingsModalEl,
      onClose: () => {},
      onAiModeChanged: (mode) => {
        console.log("Runtime AI mode updated to:", mode);
      },
    });
  }

  bindGlobalEvents() {
    if (this.startLiveBtn) {
      this.startLiveBtn.addEventListener("click", () => {
        this.livePlayer.startLive();
      });
    }

    if (this.openSettingsBtn) {
      this.openSettingsBtn.addEventListener("click", () => {
        this.settingsModal.open();
      });
    }

    // Playback control buttons
    const setSpeedPill = (btn, speed) => {
      [this.speed1x, this.speed15x, this.speed2x].forEach((b) => b?.classList.remove("active"));
      btn?.classList.add("active");
      this.eventPlayer.setSpeed(speed);
    };

    if (this.speed1x) this.speed1x.addEventListener("click", () => setSpeedPill(this.speed1x, 1.0));
    if (this.speed15x) this.speed15x.addEventListener("click", () => setSpeedPill(this.speed15x, 1.5));
    if (this.speed2x) this.speed2x.addEventListener("click", () => setSpeedPill(this.speed2x, 2.0));

    if (this.loopToggle) {
      this.loopToggle.addEventListener("click", () => {
        const isLooping = this.eventPlayer.toggleLoop();
        this.loopToggle.classList.toggle("active", isLooping);
      });
    }

    if (this.downloadBtn) {
      this.downloadBtn.addEventListener("click", () => {
        this.eventPlayer.downloadCurrent();
      });
    }
  }

  playEvent(eventItem) {
    this.currentEventItem = eventItem;
    this.livePlayer.stopLive();

    if (this.snapshotOverlay) this.snapshotOverlay.style.display = "none";
    if (this.liveVideo) this.liveVideo.style.display = "none";
    if (this.eventVideo) this.eventVideo.style.display = "block";

    this.eventPlayer.loadEvent(eventItem);
    this.acousticVis.start();
    if (eventItem.metrics && eventItem.metrics.audio_decibels) {
      this.acousticVis.setAudioDecibels(eventItem.metrics.audio_decibels);
    }
  }

  async deleteEvent(eventId) {
    try {
      const res = await fetch(`/api/events/${encodeURIComponent(eventId)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        this.timeline.removeEvent(eventId);
      }
    } catch (err) {
      console.error("Failed to delete event:", err);
    }
  }

  async fetchCameras() {
    try {
      const res = await fetch("/api/cameras");
      if (!res.ok) return;
      const data = await res.json();
      this.cameras = data.cameras || [];
      this.cameraSwitcher.setCameras(this.cameras);

      if (this.cameras.length > 0) {
        this.activeCamera = this.cameras[0];
        this.livePlayer.setCamera(this.activeCamera);
      }
    } catch (err) {
      console.warn("Failed to fetch cameras:", err);
    }
  }

  async fetchEvents() {
    try {
      const params = new URLSearchParams();
      if (this.activeCamera) {
        params.append("camera_id", this.activeCamera.id);
      }
      if (this.activeCategory && this.activeCategory !== "all") {
        params.append("category", this.activeCategory);
      }

      const res = await fetch(`/api/events?${params.toString()}`);
      if (!res.ok) return;
      const data = await res.json();
      this.events = data.events || [];
      this.timeline.setEvents(this.events);

      // Compute counts per camera
      const counts = {};
      this.events.forEach((e) => {
        counts[e.camera_id] = (counts[e.camera_id] || 0) + 1;
      });
      this.cameraSwitcher.setEventCounts(counts);
    } catch (err) {
      console.warn("Failed to fetch events:", err);
    }
  }

  connectSse() {
    if (this.sseEventSource) {
      this.sseEventSource.close();
    }

    this.sseEventSource = new EventSource("/api/events/stream");

    this.sseEventSource.addEventListener("camera_event", (e) => {
      try {
        const eventData = JSON.parse(e.data);
        this.timeline.addEvent(eventData);

        // Update counts
        const currentCounts = { ...this.cameraSwitcher.eventCounts };
        currentCounts[eventData.camera_id] = (currentCounts[eventData.camera_id] || 0) + 1;
        this.cameraSwitcher.setEventCounts(currentCounts);
      } catch (err) {
        console.warn("Malformed SSE camera_event payload:", err);
      }
    });

    this.sseEventSource.onerror = () => {
      // Reconnection handled automatically by browser EventSource
    };
  }

  async start() {
    await this.fetchCameras();
    await this.fetchEvents();
    this.connectSse();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const app = new LookoutApp();
  app.start();
});
