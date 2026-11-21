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
import { ZoomController } from "./components/zoom-controller.js";
import { resolveUrl } from "./utils/url.js";

// AC10: labels live here so the viewer keeps a single, explicit vocabulary for
// the universal camera-management controls rendered by LiveGrid.
const CAMERA_MANAGEMENT_LABELS = {
  remove: "Remove camera",
  reauthenticate: "Reauthenticate",
  authRequired: "Authentication required",
};

class LookoutApp {
  constructor() {
    this.cameras = [];
    this.events = [];
    this.activeCamera = null;
    this.activeCategory = "all";
    this.viewMode = "grid"; // Default primary main landing view
    this.sseEventSource = null;
    this.currentSettingsCamera = null;

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
    this.returnToLiveBtn = document.getElementById("return-to-live-btn");
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
    this.addCameraBtn = document.getElementById("add-camera-dropdown-btn") || document.getElementById("add-camera-btn");
    this.addCameraDropdown = document.getElementById("add-camera-dropdown");
    this.addCameraModal = document.getElementById("add-camera-modal");
    this.addCamName = document.getElementById("add-cam-name");
    this.addCamId = document.getElementById("add-cam-id");
    this.addCamType = document.getElementById("add-cam-type");
    this.addCamUrl = document.getElementById("add-cam-url");
    this.addCamLocation = document.getElementById("add-cam-location");
    this.addCamNativeId = document.getElementById("add-cam-native-id");
    this.addCamNativeGroup = document.getElementById("add-cam-native-group");
    this.addCamEnabled = document.getElementById("add-cam-enabled");
    this.addCamStatus = document.getElementById("add-cam-status");
    this.addCamSubmit = document.getElementById("add-cam-submit");
    this.addCamCancel = document.getElementById("add-cam-cancel");
    this.addCamClose = document.getElementById("add-camera-modal-close");

    // Fullscreen / maximize
    this.fullscreenBtn = document.getElementById("fullscreen-btn");
    this.maximizeBtn = document.getElementById("maximize-btn");

    // Camera settings modal
    this.cameraSettingsModal = document.getElementById("camera-settings-modal");
    this.csCamName = document.getElementById("cs-cam-name");
    this.csCamLocation = document.getElementById("cs-cam-location");
    this.csCamEnabled = document.getElementById("cs-cam-enabled");
    this.csCamMinThreat = document.getElementById("cs-cam-min-threat");
    this.csCamEventTypes = document.getElementById("cs-cam-event-types");
    this.csCamNotifyPass1 = document.getElementById("cs-cam-notify-pass1");
    this.csCamNotifyPass2 = document.getElementById("cs-cam-notify-pass2");
    this.csAuthGroup = document.getElementById("cs-auth-group");
    this.csReauthBtn = document.getElementById("cs-reauth-btn");
    this.csRemoveBtn = document.getElementById("cs-remove-btn");
    this.csSaveBtn = document.getElementById("cs-save-btn");
    this.csCancelBtn = document.getElementById("cs-cancel-btn");
    this.csCloseBtn = document.getElementById("camera-settings-modal-close");

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
      onRemoveCamera: (cam) => this.removeCamera(cam),
      onReauthenticateCamera: (cam) => this.reauthenticateCamera(cam),
      onOpenCameraSettings: (cam) => this.openCameraSettings(cam),
      cameraManagementLabels: CAMERA_MANAGEMENT_LABELS,
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

    const videoContainer = document.querySelector(".video-container");
    if (videoContainer) {
      this.zoomController = new ZoomController(videoContainer);
    }
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

    if (this.fullscreenBtn) {
      this.fullscreenBtn.addEventListener("click", () => this.toggleFullscreen());
    }
    if (this.maximizeBtn) {
      this.maximizeBtn.addEventListener("click", () => this.toggleMaximize());
    }

    // Camera settings modal handlers
    if (this.csCloseBtn) {
      this.csCloseBtn.addEventListener("click", () => this.closeCameraSettings());
    }
    if (this.csCancelBtn) {
      this.csCancelBtn.addEventListener("click", () => this.closeCameraSettings());
    }
    if (this.csSaveBtn) {
      this.csSaveBtn.addEventListener("click", () => this.saveCameraSettings());
    }
    if (this.csRemoveBtn) {
      this.csRemoveBtn.addEventListener("click", () => {
        if (this.currentSettingsCamera) {
          this.removeCamera(this.currentSettingsCamera);
          this.closeCameraSettings();
        }
      });
    }
    if (this.csReauthBtn) {
      this.csReauthBtn.addEventListener("click", () => {
        if (this.currentSettingsCamera) this.reauthenticateCamera(this.currentSettingsCamera);
      });
    }
    if (this.cameraSettingsModal) {
      this.cameraSettingsModal.addEventListener("click", (e) => {
        if (e.target === this.cameraSettingsModal) this.closeCameraSettings();
      });
    }

    if (this.returnToLiveBtn) {
      this.returnToLiveBtn.addEventListener("click", () => this.returnToLive());
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

    // Collapsible events panel (timeline aside) with persisted state.
    const eventsToggle = document.getElementById("events-panel-toggle");
    const timelineAside = document.querySelector(".timeline-section");
    if (eventsToggle && timelineAside) {
      try {
        if (localStorage.getItem("lookout_events_collapsed") === "1") {
          timelineAside.classList.add("collapsed");
        }
      } catch (_) {}
      eventsToggle.addEventListener("click", () => {
        timelineAside.classList.toggle("collapsed");
        try {
          localStorage.setItem(
            "lookout_events_collapsed",
            timelineAside.classList.contains("collapsed") ? "1" : "0"
          );
        } catch (_) {}
      });
    }
    if (this.addCameraBtn) {
      this.addCameraBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (this.addCameraDropdown) {
          this.addCameraDropdown.style.display =
            this.addCameraDropdown.style.display === "none" ? "block" : "none";
        }
      });
    }

    // Dropdown items
    document.querySelectorAll(".add-camera-dropdown .dropdown-item").forEach((item) => {
      item.addEventListener("click", () => {
        const action = item.dataset.action;
        if (this.addCameraDropdown) this.addCameraDropdown.style.display = "none";
        if (action === "camera") this.openAddCameraModal();
        else if (action === "provider") {
          // Route to settings modal integrations tab
          if (this.settingsModal) this.settingsModal.open();
        }
      });
    });

    // Close dropdown on outside click
    document.addEventListener("click", (e) => {
      if (this.addCameraDropdown && !e.target.closest(".add-camera-menu-wrapper")) {
        this.addCameraDropdown.style.display = "none";
      }
    });

    // Modal handlers
    if (this.addCamClose) {
      this.addCamClose.addEventListener("click", () => this.closeAddCameraModal());
    }
    if (this.addCamCancel) {
      this.addCamCancel.addEventListener("click", () => this.closeAddCameraModal());
    }
    if (this.addCameraModal) {
      this.addCameraModal.addEventListener("click", (e) => {
        if (e.target === this.addCameraModal) this.closeAddCameraModal();
      });
    }
    if (this.addCamSubmit) {
      this.addCamSubmit.addEventListener("click", () => this.submitAddCamera());
    }
    if (this.addCamName) {
      this.addCamName.addEventListener("input", () => this.autoGenerateCamId());
    }
    if (this.addCamType) {
      this.addCamType.addEventListener("change", () => this.updateCamTypeFields());
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

  returnToLive() {
    if (!this.activeCamera) return;
    this.currentEventItem = null;
    if (this.eventPlayer) this.eventPlayer.stop();
    const aiDetails = document.getElementById("focus-ai-details-card");
    if (aiDetails) aiDetails.style.display = "none";
    if (this.returnToLiveBtn) this.returnToLiveBtn.style.display = "none";
    if (this.timeline) this.timeline.setSelectedEvent(null);
    if (this.zoomController) this.zoomController.resetZoom(false);
    this.switchToFocus(this.activeCamera, { autoStartLive: true, fetchTimeline: false });
  }

  switchToGrid() {
    this.viewMode = "grid";
    if (this.zoomController) this.zoomController.resetZoom(false);
    if (this.returnToLiveBtn) this.returnToLiveBtn.style.display = "none";
    const aiDetails = document.getElementById("focus-ai-details-card");
    if (aiDetails) aiDetails.style.display = "none";
    if (this.liveGridSection) this.liveGridSection.style.display = "block";
    if (this.focusStage) this.focusStage.style.display = "none";
    if (this.viewerMain) this.viewerMain.style.display = "grid";
    if (this.viewGridBtn) this.viewGridBtn.classList.add("active");
    if (this.viewFocusBtn) this.viewFocusBtn.classList.remove("active");
    this.cameraSwitcher.setMode("grid");
    this.livePlayer.stopLive();
    if (this.eventPlayer) {
      this.eventPlayer.stop();
    }
    this.acousticVis.stop();
    this.fetchEvents();
  }

  switchToFocus(cam = null, { autoStartLive = true, fetchTimeline = true } = {}) {
    this.viewMode = "focus";
    if (this.zoomController) this.zoomController.resetZoom(false);
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
      if (autoStartLive) {
        if (this.returnToLiveBtn) this.returnToLiveBtn.style.display = "none";
        const aiDetails = document.getElementById("focus-ai-details-card");
        if (aiDetails) aiDetails.style.display = "none";
        if (this.eventVideo) this.eventVideo.style.display = "none";
        const snapCard = document.getElementById("event-snapshot-card");
        if (snapCard) snapCard.style.display = "none";

        if (isLiveRtc) {
          if (this.snapshotOverlay) this.snapshotOverlay.style.display = "none";
          if (this.liveVideo) this.liveVideo.style.display = "block";
          this.livePlayer.startLive();
        } else {
          if (this.snapshotOverlay) this.snapshotOverlay.style.display = "flex";
          if (this.liveVideo) this.liveVideo.style.display = "none";
        }
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

    if (this.zoomController) this.zoomController.resetZoom(false);
    this.switchToFocus(cam, { autoStartLive: false, fetchTimeline: false });
    this.livePlayer.stopLive();

    if (this.returnToLiveBtn) this.returnToLiveBtn.style.display = "inline-flex";

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
      if (selectedCard) {
        const containerRect = this.timelineEl.getBoundingClientRect();
        const cardRect = selectedCard.getBoundingClientRect();
        this.timelineEl.scrollTop += (cardRect.top - containerRect.top);
      }
    }
    // Prevent awkward scroll jumps down to the card; keep video player front and center
    window.scrollTo({ top: 0, behavior: "instant" });

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

  // AC10: management controls are rendered directly into each LiveGrid tile
  // (see components/camera-management.js) rather than attached after the fact
  // with a selector that did not match the real `.camera-grid-tile` elements.

  async addCamera() {
    try {
      const id = window.prompt("Camera ID:");
      if (!id) return;
      const name = window.prompt("Display name:", id) || id;
      const streamUrl = window.prompt("Stream URL (rtsp/http/https/ring/wyze):");
      if (!streamUrl) return;
      const provider = window.prompt("Native provider (ring/wyze/blank for generic):", "") || null;
      const nativeDeviceId = provider ? window.prompt("Native device id:", "") || null : null;
      const body = {
        id: id.trim(),
        name: name.trim(),
        stream_url: streamUrl.trim(),
        camera_type: provider || "generic",
      };
      if (provider) {
        body.native_provider = provider.trim();
        if (nativeDeviceId) body.native_device_id = nativeDeviceId.trim();
      }
      const res = await fetch(resolveUrl("/api/cameras"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) await this.fetchCameras();
      else {
        const data = await res.json().catch(() => ({}));
        window.alert(data.detail || "Failed to add camera");
      }
    } catch (err) {
      console.warn("Camera add failed:", err);
    }
  }

  async removeCamera(cam) {
    if (!window.confirm(`Remove camera '${cam.name || cam.id}'? Recorded history is kept.`)) {
      return;
    }
    try {
      const res = await fetch(resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}`), {
        method: "DELETE",
      });
      if (res.ok) await this.fetchCameras();
    } catch (err) {
      console.warn("Camera removal failed:", err);
    }
  }

  toggleFullscreen() {
    const target = this.focusStage || document.documentElement;
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else if (target && target.requestFullscreen) {
      target.requestFullscreen().catch(() => {});
    }
  }

  toggleMaximize() {
    const stage = this.focusStage;
    if (!stage) return;
    stage.classList.toggle("maximized");
    if (stage.classList.contains("maximized")) {
      if (this.maximizeBtn) this.maximizeBtn.textContent = "( ) Restore";
    } else {
      if (this.maximizeBtn) this.maximizeBtn.textContent = "( ) Maximize";
    }
  }

  openCameraSettings(cam) {
    this.currentSettingsCamera = cam || null;
    if (!this.cameraSettingsModal) return;
    if (cam) {
      if (this.csCamName) this.csCamName.value = cam.name || "";
      if (this.csCamLocation) this.csCamLocation.value = cam.location || "";
      if (this.csCamEnabled) this.csCamEnabled.checked = cam.enabled !== false;
      if (this.csCamMinThreat) this.csCamMinThreat.value = cam.notification_min_threat || "";
      if (this.csCamEventTypes) {
        const types = new Set(cam.notification_event_types || ["motion", "sound", "person", "ding"]);
        this.csCamEventTypes.querySelectorAll(".cs-cam-event-type").forEach((cb) => {
          cb.checked = types.has(cb.value);
        });
      }
      if (this.csCamNotifyPass1) this.csCamNotifyPass1.checked = cam.notify_pass1 !== false;
      if (this.csCamNotifyPass2) this.csCamNotifyPass2.checked = cam.notify_pass2 !== false;
    }
    if (this.csAuthGroup) {
      this.csAuthGroup.style.display = cam && cam.auth_required ? "" : "none";
    }
    this.cameraSettingsModal.style.display = "flex";
    this.cameraSettingsModal.classList.add("open");
  }

  closeCameraSettings() {
    if (!this.cameraSettingsModal) return;
    this.cameraSettingsModal.classList.remove("open");
    this.cameraSettingsModal.style.display = "none";
    this.currentSettingsCamera = null;
  }

  async saveCameraSettings() {
    const cam = this.currentSettingsCamera;
    if (!cam) return;
    const payload = {};
    if (this.csCamName) payload.name = this.csCamName.value.trim();
    if (this.csCamLocation) payload.location = this.csCamLocation.value.trim();
    if (this.csCamEnabled) payload.enabled = this.csCamEnabled.checked;
    if (this.csCamMinThreat) payload.notification_min_threat = this.csCamMinThreat.value || null;
    if (this.csCamEventTypes) {
      payload.notification_event_types = Array.from(
        this.csCamEventTypes.querySelectorAll(".cs-cam-event-type:checked")
      ).map((cb) => cb.value);
    }
    if (this.csCamNotifyPass1) payload.notify_pass1 = this.csCamNotifyPass1.checked;
    if (this.csCamNotifyPass2) payload.notify_pass2 = this.csCamNotifyPass2.checked;
    try {
      const res = await fetch(resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/settings`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) await this.fetchCameras();
    } catch (err) {
      console.warn("Camera settings save failed:", err);
    }
    this.closeCameraSettings();
  }

  autoGenerateCamId() {
    if (!this.addCamName || !this.addCamId) return;
    // Only auto-generate if the user hasn't manually edited the ID
    if (this._camIdManuallyEdited) return;
    const name = this.addCamName.value.trim();
    const slug = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    this.addCamId.value = slug;
  }

  updateCamTypeFields() {
    if (!this.addCamType || !this.addCamUrl) return;
    const type = this.addCamType.value;
    const urlGroup = this.addCamUrl.closest(".setting-group");
    if (type === "ring" || type === "wyze") {
      // Native providers: stream URL is resolved from discovery; hide manual URL
      if (urlGroup) urlGroup.style.display = "none";
      this.addCamUrl.value = "";
      if (this.addCamNativeGroup) this.addCamNativeGroup.style.display = "";
    } else {
      if (urlGroup) urlGroup.style.display = "";
      if (this.addCamNativeGroup) this.addCamNativeGroup.style.display = "none";
      if (this.addCamNativeId) this.addCamNativeId.value = "";
    }
  }

  openAddCameraModal() {
    if (!this.addCameraModal) return;
    // Reset form
    if (this.addCamName) this.addCamName.value = "";
    if (this.addCamId) this.addCamId.value = "";
    if (this.addCamType) this.addCamType.value = "generic";
    if (this.addCamUrl) this.addCamUrl.value = "";
    if (this.addCamLocation) this.addCamLocation.value = "";
    if (this.addCamEnabled) this.addCamEnabled.checked = true;
    if (this.addCamNativeId) this.addCamNativeId.value = "";
    if (this.addCamStatus) {
      this.addCamStatus.style.display = "none";
      this.addCamStatus.textContent = "";
    }
    this._camIdManuallyEdited = false;
    this.updateCamTypeFields();
    this.addCameraModal.style.display = "flex";
    this.addCameraModal.classList.add("open");
    if (this.addCamName) this.addCamName.focus();
    // Track manual ID edits
    if (this.addCamId) {
      this.addCamId.addEventListener("input", () => {
        this._camIdManuallyEdited = true;
      }, { once: true });
    }
  }

  closeAddCameraModal() {
    if (!this.addCameraModal) return;
    this.addCameraModal.classList.remove("open");
    this.addCameraModal.style.display = "none";
  }

  async submitAddCamera() {
    if (!this.addCamName || !this.addCamId) return;
    const name = this.addCamName.value.trim();
    const id = this.addCamId.value.trim();
    const type = this.addCamType ? this.addCamType.value : "generic";
    const stream_url = this.addCamUrl ? this.addCamUrl.value.trim() : "";
    const location = this.addCamLocation ? this.addCamLocation.value.trim() : "";
    const enabled = this.addCamEnabled ? this.addCamEnabled.checked : true;
    const native_device_id = this.addCamNativeId ? this.addCamNativeId.value.trim() : "";

    if (!name) {
      this.showAddCamStatus("Display name is required", "error");
      return;
    }
    if (!id) {
      this.showAddCamStatus("Camera ID is required", "error");
      return;
    }
    if (type === "generic" || type === "go2rtc") {
      if (!stream_url) {
        this.showAddCamStatus("Stream URL is required", "error");
        return;
      }
    } else if (type === "ring" || type === "wyze") {
      if (!native_device_id) {
        this.showAddCamStatus("Native Device ID is required for " + type, "error");
        return;
      }
    }

    const payload = { id, name, enabled };
    if (stream_url) payload.stream_url = stream_url;
    if (location) payload.location = location;
    if (type && type !== "generic") payload.camera_type = type;

    // Map camera type to provider for native adoption
    if (type === "ring" || type === "wyze") {
      payload.native_provider = type;
      if (native_device_id) payload.native_device_id = native_device_id;
    }

    try {
      if (this.addCamSubmit) {
        this.addCamSubmit.disabled = true;
        this.addCamSubmit.textContent = "Adding...";
      }
      const res = await fetch(resolveUrl("/api/cameras"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        this.showAddCamStatus("Camera added successfully", "success");
        await this.fetchCameras();
        setTimeout(() => this.closeAddCameraModal(), 800);
      } else {
        let detail = "Failed to add camera";
        try {
          const data = await res.json();
          detail = data.detail || detail;
        } catch (_) {}
        this.showAddCamStatus(detail, "error");
      }
    } catch (err) {
      this.showAddCamStatus("Network error: " + err.message, "error");
      console.warn("Camera addition failed:", err);
    } finally {
      if (this.addCamSubmit) {
        this.addCamSubmit.disabled = false;
        this.addCamSubmit.textContent = "Add Camera";
      }
    }
  }

  showAddCamStatus(message, type) {
    if (!this.addCamStatus) return;
    this.addCamStatus.style.display = "block";
    this.addCamStatus.textContent = message;
    this.addCamStatus.style.background = type === "error" ? "rgba(239,68,68,0.15)" : "rgba(16,185,129,0.15)";
    this.addCamStatus.style.color = type === "error" ? "#ef4444" : "#10b981";
    this.addCamStatus.style.border = `1px solid ${type === "error" ? "#ef4444" : "#10b981"}`;
  }

  async reauthenticateCamera(cam) {
    const provider = cam.native_provider || cam.provider_badge || cam.camera_type || "ring";
    // Reuse the AC4 Rotate path: open the real credential modal with force=true
    // so the user actually supplies credentials instead of posting a bare
    // {force: true} body that can never complete a login.
    const wizard = window.lookoutWizard;
    if (wizard && typeof wizard.startAuth === "function") {
      wizard.startAuth(provider, true);
      return;
    }
    // The modal lives on the setup page; route there with a reauth hint and let
    // the wizard open the same credential modal with force=true.
    try {
      window.location.href = resolveUrl(`/setup?reauth=${encodeURIComponent(provider)}`);
    } catch (err) {
      console.warn("Reauthentication unavailable:", err);
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
      // Management controls are now rendered by LiveGrid.setCameras() above.

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

      this.timeline.setEvents(events);
      this.events = this.timeline.events;
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
        const isNew = !this.timeline.events.some((ev) => ev.id === eventData.id);
        this.timeline.addEvent(eventData);
        this.events = this.timeline.events;
        if (this.scrubber) this.scrubber.setEvents(this.events);

        const snapUri = eventData.snapshot_url || eventData.snapshot_uri || null;
        if (this.liveGrid && typeof this.liveGrid.updateCameraSnapshot === "function") {
          this.liveGrid.updateCameraSnapshot(eventData.camera_id, snapUri);
        }
        if (this.livePlayer && typeof this.livePlayer.updateCameraSnapshot === "function") {
          this.livePlayer.updateCameraSnapshot(eventData.camera_id, snapUri);
        }

        if (isNew) {
          // Update counts only for new events
          const currentCounts = { ...this.cameraSwitcher.eventCounts };
          currentCounts[eventData.camera_id] = (currentCounts[eventData.camera_id] || 0) + 1;
          this.cameraSwitcher.setEventCounts(currentCounts);
        }
        if (this.currentEventItem && this.currentEventItem.id === eventData.id) {
          this.currentEventItem = eventData;
          if (this.eventPlayer) {
            if (eventData.media_uri) {
              this.eventPlayer.loadEvent(eventData);
            } else {
              this.eventPlayer.updateAiDetailsCard(eventData);
            }
          }
        }
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
      }
      fetch(resolveUrl(`/api/events/${encodeURIComponent(targetEventId)}`))
        .then((r) => (r.ok ? r.json() : null))
        .then((ev) => {
          if (ev) {
            const idx = this.events.findIndex((e) => e.id === ev.id);
            if (idx >= 0) {
              this.events[idx] = ev;
            } else {
              this.events.unshift(ev);
            }
            this.timeline.setEvents(this.events);
            if (!found || ev.media_uri !== found.media_uri) {
              this.playEvent(ev);
            }
          } else if (!found && targetCamId) {
            const cam = this.cameras.find((c) => c.id === targetCamId);
            if (cam) {
              this.switchToFocus(cam, { autoStartLive: false, fetchTimeline: true });
            }
          }
        })
        .catch(() => {});
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
