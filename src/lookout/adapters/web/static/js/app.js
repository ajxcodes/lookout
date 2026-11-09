/**
 * Main Single Page Application controller for Lookout Web Viewer.
 */
import { CameraSwitcher } from "./components/switcher.js";
import { LivePlayer } from "./components/live-player.js";
import { EventPlayer } from "./components/event-player.js";
import { LiveGrid } from "./components/live-grid.js";
import { AcousticVisualizer } from "./components/acoustic-canvas.js";
import { CategoryFilter } from "./components/filters.js";
import { TimelineList } from "./components/timeline.js";
import { TimelineScrubber } from "./components/scrubber.js";
import { SettingsModal } from "./components/settings-modal.js";
import { resolveUrl } from "./utils/url.js";

class LookoutApp {
  constructor() {
    this.cameras = [];
    this.events = [];
    this.activeCamera = null;
    this.activeCategory = "all";
    this.viewMode = "grid"; // Default primary main landing view
    this.sseEventSource = null;

    this.initElements();
    this.initComponents();
    this.bindGlobalEvents();
  }

  initElements() {
    this.switcherEl = document.getElementById("switcher-container");
    this.liveGridSection = document.getElementById("live-grid-section");
    this.liveGridContainer = document.getElementById("live-grid-container");
    this.focusStage = document.getElementById("focus-stage");
    this.viewerMain = document.getElementById("viewer-main");
    this.viewGridBtn = document.getElementById("view-grid-btn");
    this.viewFocusBtn = document.getElementById("view-focus-btn");
    this.backToGridBtn = document.getElementById("back-to-grid-btn");
    this.densitySelect = document.getElementById("grid-density-select");

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
    this.scrubberEl = document.getElementById("scrubber-container");
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
    this.liveGrid = new LiveGrid({
      container: this.liveGridContainer,
      densitySelector: this.densitySelect,
      onFocusCamera: (cam) => this.switchToFocus(cam),
    });

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
        const isLiveRtc =
          cam.stream_mode === "live_rtc" ||
          cam.provider_badge === "wyze" ||
          cam.camera_type === "wyze" ||
          (cam.stream_mode !== "on_demand" &&
            cam.provider_badge !== "ring" &&
            cam.camera_type !== "ring");
        if (isLiveRtc) {
          this.livePlayer.startLive();
        }
        this.fetchEvents();
      },
      onSelectionChange: (cameraIds) => {
        this.liveGrid.setSelectedCameras(cameraIds);
        if (cameraIds.length === 1) {
          const singleCam = this.cameras.find((c) => c.id === cameraIds[0]);
          if (singleCam) {
            this.switchToFocus(singleCam);
            // Retain grid mode on switcher so user can toggle additional cameras
            this.cameraSwitcher.setMode("grid");
            this.fetchEvents();
            return;
          }
        } else if (cameraIds.length > 1 && this.viewMode === "focus") {
          this.switchToGrid();
          return;
        }
        this.fetchEvents();
      },
    });

    try {
      const stored = localStorage?.getItem("lookout_grid_selected_cameras");
      if (stored) {
        const ids = JSON.parse(stored);
        if (Array.isArray(ids) && ids.length > 0) {
          this.cameraSwitcher.setSelectedCameraIds(ids);
          this.liveGrid.setSelectedCameras(ids);
        }
      }
    } catch (_) {}

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

    if (this.scrubberEl) {
      this.scrubber = new TimelineScrubber({
        container: this.scrubberEl,
        onTimeSelect: (selectedTime) => {
          const target = this.events.find(
            (e) =>
              Math.abs(new Date(e.timestamp).getTime() - selectedTime.getTime()) < 30 * 60 * 1000
          );
          if (target) {
            this.timeline.setSelectedEvent(target.id);
            this.playEvent(target);
          }
        },
      });
    }

    this.settingsModal = new SettingsModal({
      modalBackdrop: this.settingsModalEl,
      onClose: () => {},
      onAiModeChanged: (mode) => {
        console.log("Runtime AI mode updated to:", mode);
      },
    });
  }

  bindGlobalEvents() {
    if (this.viewGridBtn) {
      this.viewGridBtn.addEventListener("click", () => this.switchToGrid());
    }

    if (this.viewFocusBtn) {
      this.viewFocusBtn.addEventListener("click", () => this.switchToFocus());
    }

    if (this.backToGridBtn) {
      this.backToGridBtn.addEventListener("click", () => this.switchToGrid());
    }

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

    // Listen for cross-origin deep-link messages from Home Assistant parent window
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "lookout:deep-link" && typeof e.data.search === "string") {
        this.checkDeepLink(e.data.search);
      } else if (e.data && e.data.type === "ha-set-camera" && e.data.camera) {
        this.checkDeepLink(`?camera_id=${encodeURIComponent(e.data.camera)}`);
      }
    });
  }

  switchToGrid() {
    this.viewMode = "grid";
    if (this.liveGridSection) this.liveGridSection.style.display = "block";
    if (this.focusStage) this.focusStage.style.display = "none";
    if (this.viewerMain) this.viewerMain.style.display = "grid";
    if (this.viewGridBtn) this.viewGridBtn.classList.add("active");
    if (this.viewFocusBtn) this.viewFocusBtn.classList.remove("active");
    this.cameraSwitcher.setMode("grid");
    this.livePlayer.stopLive();
    if (this.eventPlayer && this.eventPlayer.video) {
      this.eventPlayer.video.pause();
    }
    this.acousticVis.stop();
    this.fetchEvents();
  }

  switchToFocus(cam = null, { autoStartLive = true, fetchTimeline = true } = {}) {
    this.viewMode = "focus";
    if (this.liveGridSection) this.liveGridSection.style.display = "none";
    if (this.focusStage) this.focusStage.style.display = "flex";
    if (this.viewerMain) this.viewerMain.style.display = "grid";
    if (this.viewGridBtn) this.viewGridBtn.classList.remove("active");
    if (this.viewFocusBtn) this.viewFocusBtn.classList.add("active");
    this.cameraSwitcher.setMode("focus");

    const targetCam =
      cam || this.activeCamera || (this.cameras.length > 0 ? this.cameras[0] : null);
    if (targetCam) {
      this.activeCamera = targetCam;
      this.cameraSwitcher.setActiveCamera(targetCam.id);
      this.livePlayer.setCamera(targetCam);
      const isLiveRtc =
        targetCam.stream_mode === "live_rtc" ||
        targetCam.provider_badge === "wyze" ||
        targetCam.camera_type === "wyze" ||
        (targetCam.stream_mode !== "on_demand" &&
          targetCam.provider_badge !== "ring" &&
          targetCam.camera_type !== "ring");
      if (autoStartLive && isLiveRtc) {
        this.livePlayer.startLive();
      }
      if (fetchTimeline) {
        this.fetchEvents();
      }
    }
  }

  async playEvent(eventItem) {
    const cam = this.cameras.find((c) => c.id === eventItem.camera_id) || {
      id: eventItem.camera_id,
      name: eventItem.camera_id,
    };
    const cameraChanged = !this.activeCamera || this.activeCamera.id !== cam.id;
    const modeChanged = this.viewMode !== "focus";
    this.activeCamera = cam;
    this.currentEventItem = eventItem;

    this.switchToFocus(cam, { autoStartLive: false, fetchTimeline: false });
    this.livePlayer.stopLive();

    if (this.snapshotOverlay) this.snapshotOverlay.style.display = "none";
    if (this.liveVideo) this.liveVideo.style.display = "none";
    if (this.eventVideo) this.eventVideo.style.display = "block";

    this.eventPlayer.loadEvent(eventItem);

    if (cameraChanged || modeChanged) {
      await this.fetchEvents();
    }
    this.timeline.setSelectedEvent(eventItem.id);
    if (this.timelineEl) {
      const selectedCard = this.timelineEl.querySelector(
        `.event-card[data-event-id="${eventItem.id}"], .event-card[data-id="${eventItem.id}"]`
      );
      if (selectedCard && typeof selectedCard.scrollIntoView === "function") {
        selectedCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }

    this.acousticVis.start();
    if (eventItem.metrics && eventItem.metrics.audio_decibels) {
      this.acousticVis.setAudioDecibels(eventItem.metrics.audio_decibels);
    }
  }

  async deleteEvent(eventId) {
    try {
      const res = await fetch(resolveUrl(`/api/events/${encodeURIComponent(eventId)}`), {
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
      const res = await fetch(resolveUrl("/api/cameras"));
      if (!res.ok) return;
      const data = await res.json();
      this.cameras = data.cameras || [];
      this.liveGrid.setCameras(this.cameras);
      this.timeline.setCameras(this.cameras);
      this.cameraSwitcher.setCameras(this.cameras);

      if (this.cameras.length > 0) {
        this.activeCamera = this.cameras[0];
        this.livePlayer.setCamera(this.activeCamera);
      }

      const selectedIds = this.cameraSwitcher.getSelectedCameraIds();
      if (selectedIds.length === 1) {
        const singleCam = this.cameras.find((c) => c.id === selectedIds[0]);
        if (singleCam) {
          this.switchToFocus(singleCam);
          return;
        }
      }
      this.switchToGrid();
    } catch (err) {
      console.warn("Failed to fetch cameras:", err);
    }
  }

  async fetchEvents() {
    try {
      const params = new URLSearchParams();
      if (this.viewMode === "focus" && this.activeCamera) {
        params.append("camera_id", this.activeCamera.id);
      }
      if (this.activeCategory && this.activeCategory !== "all") {
        params.append("category", this.activeCategory);
      }

      const res = await fetch(resolveUrl(`/api/events?${params.toString()}`));
      if (!res.ok) return;
      const data = await res.json();
      let events = data.events || [];

      if (this.viewMode === "grid") {
        const selectedIds = this.cameraSwitcher.getSelectedCameraIds();
        if (selectedIds.length === 0) {
          events = [];
        } else {
          events = events.filter((e) => selectedIds.includes(e.camera_id));
        }
      }

      this.events = events;
      this.timeline.setEvents(this.events);
      if (this.currentEventItem) {
        this.timeline.setSelectedEvent(this.currentEventItem.id);
      }
      if (this.scrubber) this.scrubber.setEvents(this.events);

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

    this.sseEventSource = new EventSource(resolveUrl("/api/events/stream"));

    this.sseEventSource.addEventListener("camera_event", (e) => {
      try {
        const eventData = JSON.parse(e.data);
        this.timeline.addEvent(eventData);
        if (this.scrubber) this.scrubber.setEvents(this.events);

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

  _matchEventByTarget(eventsList, targetEventId, targetCamId = null) {
    if (!targetEventId || !Array.isArray(eventsList)) return null;
    const cleanTarget = targetEventId.replace(/\.(jpg|jpeg|mp4|gif)$/i, "");
    return (
      eventsList.find((e) => {
        if (e.id === targetEventId || e.id === cleanTarget) return true;
        if (e.snapshot_uri && (e.snapshot_uri.includes(targetEventId) || e.snapshot_uri.includes(cleanTarget))) {
          return true;
        }
        if (e.media_uri && (e.media_uri.includes(targetEventId) || e.media_uri.includes(cleanTarget))) {
          return true;
        }
        return false;
      }) || null
    );
  }

  checkDeepLink(customSearch = null) {
    let search = customSearch !== null ? customSearch : window.location.search;
    if (!search && window.location.hash && window.location.hash.includes("?")) {
      search = window.location.hash.slice(window.location.hash.indexOf("?"));
    }
    if (!search) {
      try {
        if (window.parent && window.parent !== window && window.parent.location.search) {
          search = window.parent.location.search;
        }
      } catch (_) {}
    }

    // If embedded in an iframe and no query found, request deep link from parent window
    if (!search && window.self !== window.top) {
      try {
        window.parent.postMessage({ type: "lookout:request-deep-link" }, "*");
      } catch (_) {}
    }

    const urlParams = new URLSearchParams(search);
    const eventId = urlParams.get("event_id") || urlParams.get("event");
    const camId = urlParams.get("camera_id") || urlParams.get("camera");
    const live = urlParams.get("live") === "true";

    const path = window.location.pathname;
    const eventMatch = path.match(/\/events\/([^/?#]+)/);
    const camMatch = path.match(/\/cameras\/([^/?#]+)/);

    const targetEventId = eventId || (eventMatch ? eventMatch[1] : null);
    const targetCamId = camId || (camMatch ? camMatch[1] : null);

    if (targetEventId) {
      const found = this._matchEventByTarget(this.events, targetEventId, targetCamId);
      if (found) {
        this.playEvent(found);
      } else {
        fetch(resolveUrl(`/api/events/${encodeURIComponent(targetEventId)}`))
          .then((r) => (r.ok ? r.json() : null))
          .then((ev) => {
            if (ev) {
              if (!this.events.some((e) => e.id === ev.id)) {
                this.events.unshift(ev);
                this.timeline.setEvents(this.events);
              }
              this.playEvent(ev);
            } else if (targetCamId) {
              const cam = this.cameras.find((c) => c.id === targetCamId);
              if (cam) {
                this.switchToFocus(cam, { autoStartLive: false, fetchTimeline: true });
              }
            }
          })
          .catch(() => {});
      }
      return true;
    } else if (targetCamId) {
      const cam = this.cameras.find((c) => c.id === targetCamId);
      if (cam) {
        this.switchToFocus(cam);
        if (live) {
          this.livePlayer.startLive();
        }
        return true;
      }
    }
    return false;
  }

  async start() {
    await this.fetchCameras();
    await this.fetchEvents();
    this.connectSse();
    this.checkDeepLink();
  }
}

function initApp() {
  const app = new LookoutApp();
  app.start();
  window.lookoutApp = app;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}
