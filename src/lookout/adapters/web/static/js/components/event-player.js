/**
 * Recorded event video player and dedicated snapshot card presentation component.
 */
import { resolveUrl } from "../utils/url.js";
import { ZoomController } from "./zoom-controller.js";

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
    this.initLightbox();
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

    this.video.addEventListener("error", () => {
      console.warn("Event video failed to load, falling back to snapshot card:", this.video.src);
      if (this.currentEvent) {
        this.renderSnapshotCard(this.currentEvent);
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

  stop() {
    if (this.video) {
      this.video.pause();
      this.video.style.display = "none";
      this.video.removeAttribute("src");
      this.video.load();
    }
    if (this.snapshotCard) {
      this.snapshotCard.style.display = "none";
    }
    const aiDetails = document.getElementById("focus-ai-details-card");
    if (aiDetails) {
      aiDetails.style.display = "none";
    }
    this.currentEvent = null;
  }

  renderSnapshotCard(eventItem) {
    this.updateAiDetailsCard(eventItem);

    if (this.video) {
      this.video.pause();
      this.video.style.display = "none";
      this.video.removeAttribute("src");
    }

    if (this.snapshotCard) {
      this.snapshotCard.style.display = "flex";

      if (this.snapshotImg) {
        const rawThumb =
          eventItem.snapshot_url ||
          eventItem.snapshot_uri ||
          eventItem.gif_url ||
          (eventItem.camera_id
            ? `/media/${eventItem.camera_id}/${eventItem.id}.jpg`
            : `/media/events/${eventItem.id}.jpg`);
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

    // Controls Bar Adaptation: Hide video controls, display snapshot actions
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
  }

  loadEvent(eventItem) {
    this.currentEvent = eventItem;
    this.updateAiDetailsCard(eventItem);

    const isPurged = Boolean(eventItem.metadata && eventItem.metadata.media_purged);
    const hasExplicitVideo = Boolean(
      eventItem.media_uri ||
      eventItem.clip_url ||
      eventItem.video_url ||
      (eventItem.media_type === "video" && `/media/events/${eventItem.id}.mp4`)
    );
    const videoUrl = (!isPurged && hasExplicitVideo)
      ? (eventItem.media_uri ||
         eventItem.clip_url ||
         eventItem.video_url ||
         `/media/events/${eventItem.id}.mp4`)
      : null;

    if (!videoUrl || isPurged) {
      this.renderSnapshotCard(eventItem);
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

    const posterUrl =
      eventItem.snapshot_url ||
      eventItem.snapshot_uri ||
      eventItem.gif_url ||
      "";
    if (posterUrl) {
      this.video.poster = resolveUrl(posterUrl);
    }

    this.video.src = resolveUrl(videoUrl);
    this.video.playbackRate = this.currentSpeed;
    this.video.loop = this.isLooping;

    this.video.onerror = () => {
      console.warn("Video failed to load/decode, falling back to snapshot card");
      this.renderSnapshotCard(eventItem);
    };

    this.video.load();
    const playPromise = this.video.play();
    if (playPromise !== undefined) {
      playPromise.catch(() => {
        this.video.muted = true;
        this.video.play().catch(() => {});
      });
    }
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
    const rawThumb =
      this.currentEvent.snapshot_url ||
      this.currentEvent.snapshot_uri ||
      this.currentEvent.gif_url ||
      (this.currentEvent.camera_id
        ? `/media/${this.currentEvent.camera_id}/${this.currentEvent.id}.jpg`
        : `/media/events/${this.currentEvent.id}.jpg`);
    const a = document.createElement("a");
    a.href = resolveUrl(rawThumb);
    a.download = `${this.currentEvent.camera_id}_${this.currentEvent.id}_snapshot.jpg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  initLightbox() {
    this.lightboxModal = document.getElementById("snapshot-lightbox-modal");
    this.lightboxImg = document.getElementById("lightbox-img");
    this.lightboxFilename = document.getElementById("lightbox-filename");
    this.lightboxDownload = document.getElementById("lightbox-download");
    this.lightboxZoomLevel = document.getElementById("lightbox-zoom-level");
    this.lightboxViewport = document.getElementById("lightbox-viewport");

    if (!this.lightboxModal || !this.lightboxViewport || !this.lightboxImg) return;

    this.lightboxZoom = new ZoomController(this.lightboxViewport, {
      customTargetEl: this.lightboxImg,
      showHud: false,
      onZoomChange: (zoom) => {
        if (this.lightboxZoomLevel) {
          this.lightboxZoomLevel.textContent = `${zoom.toFixed(1)}x`;
        }
      },
    });

    const closeBtn = document.getElementById("lightbox-close-btn");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => this.closeLightbox());
    }

    this.lightboxModal.addEventListener("click", (e) => {
      if (e.target === this.lightboxModal) {
        this.closeLightbox();
      }
    });

    const zoomInBtn = document.getElementById("lightbox-zoom-in");
    if (zoomInBtn) {
      zoomInBtn.addEventListener("click", () => this.lightboxZoom.zoomIn(0.5));
    }

    const zoomOutBtn = document.getElementById("lightbox-zoom-out");
    if (zoomOutBtn) {
      zoomOutBtn.addEventListener("click", () => this.lightboxZoom.zoomOut(0.5));
    }

    const resetBtn = document.getElementById("lightbox-reset");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => this.lightboxZoom.resetZoom(true));
    }

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.lightboxModal && this.lightboxModal.style.display !== "none") {
        this.closeLightbox();
      }
    });
  }

  openLightbox(url, title = "") {
    if (!this.lightboxModal || !this.lightboxImg) return;
    this.lightboxImg.src = url;
    if (this.lightboxFilename) this.lightboxFilename.textContent = title;
    if (this.lightboxDownload) {
      this.lightboxDownload.href = url;
      this.lightboxDownload.download = title || "snapshot.jpg";
    }
    this.lightboxModal.style.display = "flex";
    if (this.lightboxZoom) {
      this.lightboxZoom.resetZoom(false);
    }
  }

  closeLightbox() {
    if (!this.lightboxModal) return;
    this.lightboxModal.style.display = "none";
    if (this.lightboxZoom) {
      this.lightboxZoom.resetZoom(false);
    }
  }

  inspectFrame() {
    if (!this.currentEvent) return;
    const rawThumb =
      this.currentEvent.snapshot_url ||
      this.currentEvent.snapshot_uri ||
      this.currentEvent.gif_url ||
      (this.currentEvent.camera_id
        ? `/media/${this.currentEvent.camera_id}/${this.currentEvent.id}.jpg`
        : `/media/events/${this.currentEvent.id}.jpg`);
    const dateStr = this.currentEvent.timestamp ? new Date(this.currentEvent.timestamp).toLocaleTimeString() : "";
    const title = `${this.currentEvent.camera_id || "Camera"} - ${dateStr}`;
    this.openLightbox(resolveUrl(rawThumb), title);
  }

  updateAiDetailsCard(eventItem) {
    updateAiDetailsCard(eventItem);
  }
}

export function updateAiDetailsCard(eventItem) {
  const card = document.getElementById("focus-ai-details-card");
  if (!card) return;
  if (!eventItem) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";

  // 1. Model provenance badge
  const modelBadge = document.getElementById("ai-model-badge");
  if (modelBadge) {
    let provText = "Local (moondream)";
    if (eventItem.provenance && eventItem.provenance.length > 0) {
      const p = eventItem.provenance[eventItem.provenance.length - 1];
      const prov = (p.provider || "").toLowerCase();
      const model = p.model_name || "";
      if (prov.includes("gemini")) {
        provText = model ? `Gemini (${model})` : "Gemini Flash";
      } else if (prov.includes("opencode")) {
        provText = `OpenCode (${model || "deepseek"})`;
      } else if (prov.includes("tpu") || prov.includes("edge")) {
        provText = "Edge TPU";
      } else if (prov.includes("npu") || prov.includes("hailo")) {
        provText = "Edge NPU";
      } else if (prov.includes("ollama") || prov.includes("local")) {
        provText = `Local (${model || "moondream"})`;
      } else {
        provText = `${p.provider} (${model})`;
      }
    } else if (eventItem.metadata && eventItem.metadata.legacy && eventItem.metadata.legacy.inference_provider) {
      provText = String(eventItem.metadata.legacy.inference_provider);
    }
    modelBadge.textContent = provText;
  }

  // 2. Threat tier badge: NONE, LOW, ELEVATED, HIGH
  const threatBadge = document.getElementById("ai-threat-badge");
  if (threatBadge) {
    let rawTier = eventItem.threat_level;
    let label = "NONE";
    let cssTier = "none";
    if (typeof rawTier === "string") {
      const s = rawTier.toLowerCase();
      if (s === "high" || s === "critical" || s === "3" || s === "4") {
        label = "HIGH";
        cssTier = "high";
      } else if (s === "elevated" || s === "medium" || s === "2") {
        label = "ELEVATED";
        cssTier = "elevated";
      } else if (s === "low" || s === "1") {
        label = "LOW";
        cssTier = "low";
      } else {
        label = "NONE";
        cssTier = "none";
      }
    } else if (typeof rawTier === "number") {
      if (rawTier >= 3) {
        label = "HIGH";
        cssTier = "high";
      } else if (rawTier === 2) {
        label = "ELEVATED";
        cssTier = "elevated";
      } else if (rawTier === 1) {
        label = "LOW";
        cssTier = "low";
      } else {
        label = "NONE";
        cssTier = "none";
      }
    }
    threatBadge.textContent = label;
    threatBadge.className = `ai-threat-badge tier-${cssTier}`;
  }

  // 3. Processing metrics
  const infEl = document.getElementById("metric-inference-ms");
  const clipEl = document.getElementById("metric-clip-ms");
  const gifEl = document.getElementById("metric-gif-ms");
  const totalEl = document.getElementById("metric-total-ms");

  const metrics = eventItem.metrics || {};
  const legacy = (eventItem.metadata && eventItem.metadata.legacy) || {};
  const meta = eventItem.metadata || {};

  // Inference ms
  let infMs = null;
  if (eventItem.provenance && eventItem.provenance.length > 0) {
    infMs = eventItem.provenance[0].latency_ms;
  } else if (metrics.inference_latency_ms !== undefined) {
    infMs = metrics.inference_latency_ms;
  } else if (legacy.inference_time_ms !== undefined) {
    infMs = legacy.inference_time_ms;
  }
  if (infEl) {
    infEl.textContent = infMs != null && !isNaN(infMs) ? Math.round(infMs) : "--";
  }

  // Clip ms
  let clipMs = meta.clip_ms || legacy.clip_duration_ms || metrics.clip_generation_ms || null;
  if (clipEl) {
    clipEl.textContent = clipMs != null && !isNaN(clipMs) ? Math.round(clipMs) : "--";
  }

  // GIF ms
  let gifMs = meta.gif_ms || legacy.gif_duration_ms || metrics.gif_generation_ms || null;
  if (gifEl) {
    gifEl.textContent = gifMs != null && !isNaN(gifMs) ? Math.round(gifMs) : "--";
  }

  // Total ms
  let totalMs = metrics.total_latency_ms || null;
  if ((totalMs == null || isNaN(totalMs) || totalMs === 0) && infMs != null) {
    totalMs = infMs + (clipMs || 0) + (gifMs || 0);
  }
  if (totalEl) {
    totalEl.textContent = totalMs != null && !isNaN(totalMs) && totalMs > 0 ? Math.round(totalMs) : (infMs != null && !isNaN(infMs) ? Math.round(infMs) : "--");
  }

  // 4. Taxonomy chips row: detected objects / matched entities
  const chipsEl = document.getElementById("ai-details-chips");
  if (chipsEl) {
    chipsEl.innerHTML = "";
    const chipItems = [];

    // Matched entities
    if (eventItem.matched_entities && Array.isArray(eventItem.matched_entities)) {
      eventItem.matched_entities.forEach((entity) => {
        const lower = entity.toLowerCase();
        let icon = "🏷️";
        let label = entity;
        if (lower.includes("mochi")) {
          icon = "🐾";
          label = "Mochi (Family Dog)";
        } else if (lower.includes("dog") || lower.includes("pet")) {
          icon = "🐾";
        } else if (lower.includes("courier") || lower.includes("package") || lower.includes("delivery")) {
          icon = "📦";
          label = label.toLowerCase().includes("package") ? label : `${label} Package`;
        } else if (lower.includes("person") || lower.includes("resident")) {
          icon = "👤";
        } else if (lower.includes("car") || lower.includes("civic") || lower.includes("vehicle")) {
          icon = "🚗";
        }
        chipItems.push({ icon, label });
      });
    }

    // Category fallback if no matched entities
    if (chipItems.length === 0 && eventItem.category) {
      const cat = eventItem.category.toLowerCase();
      if (cat.includes("courier") || cat.includes("package")) {
        chipItems.push({ icon: "📦", label: "Courier Package" });
      } else if (cat.includes("resident")) {
        chipItems.push({ icon: "👤", label: "Person" });
      } else if (cat.includes("pet") || cat.includes("dog")) {
        chipItems.push({ icon: "🐾", label: "Mochi (Family Dog)" });
      } else if (cat.includes("vehicle") || cat.includes("car")) {
        chipItems.push({ icon: "🚗", label: "Vehicle" });
      } else {
        chipItems.push({ icon: "👁️", label: eventItem.category.replace(/_/g, " ") });
      }
    }

    // Bounding boxes if available
    if (metrics.bounding_boxes && Array.isArray(metrics.bounding_boxes)) {
      metrics.bounding_boxes.forEach((box) => {
        if (box.label && !chipItems.some(c => c.label.toLowerCase().includes(box.label.toLowerCase()))) {
          let icon = "🎯";
          if (box.label.toLowerCase().includes("person")) icon = "👤";
          else if (box.label.toLowerCase().includes("dog")) icon = "🐾";
          else if (box.label.toLowerCase().includes("package")) icon = "📦";
          chipItems.push({ icon, label: `${box.label}` });
        }
      });
    }

    chipItems.forEach((c) => {
      const chipSpan = document.createElement("span");
      chipSpan.className = "ai-chip";
      chipSpan.textContent = `${c.icon} ${c.label}`;
      chipsEl.appendChild(chipSpan);
    });
  }

  // 5. Description summary
  const summaryEl = document.getElementById("ai-summary-text");
  if (summaryEl) {
    summaryEl.textContent = eventItem.summary || "Event detected and cataloged.";
  }
}
