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
import {
  eventTimeWithLocalHint,
  formatInBrowserZone,
  formatInDisplayZone,
  formatLocalClockTitle,
  getDisplayTimezone,
  setDisplayTimezone,
} from "./utils/timezone.js";
import { liveUnavailableControlTitle } from "./utils/live-health.js";

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
    this.eventsTotal = 0; // server total from /api/events (may exceed the loaded page)
    this.activeCamera = null;
    this.activeCategory = "all";
    this.viewMode = "grid"; // Default primary main landing view
    this.sseEventSource = null;
    this.currentSettingsCamera = null;
    this.focusPrefs = {
      controlsOverlay: true,
      acousticMode: "below",
      acousticPinned: false,
      eventsCollapsed: false,
      aiDetailsVisible: true,
      clockPinned: false,
    };
    this._acousticUserOverridden = false;
    this.displayMenuOpen = false;

    // Session-only playback/control state (never persisted). Pause ownership
    // lives in LiveGrid; the focus player mirrors the effective state.
    this.eventPaused = false;
    this.eventMuted = false;
    this.eventMutedByCamera = new Map();
    this.controlsVisible = true;
    this.controlsIdleTimer = null;
    this.controlsFocusWithin = false;
    this.controlsHover = false;

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
    this.globalPauseBtn = document.getElementById("global-pause-btn");
    this.viewGridBtn = document.getElementById("view-grid-btn");
    this.viewFocusBtn = document.getElementById("view-focus-btn");
    this.backToGridBtn = document.getElementById("back-to-grid-btn");
    this.focusSettingsBtn = document.getElementById("focus-settings-btn");
    this.returnToLiveBtn = document.getElementById("return-to-live-btn");
    this.densitySelect = document.getElementById("grid-density-select");

    this.liveVideo = document.getElementById("live-video");
    this.eventVideo = document.getElementById("event-video");
    this.snapshotOverlay = document.getElementById("snapshot-overlay");
    this.startLiveBtn = document.getElementById("start-live-btn");
    this.playerControlsBar = document.getElementById("player-controls");
    this.videoContainer = document.querySelector(".video-container");
    this.videoSurface = document.getElementById("video-surface");
    this.controlBarScroll = document.getElementById("control-bar-scroll");
    this.focusControlOverlay = document.getElementById("focus-control-overlay");
    this.playerPauseBtn = document.getElementById("player-pause-btn");
    this.playerMuteBtn = document.getElementById("player-mute-btn");
    this.acousticModeControl = document.getElementById("acoustic-mode-control");
    this.acousticModeButtons = this.acousticModeControl
      ? Array.from(this.acousticModeControl.querySelectorAll(".acoustic-mode-btn"))
      : [];
    this.displayMenuBtn = document.getElementById("display-menu-btn");
    this.displayMenu = document.getElementById("display-menu");
    this.displayAiDetailsToggle = document.getElementById("display-ai-details-toggle");
    this.displayClockPinToggle = document.getElementById("display-clock-pin-toggle");
    this.eventsToggle = document.getElementById("events-panel-toggle");
    this.focusTimeReadout = document.getElementById("focus-time-readout");
    this.focusClockLocal = document.getElementById("focus-clock-local");
    this.focusClockHome = document.getElementById("focus-clock-home");
    this.focusEventTime = document.getElementById("focus-event-time");
    this.focusEventAge = document.getElementById("focus-event-age");
    this.aiDetailsCard = document.getElementById("focus-ai-details-card");
    this.canvasEl = document.getElementById("acoustic-canvas");
    this.vuBarEl = document.getElementById("vu-bar");
    this.vuTextEl = document.getElementById("vu-db-text");
    this.filterBarEl = document.getElementById("filter-bar");
    this.timelineEl = document.getElementById("timeline-list");
    this.timelineAside = document.querySelector(".timeline-section");
    this.jumpToEventsBtn = document.getElementById("jump-to-events-btn");
    this.eventsResizeHandle = document.getElementById("events-resize-handle");
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
    this.csMotionTagGroup = document.getElementById("cs-motion-tag-group");
    this.csCamMotionTag = document.getElementById("cs-cam-motion-tag");
    this.csMotionTagStatus = document.getElementById("cs-motion-tag-status");
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
      cameraManagementLabels: CAMERA_MANAGEMENT_LABELS,
      onLiveStateChange: () => {
        // A tile gaining/losing playable media changes whether pause is honest.
        this.updateGlobalPauseButton();
        this.updatePlayerButtons();
      },
    });

    this.acousticVis = new AcousticVisualizer({
      canvasElement: this.canvasEl,
      vuBarElement: this.vuBarEl,
      vuTextElement: this.vuTextEl,
    });

    this.livePlayer = new LivePlayer({
      videoElement: this.liveVideo,
      overlayElement: this.snapshotOverlay,
      onLiveStateChange: (isLive, detail = {}) => {
        if (isLive) {
          this.eventVideo.style.display = "none";
          this.eventPlayer.video.pause();
          this.acousticVis.stop();
          this.suspendEventAudio();
          // A pause requested while WebRTC was negotiating must still win.
          this.applyLivePauseState();
        }
        // Snapshot fallback and successful media both change whether the
        // pause/mute controls are meaningful; refresh their enabled state.
        this.updatePlayerButtons();
        this.updateGlobalPauseButton();
        if (detail.unavailable) {
          console.warn(`Live unavailable for ${this.activeCamera?.id || "camera"}: ${detail.reason || "unknown"}`);
        }
      },
    });

    this.eventPlayer = new EventPlayer({
      videoElement: this.eventVideo,
      controlsContainer: this.playerControlsBar,
      onEnded: () => {
        // Analysis stops; the Web Audio graph is kept for the next event.
        this.acousticVis.stop();
        this.suspendEventAudio();
      },
    });

    this.cameraSwitcher = new CameraSwitcher({
      container: this.switcherEl,
      onSelectCamera: (cam) => {
        this.activeCamera = cam;
        this.livePlayer.setCamera(cam);
        this.applyLivePauseState();
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
      onFocusViewChanged: (prefs) => this.updateFocusPreferences(prefs),
    });

    const videoContainer = document.querySelector(".video-container");
    if (videoContainer) {
      this.zoomController = new ZoomController(videoContainer);
    }
  }

  /**
   * Build the Web Audio graph for the event video exactly once. Creating a
   * second MediaElementAudioSourceNode for the same <video> throws, so the
   * graph is kept for reuse when switching events or returning to live.
   * The analyser drives the acoustic canvas; it is also connected to the
   * destination so the clip's audio still plays through the speakers.
   */
  setupEventAudio() {
    if (this.audioSetupAttempted || !this.eventVideo || !this.acousticVis) {
      return this.analyser || null;
    }
    this.audioSetupAttempted = true;
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return null; // Unsupported: draw a neutral line.

      if (!this.audioCtx) this.audioCtx = new AudioContextClass();
      if (!this.analyser) {
        this.analyser = this.audioCtx.createAnalyser();
        this.analyser.fftSize = 2048;
        this.analyser.smoothingTimeConstant = 0.8;
        const source = this.audioCtx.createMediaElementSource(this.eventVideo);
        source.connect(this.analyser);
        this.analyser.connect(this.audioCtx.destination);
        this.mediaSource = source;

        // Pause/play must suspend/resume the context so analysis stops when
        // there is nothing playing, without tearing down the graph.
        this.eventVideo.addEventListener("play", () => this.resumeEventAudio());
        this.eventVideo.addEventListener("pause", () => {
          this.acousticVis.stop();
          this.suspendEventAudio();
        });
      }
      this.acousticVis.setAnalyser(this.analyser);
      return this.analyser;
    } catch (err) {
      console.warn("Web Audio analysis unavailable for event video:", err);
      this.analyser = null;
      this.acousticVis.setAnalyser(null);
      return null;
    }
  }

  resumeEventAudio() {
    if (this.audioCtx && this.audioCtx.state === "suspended") {
      // Invoked from the user gesture that selects an event (and from the
      // video's play event), so autoplay policies allow the resume.
      this.audioCtx.resume().catch(() => {});
    }
  }

  suspendEventAudio() {
    if (this.audioCtx && this.audioCtx.state === "running") {
      this.audioCtx.suspend().catch(() => {});
    }
  }

  /**
   * Cap the focus stage to the space actually available below the header,
   * switcher and scrubber. The video then flex-shrinks while the controls,
   * acoustic panel and AI card remain visible without page scrolling.
   */
  fitFocusStage() {
    const stage = this.focusStage;
    if (!stage || stage.style.display === "none") return;
    if (stage.classList.contains("maximized") || document.fullscreenElement === stage) {
      stage.classList.remove("is-fitted");
      stage.style.removeProperty("--focus-available-height");
      return;
    }
    const top = stage.getBoundingClientRect().top;
    const available = Math.max(180, Math.round(window.innerHeight - top - 8));
    stage.style.setProperty("--focus-available-height", `${available}px`);
    stage.classList.add("is-fitted");
  }

  /** Narrow screens keep the video tall by defaulting the acoustic panel to a
   *  pinned translucent bottom overlay. An explicit user choice (pin/collapse
   *  buttons or the Settings override) always wins; desktop is unchanged. */
  _isNarrowLayout() {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 640px)").matches
    );
  }

  /** Read the persisted focus-view layout preferences, falling back safely. */
  readFocusPreferences() {
    const prefs = {
      controlsOverlay: true,
      acousticMode: "below",
      acousticPinned: false,
      eventsCollapsed: false,
      aiDetailsVisible: true,
      clockPinned: false,
    };
    try {
      this._acousticUserOverridden =
        localStorage.getItem("lookout_acoustic_user_override") === "1";
      const overlay = localStorage.getItem("lookout_focus_controls_overlay");
      if (overlay !== null) prefs.controlsOverlay = overlay !== "0";
      const storedMode = localStorage.getItem("lookout_acoustic_mode");
      const mode = storedMode === "collapsed" ? "hidden" : storedMode; // legacy alias
      if (mode === "overlay" || mode === "below" || mode === "hidden") {
        prefs.acousticMode = mode;
      }
      const pinned = localStorage.getItem("lookout_acoustic_pinned");
      if (pinned !== null) prefs.acousticPinned = pinned === "1";
      const collapsed = localStorage.getItem("lookout_events_collapsed");
      if (collapsed !== null) prefs.eventsCollapsed = collapsed === "1";
      const aiDetails = localStorage.getItem("lookout_display_ai_details");
      if (aiDetails !== null) prefs.aiDetailsVisible = aiDetails !== "0";
      const clockPinned = localStorage.getItem("lookout_display_clock_pinned");
      if (clockPinned !== null) prefs.clockPinned = clockPinned === "1";
    } catch (_) {}
    if (!this._acousticUserOverridden && this._isNarrowLayout()) {
      prefs.acousticMode = "overlay";
      prefs.acousticPinned = true;
    }
    prefs.acousticPinned = prefs.acousticMode === "overlay";
    return prefs;
  }

  restoreFocusPreferences() {
    this.focusPrefs = this.readFocusPreferences();
    this.applyFocusPreferences();
  }

  /** Apply preferences to the DOM (and to the Settings controls) on load,
   *  on focus entry, and after any preference change. */
  applyFocusPreferences() {
    const prefs = this.focusPrefs;
    if (!prefs) return;
    const container = this.videoContainer || document.querySelector(".video-container");
    if (container) {
      container.classList.toggle("controls-below", !prefs.controlsOverlay);
      container.classList.remove(
        "acoustic-overlay",
        "acoustic-below",
        "acoustic-hidden",
        "acoustic-collapsed"
      );
      container.classList.add(`acoustic-${prefs.acousticMode}`);
    }
    // One 3-state segmented control: exactly one of Overlay / Below / Hidden
    // is active. The mode class on .video-container drives the actual layout.
    if (this.acousticModeControl) {
      this.acousticModeControl.dataset.mode = prefs.acousticMode;
    }
    this.acousticModeButtons.forEach((btn) => {
      const active = btn.dataset.mode === prefs.acousticMode;
      btn.setAttribute("aria-pressed", String(active));
      btn.dataset.active = String(active);
      btn.classList.toggle("active", active);
    });
    const aside = this.timelineAside || document.querySelector(".timeline-section");
    if (aside) aside.classList.toggle("collapsed", prefs.eventsCollapsed);
    if (this.viewerMain) this.viewerMain.classList.toggle("events-collapsed", prefs.eventsCollapsed);

    // Unified Display popover mirrors every preference into its controls.
    if (this.eventsToggle) {
      this.eventsToggle.setAttribute("aria-pressed", String(Boolean(prefs.eventsCollapsed)));
      this.eventsToggle.title = prefs.eventsCollapsed
        ? "Expand events panel"
        : "Collapse events panel";
      this.eventsToggle.textContent = prefs.eventsCollapsed ? "▴ Expand" : "▾ Collapse / Expand";
    }
    if (this.displayAiDetailsToggle) {
      const available = this._displayModeIsEvent();
      this.displayAiDetailsToggle.disabled = !available;
      this.displayAiDetailsToggle.setAttribute("aria-disabled", String(!available));
      this.displayAiDetailsToggle.setAttribute("aria-pressed", String(Boolean(prefs.aiDetailsVisible)));
      this.displayAiDetailsToggle.title = available
        ? prefs.aiDetailsVisible
          ? "Hide AI details for events"
          : "Show AI details for events"
        : "Available for events only";
      this.displayAiDetailsToggle.textContent = prefs.aiDetailsVisible ? "✨ Show" : "✨ Hide";
    }
    if (this.displayClockPinToggle) {
      this.displayClockPinToggle.setAttribute("aria-pressed", String(Boolean(prefs.clockPinned)));
      this.displayClockPinToggle.title = prefs.clockPinned
        ? "Unpin the time readout"
        : "Keep the time readout visible when controls hide";
      this.displayClockPinToggle.textContent = prefs.clockPinned ? "📌 Pinned" : "📌 Unpinned";
    }
    if (this.focusControlOverlay) {
      this.focusControlOverlay.classList.toggle("time-readout-pinned", Boolean(prefs.clockPinned));
    }
    this._applyAiDetailsVisibility();
    this.updateTimeReadout();

    if (this.settingsModal && this.settingsModal.setFocusViewPreferences) {
      this.settingsModal.setFocusViewPreferences(prefs);
    }
    this.fitFocusStage();
  }

  /** True when the focus bar is currently in event mode (AI details apply). */
  _displayModeIsEvent() {
    return Boolean(
      this.focusControlOverlay && this.focusControlOverlay.dataset.mode === "event"
    );
  }

  /** The AI card is owned by event-player.js; a class forces the user's
   *  Display choice without moving or restyling the card itself. */
  _applyAiDetailsVisibility() {
    if (!this.aiDetailsCard) return;
    const show = Boolean(
      this.focusPrefs && this.focusPrefs.aiDetailsVisible && this._displayModeIsEvent()
    );
    this.aiDetailsCard.classList.toggle("ai-details-hidden", !show);
  }

  /** Merge, persist and immediately apply one or more focus preferences. */
  updateFocusPreferences(partial) {
    this.focusPrefs = Object.assign({}, this.focusPrefs || {}, partial);
    if (Object.prototype.hasOwnProperty.call(partial, "acousticMode")) {
      this._acousticUserOverridden = true;
      try {
        localStorage.setItem("lookout_acoustic_user_override", "1");
      } catch (_) {}
    }
    // Derived compatibility value: with one 3-state control, "overlay" is the
    // only pinned state. Kept in storage so older sessions still read cleanly.
    this.focusPrefs.acousticPinned = this.focusPrefs.acousticMode === "overlay";
    try {
      localStorage.setItem(
        "lookout_focus_controls_overlay",
        this.focusPrefs.controlsOverlay ? "1" : "0"
      );
      localStorage.setItem("lookout_acoustic_mode", this.focusPrefs.acousticMode);
      localStorage.setItem(
        "lookout_acoustic_pinned",
        this.focusPrefs.acousticPinned ? "1" : "0"
      );
      localStorage.setItem(
        "lookout_events_collapsed",
        this.focusPrefs.eventsCollapsed ? "1" : "0"
      );
      localStorage.setItem(
        "lookout_display_ai_details",
        this.focusPrefs.aiDetailsVisible ? "1" : "0"
      );
      localStorage.setItem(
        "lookout_display_clock_pinned",
        this.focusPrefs.clockPinned ? "1" : "0"
      );
    } catch (_) {}
    this.applyFocusPreferences();
  }

  // --- Context-aware, auto-hiding player controls --------------------------

  /** The same bar serves live and event media; only the relevant groups show. */
  setPlayerMode(mode) {
    if (!this.focusControlOverlay) return;
    this.focusControlOverlay.setAttribute("data-mode", mode === "event" ? "event" : "live");
    this._applyAiDetailsVisibility();
    if (this.displayAiDetailsToggle) {
      const available = this._displayModeIsEvent();
      this.displayAiDetailsToggle.disabled = !available;
      this.displayAiDetailsToggle.setAttribute("aria-disabled", String(!available));
      this.displayAiDetailsToggle.title = available
        ? this.focusPrefs && this.focusPrefs.aiDetailsVisible
          ? "Hide AI details for events"
          : "Show AI details for events"
        : "Available for events only";
    }
    this.updateTimeReadout();
    this.updatePlayerButtons();
  }

  _activeVideo() {
    if (this.viewMode !== "focus") return null;
    if (this.currentEventItem) {
      return this.eventVideo && this.eventVideo.style.display !== "none"
        ? this.eventVideo
        : null;
    }
    return this.liveVideo && this.liveVideo.style.display !== "none" ? this.liveVideo : null;
  }

  /** True only when the focus view has real media that pause/mute can act on.
   *  A Ring/Wyze snapshot fallback has no frames, so controls must be disabled. */
  _focusMediaAvailable() {
    if (this.currentEventItem) {
      if (this.eventPlayer && typeof this.eventPlayer.isSnapshotOnly === "function") {
        if (this.eventPlayer.isSnapshotOnly()) return false;
      }
      const video = this.eventVideo;
      return Boolean(
        video && video.style.display !== "none" && (video.videoWidth > 0 || video.currentTime > 0)
      );
    }
    if (this.activeCamera && this.livePlayer && typeof this.livePlayer.hasPlayableMedia === "function") {
      return this.livePlayer.hasPlayableMedia();
    }
    return false;
  }

  isPlayerPlaying() {
    const video = this._activeVideo();
    if (!video || video.paused || video.ended) return false;
    if (
      this.currentEventItem &&
      this.eventPlayer &&
      typeof this.eventPlayer.isSnapshotOnly === "function" &&
      this.eventPlayer.isSnapshotOnly()
    ) {
      return false;
    }
    return true;
  }

  _controlHasFocus() {
    const active = document.activeElement;
    return Boolean(
      active && this.focusControlOverlay && this.focusControlOverlay.contains(active)
    );
  }

  /** Idle auto-hide is allowed only while media is playing and neither focus
   *  nor hover is inside the control bar. */
  _autoHideAllowed() {
    return (
      this.isPlayerPlaying() &&
      !this.controlsFocusWithin &&
      !this.controlsHover &&
      !this.displayMenuOpen &&
      !this._controlHasFocus()
    );
  }

  setControlsVisible(visible) {
    this.controlsVisible = Boolean(visible);
    if (!this.focusControlOverlay) return;
    this.focusControlOverlay.classList.toggle("controls-hidden", !this.controlsVisible);
    this.focusControlOverlay.setAttribute("aria-hidden", String(!this.controlsVisible));
    if ("inert" in this.focusControlOverlay) {
      this.focusControlOverlay.inert = !this.controlsVisible;
    }
    if (!this.controlsVisible) {
      // A hidden bar never keeps a popover hanging in mid-air.
      this._hideDisplayMenu();
      this.disarmControlsIdleTimer();
    }
  }

  disarmControlsIdleTimer() {
    if (this.controlsIdleTimer) {
      clearTimeout(this.controlsIdleTimer);
      this.controlsIdleTimer = null;
    }
  }

  armControlsIdleTimer() {
    this.disarmControlsIdleTimer();
    if (!this._autoHideAllowed()) return;
    this.controlsIdleTimer = setTimeout(() => {
      this.controlsIdleTimer = null;
      if (this._autoHideAllowed()) this.setControlsVisible(false);
    }, 3000);
  }

  revealControls() {
    if (!this.controlsVisible) this.setControlsVisible(true);
    this.armControlsIdleTimer();
  }

  /** Tap/click on the video surface toggles the bar. Clicks inside the bar
   *  never hide it - they only reset the idle timer. */
  toggleControls() {
    if (!this.focusControlOverlay) return;
    if (this.focusControlOverlay.classList.contains("controls-hidden")) {
      this.revealControls();
    } else {
      this.setControlsVisible(false);
    }
  }

  // --- Unified Display popover -----------------------------------------------

  _hideDisplayMenu() {
    if (this.displayMenu) this.displayMenu.hidden = true;
    if (this.displayMenuBtn) this.displayMenuBtn.setAttribute("aria-expanded", "false");
    this.displayMenuOpen = false;
  }

  openDisplayMenu() {
    if (!this.displayMenu || !this.displayMenuBtn) return;
    this.displayMenu.hidden = false;
    this.displayMenuBtn.setAttribute("aria-expanded", "true");
    this.displayMenuOpen = true;
    // Focus inside the menu (and displayMenuOpen itself) blocks auto-hide.
    this.revealControls();
    this.disarmControlsIdleTimer();
    const first = this.displayMenu.querySelector("button:not(:disabled)");
    if (first) first.focus();
  }

  closeDisplayMenu({ returnFocus = false, rearm = true } = {}) {
    if (!this.displayMenuOpen) return;
    this._hideDisplayMenu();
    if (returnFocus && this.displayMenuBtn) this.displayMenuBtn.focus();
    if (rearm) this.armControlsIdleTimer();
  }

  toggleDisplayMenu() {
    if (this.displayMenuOpen) this.closeDisplayMenu({ returnFocus: true });
    else this.openDisplayMenu();
  }

  bindDisplayMenu() {
    if (this.displayMenuBtn) {
      this.displayMenuBtn.addEventListener("click", () => this.toggleDisplayMenu());
    }
    if (this.displayMenu) {
      // Clicking a control keeps the popover open so several settings can be
      // adjusted in one visit.
      this.displayMenu.addEventListener("click", () => this.disarmControlsIdleTimer());
    }
    document.addEventListener("keydown", (event) => {
      if (!this.displayMenuOpen) return;
      if (event.key === "Escape") {
        event.preventDefault();
        this.closeDisplayMenu({ returnFocus: true });
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (!this.displayMenuOpen) return;
      const target = event.target;
      if (
        this.displayMenu &&
        this.displayMenu.contains(target)
      ) {
        return;
      }
      if (this.displayMenuBtn && this.displayMenuBtn.contains(target)) return;
      this.closeDisplayMenu();
    });
  }

  // --- Pinnable time readout -------------------------------------------------

  /** Set a readout span's value while preserving its (possibly CSS-hidden)
   *  label element for the accessible/deterministic text contract. */
  _setReadoutText(element, text) {
    if (!element) return;
    const value = element.querySelector(".readout-value");
    if (value) value.textContent = text;
    else element.textContent = text;
  }

  /** Clock ticks once per second; event details follow the selected event. */
  updateTimeReadout() {
    if (!this.focusTimeReadout) return;
    const now = new Date();
    const clockOptions = {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    };
    const homeZone = getDisplayTimezone();
    const localClock = formatInBrowserZone(now, clockOptions);
    this._setReadoutText(this.focusClockLocal, localClock);
    if (this.focusClockHome) {
      if (homeZone) {
        this.focusClockHome.hidden = false;
        this._setReadoutText(
          this.focusClockHome,
          formatInDisplayZone(now, clockOptions)
        );
      } else {
        this.focusClockHome.hidden = true;
        this._setReadoutText(this.focusClockHome, "");
      }
    }
    this.focusTimeReadout.title = homeZone
      ? `Browser local time: ${formatInBrowserZone(now, {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          ...clockOptions,
        })} · Home ${homeZone}: ${formatInDisplayZone(now, {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          ...clockOptions,
        })}`
      : formatLocalClockTitle(now) || `Browser local time: ${localClock}`;

    const item = this._displayModeIsEvent() ? this.currentEventItem : null;
    if (this.focusEventTime) {
      if (item && item.timestamp) {
        // Compact bar widths keep only the clock part of the timestamp; the
        // full date/time remains in the tooltip and on wider layouts.
        const options = window.innerWidth <= 960
          ? { hour: "numeric", minute: "2-digit" }
          : undefined;
        const info = eventTimeWithLocalHint(item.timestamp, options);
        this.focusEventTime.hidden = false;
        this._setReadoutText(this.focusEventTime, info.primary);
        this.focusEventTime.title = info.localTitle || "";
      } else {
        this.focusEventTime.hidden = true;
        this._setReadoutText(this.focusEventTime, "");
        this.focusEventTime.removeAttribute("title");
      }
    }
    if (this.focusEventAge) {
      if (item && item.timestamp && this.timeline) {
        this.focusEventAge.hidden = false;
        this.focusEventAge.textContent = this.timeline.formatRelativeTime(item.timestamp);
      } else {
        this.focusEventAge.hidden = true;
        this.focusEventAge.textContent = "";
      }
    }
  }

  bindTimeReadout() {
    this.updateTimeReadout();
    if (this._timeReadoutTimer) return;
    this._timeReadoutTimer = setInterval(() => this.updateTimeReadout(), 1000);
  }

  bindControlAutoHide() {
    const stage = this.focusStage;
    const overlay = this.focusControlOverlay;
    if (stage) {
      stage.addEventListener("pointermove", () => {
        if (this.viewMode === "focus") this.revealControls();
      });
      stage.addEventListener("focusin", () => {
        if (this.viewMode !== "focus") return;
        this.controlsFocusWithin = Boolean(overlay && overlay.contains(document.activeElement));
        this.revealControls();
        if (this.controlsFocusWithin) this.disarmControlsIdleTimer();
      });
      stage.addEventListener("focusout", () => {
        // relatedTarget is resolved after the event, so defer the re-check.
        Promise.resolve().then(() => {
          this.controlsFocusWithin = Boolean(overlay && overlay.contains(document.activeElement));
          if (this.controlsFocusWithin) this.disarmControlsIdleTimer();
          else this.armControlsIdleTimer();
        });
      });
    }
    if (this.videoSurface) {
      this.videoSurface.addEventListener("click", (event) => {
        if (this.viewMode !== "focus") return;
        event.stopPropagation();
        this.toggleControls();
      });
    }
    if (overlay) {
      overlay.addEventListener("click", () => this.revealControls());
      overlay.addEventListener("pointerenter", () => {
        this.controlsHover = true;
        this.revealControls();
        this.disarmControlsIdleTimer();
      });
      overlay.addEventListener("pointerleave", () => {
        this.controlsHover = false;
        this.armControlsIdleTimer();
      });
    }
    document.addEventListener("keydown", (event) => this._handleSpaceKey(event));
  }

  _bindNarrowAcousticDefault() {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 640px)");
    if (!query || !query.addEventListener) return;
    query.addEventListener("change", () => {
      if (this._acousticUserOverridden) return;
      this.focusPrefs.acousticMode = query.matches ? "overlay" : "below";
      this.focusPrefs.acousticPinned = query.matches;
      this.applyFocusPreferences();
    });
  }

  // --- Session-only pause / mute -------------------------------------------

  toggleGlobalPaused() {
    if (!this.liveGrid || this.globalPauseUnavailable) return;
    this.liveGrid.setGlobalPaused(!this.liveGrid.globalPaused);
    this.updateGlobalPauseButton();
    this.applyLivePauseState();
  }

  updateGlobalPauseButton() {
    const btn = this.globalPauseBtn;
    if (!btn || !this.liveGrid) return;
    const paused = this.liveGrid.globalPaused;
    const label = paused ? "Resume all live streams" : "Pause all live streams";
    // No camera currently has playable live media (e.g. all tiles are in
    // snapshot fallback): the global pause must not pretend to do anything.
    const hasPlayable =
      Boolean(this.currentEventItem) ||
      (typeof this.liveGrid.hasPlayableCamera === "function" && this.liveGrid.hasPlayableCamera());
    this.globalPauseUnavailable = !hasPlayable;
    btn.disabled = !hasPlayable;
    btn.setAttribute("aria-disabled", String(!hasPlayable));
    btn.setAttribute("aria-pressed", String(paused));
    btn.setAttribute("aria-label", label);
    btn.title = hasPlayable ? label : liveUnavailableControlTitle();
    btn.dataset.icon = paused ? "▶" : "⏸";
    btn.textContent = paused ? "▶ Resume Live" : "⏸ Pause Live";
  }

  toggleCameraPaused(cameraId) {
    if (!cameraId || !this.liveGrid) return;
    this.liveGrid.setPaused(cameraId, !this.liveGrid.isPaused(cameraId));
    this.applyLivePauseState();
  }

  applyLivePauseState() {
    if (!this.activeCamera || this.currentEventItem) return;
    if (this.livePlayer) {
      this.livePlayer.setPaused(this.liveGrid.isPaused(this.activeCamera.id));
      this.livePlayer.setMuted(this.liveGrid.isMuted(this.activeCamera.id));
    }
    this.updatePlayerButtons();
  }

  togglePlayerPaused() {
    if (!this._focusMediaAvailable()) return;
    if (this.currentEventItem) {
      this.eventPaused = !this.eventPaused;
      this.eventPlayer.setPaused(this.eventPaused);
    } else if (this.activeCamera) {
      this.toggleCameraPaused(this.activeCamera.id);
    }
    this.updatePlayerButtons();
    this.revealControls();
  }

  togglePlayerMuted() {
    if (!this._focusMediaAvailable()) return;
    if (this.currentEventItem) {
      const cameraId = this.currentEventItem.camera_id;
      this.eventMuted = !this.eventMutedByCamera.get(cameraId);
      this.eventMutedByCamera.set(cameraId, this.eventMuted);
      this.eventPlayer.setMuted(this.eventMuted);
    } else if (this.activeCamera) {
      const id = this.activeCamera.id;
      this.liveGrid.setMuted(id, !this.liveGrid.isMuted(id));
      this.livePlayer.setMuted(this.liveGrid.isMuted(id));
    }
    this.updatePlayerButtons();
    this.revealControls();
  }

  updatePlayerButtons() {
    const isEvent = Boolean(this.currentEventItem);
    let paused = this.eventPaused;
    let muted = this.eventMuted;
    if (!isEvent && this.activeCamera && this.liveGrid) {
      paused = this.liveGrid.isPaused(this.activeCamera.id);
      muted = this.liveGrid.isMuted(this.activeCamera.id);
    }
    // Snapshot fallback / no media: keep the buttons visible but disabled so
    // they never flip state with nothing to pause or mute.
    const playable = this._focusMediaAvailable();
    const disabledTitle = liveUnavailableControlTitle();
    if (this.playerPauseBtn) {
      this.playerPauseBtn.disabled = !playable;
      this.playerPauseBtn.setAttribute("aria-disabled", String(!playable));
    }
    if (this.playerMuteBtn) {
      this.playerMuteBtn.disabled = !playable;
      this.playerMuteBtn.setAttribute("aria-disabled", String(!playable));
    }
    if (this.playerPauseBtn) {
      const label = paused ? "Resume video" : "Pause video";
      this.playerPauseBtn.setAttribute("aria-pressed", String(paused));
      this.playerPauseBtn.setAttribute("aria-label", label);
      this.playerPauseBtn.title = playable ? label : disabledTitle;
      this.playerPauseBtn.dataset.icon = paused ? "▶" : "⏸";
      this.playerPauseBtn.textContent = paused ? "▶ Resume" : "⏸ Pause";
    }
    if (this.playerMuteBtn) {
      const label = muted ? "Unmute video" : "Mute video";
      this.playerMuteBtn.setAttribute("aria-pressed", String(muted));
      this.playerMuteBtn.setAttribute("aria-label", label);
      this.playerMuteBtn.title = playable ? label : disabledTitle;
      this.playerMuteBtn.dataset.icon = muted ? "🔇" : "🔊";
      this.playerMuteBtn.textContent = muted ? "🔇 Muted" : "🔊 Sound";
    }
  }

  /** Space toggles the focused player/tile, never a focused interactive control. */
  _handleSpaceKey(event) {
    if (event.code !== "Space" && event.key !== " ") return;
    const active = document.activeElement;
    if (
      active &&
      active.closest &&
      active.closest(
        "button, input, select, textarea, a[href], [contenteditable='true'], [role='button']"
      )
    ) {
      return;
    }
    const tile = active && active.closest ? active.closest(".camera-grid-tile") : null;
    if (tile && this.viewMode === "grid") {
      event.preventDefault();
      this.toggleCameraPaused(tile.dataset.cameraId);
      return;
    }
    const surface =
      active && active.closest ? active.closest("#video-surface, .video-container") : null;
    if (surface && this.viewMode === "focus") {
      event.preventDefault();
      this.togglePlayerPaused();
    }
  }

  bindGlobalEvents() {
    this.restoreFocusPreferences();
    this.bindControlAutoHide();
    this.bindDisplayMenu();
    this.bindTimeReadout();
    this._bindNarrowAcousticDefault();

    if (this.viewGridBtn) {
      this.viewGridBtn.addEventListener("click", () => this.switchToGrid());
    }

    if (this.viewFocusBtn) {
      this.viewFocusBtn.addEventListener("click", () => this.switchToFocus());
    }

    if (this.backToGridBtn) {
      this.backToGridBtn.addEventListener("click", () => this.switchToGrid());
    }
    if (this.focusSettingsBtn) {
      this.focusSettingsBtn.addEventListener("click", () => {
        if (this.activeCamera) this.openCameraSettings(this.activeCamera);
      });
    }
    const brandHome = document.getElementById("brand-home-link");
    if (brandHome) {
      brandHome.addEventListener("click", (e) => { e.preventDefault(); this.switchToGrid(); });
    }

    if (this.fullscreenBtn) {
      this.fullscreenBtn.addEventListener("click", () => this.toggleFullscreen());
    }
    if (this.maximizeBtn) {
      this.maximizeBtn.addEventListener("click", () => this.toggleMaximize());
    }

    if (this.globalPauseBtn) {
      this.globalPauseBtn.addEventListener("click", () => this.toggleGlobalPaused());
    }
    if (this.playerPauseBtn) {
      this.playerPauseBtn.addEventListener("click", () => this.togglePlayerPaused());
    }
    if (this.playerMuteBtn) {
      this.playerMuteBtn.addEventListener("click", () => this.togglePlayerMuted());
    }
    // Media can become (un)playable after the state setters ran; re-evaluate
    // the pause/mute enabled state whenever a video element changes readiness.
    [this.liveVideo, this.eventVideo].filter(Boolean).forEach((video) => {
      ["playing", "loadeddata", "error", "emptied"].forEach((name) => {
        video.addEventListener(name, () => this.updatePlayerButtons());
      });
    });
    this.updateGlobalPauseButton();
    this.updatePlayerButtons();

    // One 3-state acoustic control: Overlay / Below / Hidden. Selecting a
    // mode applies immediately, persists, and mirrors into Settings.
    this.acousticModeButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.dataset.mode;
        if (mode !== "overlay" && mode !== "below" && mode !== "hidden") return;
        this.updateFocusPreferences({ acousticMode: mode });
      });
    });

    // Keep the focus stage inside the viewport as chrome wraps/resizes and
    // when entering or leaving native fullscreen.
    window.addEventListener("resize", () => this.fitFocusStage());
    document.addEventListener("fullscreenchange", () => this.fitFocusStage());

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
        // A user Retry click always gets a fresh bounded attempt budget.
        this.livePlayer.liveAttempts = 0;
        this.livePlayer.startLive();
      });
    }

    if (this.openSettingsBtn) {
      this.openSettingsBtn.addEventListener("click", () => {
        this.settingsModal.open();
      });
    }

    // Collapsible events panel. The collapsed state is one of the persisted
    // focus-view preferences, shared with the Settings modal checkbox. The
    // button now lives in the unified Display popover but keeps its ID.
    if (this.eventsToggle) {
      this.eventsToggle.addEventListener("click", () => {
        this.updateFocusPreferences({
          eventsCollapsed: !this.focusPrefs.eventsCollapsed,
        });
      });
    }

    if (this.displayAiDetailsToggle) {
      this.displayAiDetailsToggle.addEventListener("click", () => {
        if (this.displayAiDetailsToggle.disabled || !this._displayModeIsEvent()) return;
        this.updateFocusPreferences({
          aiDetailsVisible: !this.focusPrefs.aiDetailsVisible,
        });
      });
    }

    if (this.displayClockPinToggle) {
      this.displayClockPinToggle.addEventListener("click", () => {
        this.updateFocusPreferences({ clockPinned: !this.focusPrefs.clockPinned });
      });
    }

    // Narrow layouts: jump straight to events instead of scrolling past every tile.
    if (this.jumpToEventsBtn) {
      this.jumpToEventsBtn.addEventListener("click", () => {
        const aside = this.timelineAside || document.querySelector(".timeline-section");
        if (!aside) return;
        if (this.focusPrefs.eventsCollapsed) {
          this.updateFocusPreferences({ eventsCollapsed: false });
        }
        aside.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }

    // Fluid events drawer: drag the separator or use ArrowLeft/ArrowRight.
    if (this.eventsResizeHandle && this.viewerMain) {
      const clampPanelWidth = (px) => {
        const max = Math.min(900, window.innerWidth * 0.6);
        return Math.max(280, Math.min(max, px));
      };
      const setPanelWidth = (px) => {
        this.viewerMain.style.setProperty(
          "--events-panel-width",
          `${Math.round(clampPanelWidth(px))}px`
        );
      };
      let startX = 0;
      let startWidth = 0;

      this.eventsResizeHandle.addEventListener("pointerdown", (e) => {
        const aside = this.timelineAside || document.querySelector(".timeline-section");
        startX = e.clientX;
        startWidth = aside ? aside.getBoundingClientRect().width : 320;
        if (this.eventsResizeHandle.setPointerCapture) this.eventsResizeHandle.setPointerCapture(e.pointerId);
        e.preventDefault();
      });
      this.eventsResizeHandle.addEventListener("pointermove", (e) => {
        if (!this.eventsResizeHandle.hasPointerCapture || !this.eventsResizeHandle.hasPointerCapture(e.pointerId)) return;
        setPanelWidth(startWidth - (e.clientX - startX));
      });
      const endResize = (e) => {
        if (this.eventsResizeHandle.hasPointerCapture && this.eventsResizeHandle.hasPointerCapture(e.pointerId)) {
          this.eventsResizeHandle.releasePointerCapture(e.pointerId);
        }
      };
      this.eventsResizeHandle.addEventListener("pointerup", endResize);
      this.eventsResizeHandle.addEventListener("pointercancel", endResize);
      this.eventsResizeHandle.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const aside = this.timelineAside || document.querySelector(".timeline-section");
        const current = aside ? aside.getBoundingClientRect().width : 320;
        setPanelWidth(current + (e.key === "ArrowLeft" ? 16 : -16));
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
    // Returning to live clears global and per-camera pause overrides.
    if (this.liveGrid) this.liveGrid.setGlobalPaused(false);
    this.eventPaused = false;
    this.eventMuted = false;
    if (this.livePlayer) this.livePlayer.setPaused(false);
    this.setPlayerMode("live");
    this.updateGlobalPauseButton();
    this.setControlsVisible(true);
    if (this.eventPlayer) this.eventPlayer.stop();
    // Stop analysing the event clip; keep the Web Audio graph for reuse.
    this.acousticVis.stop();
    this.suspendEventAudio();
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
    this.suspendEventAudio();
    this.setPlayerMode("live");
    this.fetchEvents();
    // The Display popover is now header-anchored and available in Grid too, so
    // mirror the persisted view-state into it on every Grid entry.
    this.applyFocusPreferences();
  }

  switchToFocus(cam = null, { autoStartLive = true, fetchTimeline = true } = {}) {
    this.viewMode = "focus";
    this.setPlayerMode(autoStartLive ? "live" : this.currentEventItem ? "event" : "live");
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
    // Mirror the effective per-camera pause state onto the focus player.
    this.applyLivePauseState();
    this.setControlsVisible(true);
    // Re-apply saved layout preferences each time focus view is entered.
    this.applyFocusPreferences();
    this.fitFocusStage();
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
    this.eventPaused = false;
    this.eventMuted = Boolean(this.eventMutedByCamera.get(eventItem.camera_id));
    this.setPlayerMode("event");
    this.updatePlayerButtons();

    if (this.zoomController) this.zoomController.resetZoom(false);
    this.switchToFocus(cam, { autoStartLive: false, fetchTimeline: false });
    this.livePlayer.stopLive();

    if (this.returnToLiveBtn) this.returnToLiveBtn.style.display = "inline-flex";

    if (this.snapshotOverlay) this.snapshotOverlay.style.display = "none";
    if (this.liveVideo) this.liveVideo.style.display = "none";
    if (this.eventVideo) this.eventVideo.style.display = "block";

    // Real Web Audio analysis of the playing video. Built once, on the user
    // gesture that selected this event; the graph is reused for later events.
    this.setupEventAudio();
    this.resumeEventAudio();
    this.eventPlayer.loadEvent(eventItem);
    this.eventPlayer.setMuted(this.eventMuted);

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

    // The analyser (not event metadata) is now the only source of the VU and
    // waveform values.
    this.acousticVis.start();
    this.fitFocusStage();
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
    this.fitFocusStage();
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
    if (this.csMotionTagGroup) {
      this.csMotionTagGroup.style.display = "none";
      if (this.csMotionTagStatus) this.csMotionTagStatus.textContent = "";
    }
    this._motionTagBaseline = null;
    if (cam && (cam.native_provider === "wyze" || cam.provider_badge === "wyze")) {
      this.loadMotionTagSupport(cam);
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

  async loadMotionTagSupport(cam) {
    if (!this.csMotionTagGroup) return;
    this.csMotionTagGroup.style.display = "none";
    this._motionTagBaseline = null;
    if (!cam || !cam.id) return;
    try {
      const res = await fetch(resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/motion_tag`));
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !data.supported) {
        if (this.csMotionTagStatus && data && data.enabled === null) {
          this.csMotionTagStatus.textContent = "Not supported on this camera";
        }
        return;
      }
      this.csMotionTagGroup.style.display = "";
      if (this.csCamMotionTag) this.csCamMotionTag.checked = data.enabled === true;
      this._motionTagBaseline = data.enabled === true;
      if (this.csMotionTagStatus) this.csMotionTagStatus.textContent = "";
    } catch (err) {
      console.warn("Motion tag probe failed:", err);
    }
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
    if (
      this.csMotionTagGroup &&
      this.csMotionTagGroup.style.display !== "none" &&
      this.csCamMotionTag &&
      this.csCamMotionTag.checked !== this._motionTagBaseline
    ) {
      try {
        const mtRes = await fetch(
          resolveUrl(`/api/cameras/${encodeURIComponent(cam.id)}/motion_tag`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: this.csCamMotionTag.checked }),
          }
        );
        if (mtRes.ok) {
          const mtData = await mtRes.json();
          cam.motion_tag_enabled = mtData.enabled === true;
          this._motionTagBaseline = cam.motion_tag_enabled;
        }
      } catch (err) {
        console.warn("Motion tag save failed:", err);
      }
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
        this.applyLivePauseState();
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
      // Capture the server total before local camera filtering shrinks the page.
      this.eventsTotal = Number(data.total) || events.length;

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
      if (this.scrubber) this.scrubber.setEvents(this.events, { total: this.eventsTotal });

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
        if (isNew) this.eventsTotal += 1;
        if (this.scrubber) this.scrubber.setEvents(this.events, { total: this.eventsTotal });

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

  /** Read only the non-secret viewer preference exposed by the public config
   *  endpoint. Any failure keeps the previous browser-local behaviour. */
  async loadPublicConfig() {
    try {
      const res = await fetch(resolveUrl("/api/config/public"));
      if (!res.ok) return;
      const data = await res.json();
      setDisplayTimezone(typeof data.display_timezone === "string" ? data.display_timezone : "");
      this.updateTimeReadout();
    } catch (err) {
      console.warn("Public viewer config unavailable; using browser-local time", err);
    }
  }

  async start() {
    await this.loadPublicConfig();
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
