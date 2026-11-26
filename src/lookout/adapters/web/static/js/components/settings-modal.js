/**
 * Admin settings modal and face gallery management component.
 */
import { resolveUrl } from "../utils/url.js";

export class SettingsModal {
  constructor({ modalBackdrop, onClose, onAiModeChanged, onFocusViewChanged }) {
    this.modal = modalBackdrop;
    this.onClose = onClose;
    this.onAiModeChanged = onAiModeChanged;
    this.onFocusViewChanged = onFocusViewChanged;

    this.focusControlsToggle = this.modal ? this.modal.querySelector("#setting-focus-controls-overlay") : null;
    this.acousticModeSelect = this.modal ? this.modal.querySelector("#setting-acoustic-mode") : null;
    this.eventsCollapsedToggle = this.modal ? this.modal.querySelector("#setting-events-collapsed") : null;

    this.aiSelect = this.modal ? this.modal.querySelector("#setting-ai-mode") : null;
    this.dropzone = this.modal ? this.modal.querySelector("#face-dropzone") : null;
    this.facesContainer = this.modal ? (this.modal.querySelector("#face-gallery-list") || this.modal.querySelector("#faces-grid")) : null;
    this.entitiesContainer = this.modal ? this.modal.querySelector("#entity-catalog-list") : null;
    this.integrationsContainer = this.modal ? this.modal.querySelector("#sidecar-integrations-list") : null;
    this.cameraNotificationsContainer = this.modal ? this.modal.querySelector("#camera-notifications-list") : null;
    this.homeAssistantContainer = this.modal ? this.modal.querySelector("#home-assistant-settings") : null;
    this.fileInput = this.modal ? this.modal.querySelector("#face-file-input") : null;
    this.currentResetScope = null;
    this._lastFocused = null;

    this.archiveToggle = this.modal ? this.modal.querySelector("#setting-archive-enabled") : null;
    this.archiveFields = this.modal ? this.modal.querySelector("#archive-settings-fields") : null;
    this.saveStorageBtn = this.modal ? this.modal.querySelector("#save-storage-config-btn") : null;
    this.triggerEvictionBtn = this.modal ? this.modal.querySelector("#trigger-eviction-btn") : null;
    this.feedbackEl = this.modal ? this.modal.querySelector("#storage-action-feedback") : null;

    this.bindEvents();
  }

  bindEvents() {
    if (!this.modal) return;

    const closeBtn = this.modal.querySelector("#modal-close-btn");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => this.close());
    }

    // Focus view preferences apply immediately and are persisted by the app.
    const focusControls = [
      this.focusControlsToggle,
      this.acousticModeSelect,
      this.eventsCollapsedToggle,
    ].filter(Boolean);
    focusControls.forEach((control) => {
      control.addEventListener("change", () => this._emitFocusViewChange());
    });

    if (this.archiveToggle && this.archiveFields) {
      this.archiveToggle.addEventListener("change", (e) => {
        this.archiveFields.style.display = e.target.checked ? "grid" : "none";
      });
    }

    if (this.saveStorageBtn) {
      this.saveStorageBtn.addEventListener("click", () => this.saveStorageConfig());
    }

    if (this.triggerEvictionBtn) {
      this.triggerEvictionBtn.addEventListener("click", () => this.triggerEviction());
    }

    const applyProfileBtn = this.modal.querySelector("#apply-hardware-profile-btn");
    const profileSelect = this.modal.querySelector("#setting-hardware-profile");
    const runBenchBtn = this.modal.querySelector("#run-benchmark-btn");
    const benchFeedback = this.modal.querySelector("#benchmark-feedback");

    if (applyProfileBtn && profileSelect) {
      applyProfileBtn.addEventListener("click", async () => {
        const profile = profileSelect.value;
        try {
          const res = await fetch(resolveUrl("/api/system/profile"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile }),
          });
          if (benchFeedback) {
            benchFeedback.style.display = "block";
            if (res.ok) {
              benchFeedback.innerHTML = `<span style="color:#34d399;">✓ Preset <strong>${profile}</strong> applied successfully.</span>`;
            } else {
              benchFeedback.innerHTML = `<span style="color:#ef4444;">Failed to apply preset.</span>`;
            }
          }
        } catch (e) {
          if (benchFeedback) {
            benchFeedback.style.display = "block";
            benchFeedback.innerHTML = `<span style="color:#ef4444;">Network error: ${e.message}</span>`;
          }
        }
      });
    }

    if (runBenchBtn) {
      runBenchBtn.addEventListener("click", async () => {
        if (benchFeedback) {
          benchFeedback.style.display = "block";
          benchFeedback.textContent = "Running empirical hardware diagnostics...";
        }
        try {
          const res = await fetch(resolveUrl("/api/system/benchmark"));
          if (res.ok) {
            const data = await res.json();
            if (benchFeedback) {
              benchFeedback.innerHTML = `
                <div><strong>Transcode FPS:</strong> ${data.transcode_fps} fps | <strong>Edge ML Latency:</strong> ${data.inference_latency_ms} ms | <strong>IOPS:</strong> ${data.sqlite_iops}</div>
                <div style="margin-top:4px;"><strong>Recommended Max Cameras:</strong> ${data.recommended_max_cameras} continuous feeds</div>
                <ul style="margin:4px 0 0 16px;padding:0;">${(data.sizing_recommendations || []).map((r) => `<li>${r}</li>`).join("")}</ul>
              `;
            }
          }
        } catch (e) {
          if (benchFeedback) {
            benchFeedback.innerHTML = `<span style="color:#ef4444;">Benchmark error: ${e.message}</span>`;
          }
        }
      });
    }

    const dismissWarningBtn = document.querySelector("#dismiss-storage-warning");
    if (dismissWarningBtn) {
      dismissWarningBtn.addEventListener("click", () => {
        const banner = document.querySelector("#storage-warning-banner");
        if (banner) banner.style.display = "none";
      });
    }

    // Reset section multi-confirmation flow.
    const resetButtons = this.modal ? this.modal.querySelectorAll(".reset-btn") : [];
    resetButtons.forEach((btn) => {
      btn.addEventListener("click", () => this.startReset(btn.getAttribute("data-scope")));
    });
    const resetConfirmBtn = this.modal ? this.modal.querySelector("#reset-confirm-btn") : null;
    if (resetConfirmBtn) {
      resetConfirmBtn.addEventListener("click", () => this.confirmReset());
    }
    const resetCancelBtn = this.modal ? this.modal.querySelector("#reset-cancel-btn") : null;
    if (resetCancelBtn) {
      resetCancelBtn.addEventListener("click", () => this.cancelReset());
    }

    this.modal.addEventListener("click", (e) => {
      if (e.target === this.modal) {
        this.close();
      }
    });

    // Keyboard focus lifecycle: Escape closes, Tab is trapped inside the dialog.
    document.addEventListener("keydown", (event) => this._handleKeydown(event));

    if (this.aiSelect) {
      this.aiSelect.addEventListener("change", async (e) => {
        const newMode = e.target.value;
        try {
          const res = await fetch(resolveUrl("/api/settings/ai-mode"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: newMode }),
          });
          if (res.ok && this.onAiModeChanged) {
            this.onAiModeChanged(newMode);
          }
        } catch (err) {
          console.error("Failed to update AI mode:", err);
        }
      });
    }

    if (this.dropzone && this.fileInput) {
      this.dropzone.addEventListener("click", () => this.fileInput.click());

      this.dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        this.dropzone.classList.add("dragover");
      });

      this.dropzone.addEventListener("dragleave", () => {
        this.dropzone.classList.remove("dragover");
      });

      this.dropzone.addEventListener("drop", async (e) => {
        e.preventDefault();
        this.dropzone.classList.remove("dragover");
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
          await this.uploadFace(e.dataTransfer.files[0]);
        }
      });

      this.fileInput.addEventListener("change", async (e) => {
        if (e.target.files && e.target.files[0]) {
          await this.uploadFace(e.target.files[0]);
        }
      });
    }
  }

  _emitFocusViewChange() {
    if (!this.onFocusViewChanged) return;
    const acousticMode = this.acousticModeSelect
      ? this.acousticModeSelect.value
      : "below";
    this.onFocusViewChanged({
      controlsOverlay: this.focusControlsToggle
        ? this.focusControlsToggle.checked
        : true,
      acousticMode,
      acousticPinned: acousticMode === "overlay",
      eventsCollapsed: this.eventsCollapsedToggle
        ? this.eventsCollapsedToggle.checked
        : false,
    });
  }

  /** Mirror the persisted preferences into the Settings controls. */
  setFocusViewPreferences(prefs) {
    if (!prefs) return;
    if (this.focusControlsToggle) {
      this.focusControlsToggle.checked = prefs.controlsOverlay !== false;
    }
    if (this.acousticModeSelect) {
      const mode = prefs.acousticMode === "collapsed" ? "hidden" : prefs.acousticMode;
      this.acousticModeSelect.value =
        ["overlay", "below", "hidden"].indexOf(mode) !== -1 ? mode : "below";
    }
    if (this.eventsCollapsedToggle) {
      this.eventsCollapsedToggle.checked = Boolean(prefs.eventsCollapsed);
    }
  }

  _getFocusableElements() {
    if (!this.modal) return [];
    const selector = 'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
    return Array.from(this.modal.querySelectorAll(selector)).filter((el) => {
      if (el.disabled) return false;
      const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
      return el.getClientRects().length > 0;
    });
  }

  _focusFirst() {
    const focusables = this._getFocusableElements();
    if (focusables.length) {
      focusables[0].focus();
    } else if (this.modal) {
      this.modal.setAttribute("tabindex", "-1");
      this.modal.focus();
    }
  }

  _trapFocus(event) {
    const focusables = this._getFocusableElements();
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (!this.modal.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
  }

  _handleKeydown(event) {
    if (!this.modal || !this.modal.classList.contains("open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
    } else if (event.key === "Tab") {
      this._trapFocus(event);
    }
  }

  async open() {
    if (!this.modal) return;
    this._lastFocused = document.activeElement;
    this.modal.classList.add("open");
    this._focusFirst();
    await this.loadSettings();
    await this.loadFaces();
    await this.loadEntities();
    await this.loadIntegrations();
    await this.loadCameraNotifications();
    await this.loadHomeAssistant();
    await this.loadStorage();
  }

  close() {
    if (!this.modal) return;
    this.modal.classList.remove("open");
    if (this.onClose) {
      this.onClose();
    }
    if (this._lastFocused && this._lastFocused.isConnected) {
      this._lastFocused.focus();
    }
  }

  async startReset(scope) {
    this.currentResetScope = scope;
    const summaryEl = this.modal ? this.modal.querySelector("#reset-impact-summary") : null;
    try {
      const res = await fetch(resolveUrl("/api/system/reset/preview"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      });
      const data = await res.json().catch(() => ({}));
      if (summaryEl) {
        summaryEl.textContent =
          data.summary || data.impact_summary || data.impact || JSON.stringify(data);
      }
    } catch (err) {
      if (summaryEl) summaryEl.textContent = "Preview failed.";
    }
    const wrap = this.modal ? this.modal.querySelector("#reset-confirm-wrap") : null;
    if (wrap) wrap.style.display = "block";
    const input = this.modal ? this.modal.querySelector("#reset-confirm-input") : null;
    if (input) input.value = "";
  }

  cancelReset() {
    const wrap = this.modal ? this.modal.querySelector("#reset-confirm-wrap") : null;
    if (wrap) wrap.style.display = "none";
    this.currentResetScope = null;
  }

  async confirmReset() {
    const input = this.modal ? this.modal.querySelector("#reset-confirm-input") : null;
    if (!input || input.value !== "RESET") {
      window.alert('Type "RESET" to confirm.');
      return;
    }
    // Second confirmation dialog.
    if (!window.confirm("This action cannot be undone. Proceed?")) {
      return;
    }
    const scope = this.currentResetScope;
    try {
      const res = await fetch(resolveUrl("/api/system/reset/execute"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: "RESET", scope }),
      });
      if (res.ok) {
        await this.open();
      }
    } catch (err) {
      console.error("Reset failed:", err);
    }
    const wrap = this.modal ? this.modal.querySelector("#reset-confirm-wrap") : null;
    if (wrap) wrap.style.display = "none";
  }

  async loadEntities() {
    if (!this.entitiesContainer) return;
    try {
      const res = await fetch(resolveUrl("/api/entities"));
      if (!res.ok) return;
      const data = await res.json();
      this.entitiesContainer.innerHTML = "";

      const entities = data.entities || [];
      if (entities.length === 0) {
        this.entitiesContainer.innerHTML =
          '<span class="text-muted" style="font-size:0.75rem;">No catalog entities enrolled yet</span>';
        return;
      }

      const defaultEmoji = {
        resident: "👤",
        pet: "🐶",
        vehicle: "🚗",
        thing: "📦",
      };

      entities.forEach((ent) => {
        const row = document.createElement("div");
        row.className = "entity-row";

        const renderView = () => {
          const icon = ent.emoji || defaultEmoji[ent.entity_type] || "👤";
          row.innerHTML = `
            <div class="entity-meta">
              <span>${icon}</span>
              <div>
                <div style="font-size:0.8125rem;font-weight:600;">${ent.name}</div>
                <div class="text-muted" style="font-size:0.6875rem;">${ent.entity_type} ${ent.species ? `(${ent.species})` : ""}</div>
              </div>
            </div>
            <div class="entity-actions">
              <button class="btn entity-edit" style="padding:2px 6px;font-size:0.6875rem;" title="Edit entity">Edit</button>
              <button class="btn entity-delete" style="padding:2px 6px;font-size:0.6875rem;" title="Delete entity">✕</button>
            </div>
          `;

          row.querySelector(".entity-edit").addEventListener("click", renderEdit);
          row.querySelector(".entity-delete").addEventListener("click", async () => {
            await fetch(resolveUrl(`/api/entities/${encodeURIComponent(ent.id)}`), {
              method: "DELETE",
            });
            await this.loadEntities();
          });
        };

        const renderEdit = () => {
          const currentEmoji = ent.emoji || defaultEmoji[ent.entity_type] || "👤";
          row.innerHTML = `
            <div class="entity-edit-form" style="display:flex;flex-direction:column;gap:6px;width:100%;">
              <div style="display:flex;gap:6px;align-items:center;">
                <input class="input-control entity-emoji-input" type="text" value="${currentEmoji}" title="Emoji" style="width:52px;text-align:center;" />
                <input class="input-control entity-name-input" type="text" value="${ent.name}" title="Name" style="flex:1;" />
              </div>
              <div style="display:flex;gap:6px;">
                <button class="btn btn-primary entity-save" style="padding:2px 8px;font-size:0.6875rem;">Save</button>
                <button class="btn entity-cancel" style="padding:2px 8px;font-size:0.6875rem;">Cancel</button>
              </div>
            </div>
          `;

          row.querySelector(".entity-cancel").addEventListener("click", renderView);
          row.querySelector(".entity-save").addEventListener("click", async () => {
            const name = row.querySelector(".entity-name-input").value.trim() || ent.name;
            const emoji = row.querySelector(".entity-emoji-input").value.trim() || currentEmoji;
            try {
              const saveRes = await fetch(resolveUrl("/api/entities"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  id: ent.id,
                  name: name,
                  emoji: emoji,
                  entity_type: ent.entity_type,
                  species: ent.species,
                  attributes: ent.attributes || {},
                  enrolled_photos: ent.enrolled_photos || [],
                  enrolled_faces: ent.enrolled_faces || [],
                }),
              });
              if (saveRes.ok) {
                await this.loadEntities();
              }
            } catch (err) {
              console.warn("Failed to save entity:", err);
            }
          });
        };

        renderView();
        this.entitiesContainer.appendChild(row);
      });
    } catch (err) {
      console.warn("Failed to load entities:", err);
    }
  }

  async loadSettings() {
    try {
      const res = await fetch(resolveUrl("/api/settings/ai-mode"));
      if (res.ok) {
        const data = await res.json();
        if (this.aiSelect && data.active_mode) {
          this.aiSelect.value = data.active_mode;
        }
      }
    } catch (err) {
      console.warn("Could not load AI mode setting:", err);
    }
  }

  async loadFaces() {
    if (!this.facesContainer) return;
    try {
      const res = await fetch(resolveUrl("/api/settings/faces"));
      if (!res.ok) return;
      const data = await res.json();
      this.facesContainer.innerHTML = "";

      const faces = data.faces || [];
      if (faces.length === 0) {
        this.facesContainer.innerHTML = '<span class="text-muted" style="font-size:0.75rem;">No faces enrolled yet</span>';
        return;
      }

      faces.forEach((f) => {
        const card = document.createElement("div");
        card.className = "face-card";
        const faceImgUrl = resolveUrl(`/media/faces/${encodeURIComponent(f.resident_id)}/${encodeURIComponent(f.filename)}`);
        const placeholderUrl = resolveUrl("/static/placeholder.jpg");
        card.innerHTML = `
          <img class="face-img" src="${faceImgUrl}" alt="${f.filename}" onerror="this.src='${placeholderUrl}'" />
          <button class="face-card-del" title="Delete face">✕</button>
        `;

        const delBtn = card.querySelector(".face-card-del");
        delBtn.addEventListener("click", async () => {
          await fetch(resolveUrl(`/api/settings/faces/${encodeURIComponent(f.resident_id)}/${encodeURIComponent(f.filename)}`), {
            method: "DELETE",
          });
          await this.loadFaces();
        });

        this.facesContainer.appendChild(card);
      });
    } catch (err) {
      console.warn("Failed to load enrolled faces:", err);
    }
  }

  async uploadFace(file) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("resident_id", "default");

    try {
      const res = await fetch(resolveUrl("/api/settings/faces/upload"), {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        await this.loadFaces();
      }
    } catch (err) {
      console.error("Face upload error:", err);
    }
  }

  async loadIntegrations() {
    if (!this.integrationsContainer) return;
    try {
      const res = await fetch(resolveUrl("/api/auth/status"));
      if (!res.ok) return;
      const statusData = await res.json();

      let provRes = await fetch(resolveUrl("/api/providers"));
      let providers = [];
      if (provRes.ok) {
        const provData = await provRes.json();
        providers = provData.providers || [];
      } else {
        provRes = await fetch(resolveUrl("/api/auth/providers"));
        providers = provRes.ok ? (await provRes.json()).providers || [] : [];
      }

      this.integrationsContainer.innerHTML = "";

      const allProviders = [
        ...providers,
        { id: "go2rtc", name: "go2rtc Streaming Server", display_name: "go2rtc Streaming Server" },
      ];

      allProviders.forEach((prov) => {
        const pStatus = statusData[prov.id] || {};
        const isConn =
          pStatus.status === "connected" ||
          pStatus.status === "pre_authenticated" ||
          pStatus.reachable;
        const statusLabel =
          pStatus.status === "pre_authenticated"
            ? "Connected (Pre-authenticated)"
            : isConn
            ? "Connected"
            : "Disconnected";
        const badgeColor = isConn ? "#10b981" : "#64748b";

        const row = document.createElement("div");
        row.className = "integration-row";
        row.style.cssText =
          "display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--bg-subtle, #334155);border-radius:6px;";

        row.innerHTML = `
          <div>
            <div style="font-size:0.8125rem;font-weight:600;">${prov.display_name || prov.name}</div>
            <div style="font-size:0.6875rem;color:var(--text-muted, #94a3b8);">${prov.id} sidecar bridge</div>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="badge" style="font-size:0.6875rem;padding:2px 6px;border-radius:4px;background:${badgeColor};color:#fff;">${statusLabel}</span>
            <button class="btn btn-test-conn" style="padding:2px 8px;font-size:0.75rem;" type="button">Test</button>
          </div>
        `;

        const testBtn = row.querySelector(".btn-test-conn");
        testBtn.addEventListener("click", async () => {
          testBtn.disabled = true;
          testBtn.textContent = "...";
          try {
            const tRes = await fetch(resolveUrl("/api/auth/test-connection"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ provider: prov.id }),
            });
            const tData = await tRes.json();
            testBtn.textContent = tData.reachable ? "Reachable" : "Failed";
            testBtn.style.color = tData.reachable ? "#10b981" : "#ef4444";
          } catch (e) {
            testBtn.textContent = "Error";
          } finally {
            setTimeout(() => {
              testBtn.disabled = false;
              testBtn.textContent = "Test";
              testBtn.style.color = "inherit";
            }, 2000);
          }
        });

        this.integrationsContainer.appendChild(row);
      });
    } catch (err) {
      console.warn("Failed to load integrations status:", err);
    }
  }

  async loadStorage() {
    try {
      const [statusRes, configRes] = await Promise.all([
        fetch(resolveUrl("/api/storage/status")),
        fetch(resolveUrl("/api/storage/config")),
      ]);

      if (statusRes.ok) {
        const s = await statusRes.json();
        const localUsedGb = (s.media_total_bytes / (1024 ** 3)).toFixed(2);
        const localMaxGb = (s.max_storage_bytes / (1024 ** 3)).toFixed(1);
        const localStats = this.modal.querySelector("#local-storage-stats");
        if (localStats) localStats.textContent = `${localUsedGb} / ${localMaxGb} GB (${s.media_quota_used_percent}%)`;

        const localFill = this.modal.querySelector("#local-progress-fill");
        if (localFill) {
          localFill.style.width = `${Math.min(100, s.media_quota_used_percent)}%`;
          localFill.classList.toggle("warning", s.is_warning);
        }

        const localQuotaDesc = this.modal.querySelector("#local-quota-desc");
        if (localQuotaDesc) localQuotaDesc.textContent = `Quota: ${s.max_storage_gb} GB | Cap: ${s.max_storage_percent}%`;

        const localRunwayDesc = this.modal.querySelector("#local-runway-desc");
        if (localRunwayDesc) localRunwayDesc.textContent = s.retention_runway_hours !== null ? `Runway: ~${s.retention_runway_hours}h` : "Runway: N/A";

        // Archive tier
        const archStats = this.modal.querySelector("#archive-storage-stats");
        const archFill = this.modal.querySelector("#archive-progress-fill");
        const archQuotaDesc = this.modal.querySelector("#archive-quota-desc");
        const archBadge = this.modal.querySelector("#archive-status-badge");

        if (s.archive_enabled && s.archive_path) {
          const archUsedGb = (s.archive_media_total_bytes / (1024 ** 3)).toFixed(2);
          const archMaxGb = s.archive_max_storage_bytes > 0 ? (s.archive_max_storage_bytes / (1024 ** 3)).toFixed(1) : "Unbounded";
          if (archStats) archStats.textContent = `${archUsedGb} / ${archMaxGb} GB (${s.archive_quota_used_percent}%)`;
          if (archFill) {
            archFill.style.width = `${Math.min(100, s.archive_quota_used_percent)}%`;
            archFill.classList.toggle("warning", s.archive_is_warning);
          }
          if (archQuotaDesc) archQuotaDesc.textContent = `Mount: ${s.archive_path}`;
          if (archBadge) {
            archBadge.textContent = s.archive_available ? "Mounted (Writable)" : "Unavailable / Read-Only";
            archBadge.style.color = s.archive_available ? "#10b981" : "#ef4444";
          }
        } else {
          if (archStats) archStats.textContent = "Disabled";
          if (archFill) archFill.style.width = "0%";
          if (archQuotaDesc) archQuotaDesc.textContent = "Mount: Inactive";
          if (archBadge) {
            archBadge.textContent = "Disabled";
            archBadge.style.color = "var(--text-muted)";
          }
        }

        // Global warning banner
        const banner = document.querySelector("#storage-warning-banner");
        if (banner) {
          if (s.is_warning || s.archive_is_warning) {
            banner.style.display = "flex";
            const text = document.querySelector("#storage-warning-text");
            if (text) {
              text.textContent = s.archive_is_warning
                ? "Archive storage quota exceeded (>90%). Purging oldest archived media."
                : "Local storage quota exceeded (>90%). Evicting oldest media.";
            }
          } else {
            banner.style.display = "none";
          }
        }
      }

      if (configRes.ok) {
        const c = await configRes.json();
        const maxGbInput = this.modal.querySelector("#setting-max-storage-gb");
        if (maxGbInput && c.max_storage_gb !== undefined) maxGbInput.value = c.max_storage_gb;

        const maxPctInput = this.modal.querySelector("#setting-max-storage-pct");
        if (maxPctInput && c.max_storage_percent !== undefined) maxPctInput.value = c.max_storage_percent;

        const retModeInput = this.modal.querySelector("#setting-retention-mode");
        if (retModeInput && c.retention_mode) retModeInput.value = c.retention_mode;

        const bmToggle = this.modal.querySelector("#setting-preserve-bookmarked");
        if (bmToggle && c.preserve_bookmarked !== undefined) bmToggle.checked = c.preserve_bookmarked;

        if (this.archiveToggle && c.archive_enabled !== undefined) {
          this.archiveToggle.checked = c.archive_enabled;
          if (this.archiveFields) this.archiveFields.style.display = c.archive_enabled ? "grid" : "none";
        }

        const archPathInput = this.modal.querySelector("#setting-archive-path");
        if (archPathInput && c.archive_path) archPathInput.value = c.archive_path;

        const archMaxGbInput = this.modal.querySelector("#setting-archive-max-gb");
        if (archMaxGbInput && c.archive_max_storage_gb !== null && c.archive_max_storage_gb !== undefined) {
          archMaxGbInput.value = c.archive_max_storage_gb;
        }

        const archMaxPctInput = this.modal.querySelector("#setting-archive-max-pct");
        if (archMaxPctInput && c.archive_max_storage_percent !== undefined) {
          archMaxPctInput.value = c.archive_max_storage_percent;
        }
      }
    } catch (err) {
      console.warn("Failed to load storage telemetry:", err);
    }
  }

  async saveStorageConfig() {
    if (!this.saveStorageBtn) return;
    this.saveStorageBtn.disabled = true;
    this.saveStorageBtn.textContent = "Saving...";
    try {
      const maxGb = parseFloat(this.modal.querySelector("#setting-max-storage-gb")?.value || "50");
      const maxPct = parseFloat(this.modal.querySelector("#setting-max-storage-pct")?.value || "80");
      const retentionMode = this.modal.querySelector("#setting-retention-mode")?.value || "fifo_oldest_first";
      const preserveBm = Boolean(this.modal.querySelector("#setting-preserve-bookmarked")?.checked);
      const archiveEnabled = Boolean(this.archiveToggle?.checked);
      const archivePath = this.modal.querySelector("#setting-archive-path")?.value?.trim() || null;
      const rawArchGb = this.modal.querySelector("#setting-archive-max-gb")?.value;
      const archiveMaxGb = rawArchGb ? parseFloat(rawArchGb) : null;
      const archiveMaxPct = parseFloat(this.modal.querySelector("#setting-archive-max-pct")?.value || "90");

      const res = await fetch(resolveUrl("/api/storage/config"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          max_storage_gb: maxGb,
          max_storage_percent: maxPct,
          retention_mode: retentionMode,
          preserve_bookmarked: preserveBm,
          archive_enabled: archiveEnabled,
          archive_path: archivePath,
          archive_max_storage_gb: archiveMaxGb,
          archive_max_storage_percent: archiveMaxPct,
        }),
      });

      if (this.feedbackEl) {
        this.feedbackEl.style.display = "block";
        if (res.ok) {
          this.feedbackEl.textContent = "✓ Storage settings saved successfully.";
          this.feedbackEl.style.color = "#10b981";
          await this.loadStorage();
        } else {
          this.feedbackEl.textContent = "✕ Failed to save storage settings.";
          this.feedbackEl.style.color = "#ef4444";
        }
      }
    } catch (err) {
      if (this.feedbackEl) {
        this.feedbackEl.style.display = "block";
        this.feedbackEl.textContent = "Error saving settings.";
        this.feedbackEl.style.color = "#ef4444";
      }
    } finally {
      this.saveStorageBtn.disabled = false;
      this.saveStorageBtn.textContent = "Save Storage Settings";
    }
  }

  async triggerEviction() {
    if (!this.triggerEvictionBtn) return;
    this.triggerEvictionBtn.disabled = true;
    this.triggerEvictionBtn.textContent = "Running...";
    try {
      const res = await fetch(resolveUrl("/api/storage/clean"), { method: "POST" });
      const data = await res.json();
      if (this.feedbackEl) {
        this.feedbackEl.style.display = "block";
        this.feedbackEl.style.color = "#10b981";
        this.feedbackEl.textContent = `✓ Eviction complete: ${data.files_migrated || 0} migrated, ${data.files_deleted || 0} pruned.`;
      }
      await this.loadStorage();
    } catch (err) {
      if (this.feedbackEl) {
        this.feedbackEl.style.display = "block";
        this.feedbackEl.style.color = "#ef4444";
        this.feedbackEl.textContent = "Failed to run eviction.";
      }
    } finally {
      this.triggerEvictionBtn.disabled = false;
      this.triggerEvictionBtn.textContent = "⚡ Run Eviction / Sync Now";
    }
  }

  async loadCameraNotifications() {
    if (!this.cameraNotificationsContainer) return;
    this.cameraNotificationsContainer.innerHTML = '<div style="font-size:0.8rem;color:var(--text-muted);">Loading cameras...</div>';
    try {
      const res = await fetch("/api/cameras");
      if (!res.ok) throw new Error("Failed to load cameras");
      const data = await res.json();
      const cameras = data.cameras || [];
      if (cameras.length === 0) {
        this.cameraNotificationsContainer.innerHTML = '<div style="font-size:0.8rem;color:var(--text-muted);">No cameras configured.</div>';
        return;
      }

      this.cameraNotificationsContainer.innerHTML = "";
      const eventTypes = ["motion", "sound", "person", "ding"];
      const threatLevels = [
        { value: "", label: "Global Default" },
        { value: "INFO", label: "INFO" },
        { value: "LOW", label: "LOW" },
        { value: "MEDIUM", label: "MEDIUM" },
        { value: "HIGH", label: "HIGH" },
        { value: "CRITICAL", label: "CRITICAL" },
      ];

      for (const cam of cameras) {
        const card = document.createElement("div");
        card.className = "camera-notification-card";
        card.style.cssText = "padding:10px;border:1px solid var(--border-subtle);border-radius:8px;background:var(--bg-subtle);display:flex;flex-direction:column;gap:8px;";

        const enabled = cam.notifications_enabled !== false;
        const minThreat = cam.notification_min_threat || "";
        const selectedTypes = new Set(cam.notification_event_types || eventTypes);
        const pass1 = cam.notify_pass1 !== false;
        const pass2 = cam.notify_pass2 !== false;
        const continuousRecording = cam.continuous_recording !== false;

        card.innerHTML = `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
            <div style="display:flex;align-items:center;gap:6px;font-weight:600;font-size:0.85rem;">
              <span>${cam.icon || "📷"}</span>
              <span>${cam.name || cam.id}</span>
              <span class="badge" style="font-size:0.7rem;padding:2px 6px;border-radius:4px;background:var(--bg-surface);">${cam.provider_badge || "generic"}</span>
            </div>
            <label style="display:flex;align-items:center;gap:4px;font-size:0.8rem;cursor:pointer;">
              <input type="checkbox" class="cam-notif-enabled" ${enabled ? "checked" : ""}>
              <span>Alerts</span>
            </label>
          </div>
          <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;font-size:0.78rem;">
            <label style="display:flex;align-items:center;gap:4px;">
              <span>Min Threat:</span>
              <select class="cam-notif-threat" style="padding:2px 6px;border-radius:4px;border:1px solid var(--border-subtle);background:var(--bg-surface);color:inherit;">
                ${threatLevels.map((t) => `<option value="${t.value}" ${minThreat === t.value ? "selected" : ""}>${t.label}</option>`).join("")}
              </select>
            </label>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              ${eventTypes.map((t) => `<label style="display:flex;align-items:center;gap:3px;cursor:pointer;"><input type="checkbox" class="cam-notif-type" value="${t}" ${selectedTypes.has(t) ? "checked" : ""}><span>${t}</span></label>`).join("")}
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;font-size:0.78rem;">
            <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
              <input type="checkbox" class="cam-notif-pass1" ${pass1 ? "checked" : ""}>
              <span>Pass 1 (Instant)</span>
            </label>
            <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
              <input type="checkbox" class="cam-notif-pass2" ${pass2 ? "checked" : ""}>
              <span>Pass 2 (Enriched Clip)</span>
            </label>
            <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
              <input type="checkbox" class="cam-continuous-recording" ${continuousRecording ? "checked" : ""}>
              <span>Record continuously</span>
            </label>
          </div>
          <div class="camera-ha-section" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border-subtle);">
            <div style="font-weight:600;font-size:0.8rem;margin-bottom:4px;">Home Assistant</div>
            <label style="display:flex;align-items:center;gap:4px;font-size:0.78rem;">
              <input type="checkbox" class="cam-ha-enabled" ${cam.ha_enabled !== false ? "checked" : ""}>
              <span>Enabled for HA</span>
            </label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px;">
              <input class="input-control cam-ha-name" type="text" placeholder="Entity name" value="${cam.ha_entity_name || ""}">
              <input class="input-control cam-ha-unique" type="text" placeholder="Unique id" value="${cam.ha_unique_id || ""}">
              <input class="input-control cam-ha-class" type="text" placeholder="Device class" value="${cam.ha_device_class || ""}">
              <input class="input-control cam-ha-icon" type="text" placeholder="Icon" value="${cam.ha_icon || ""}">
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;font-size:0.75rem;">
              ${["motion","person","sound","ding"].map((t) => `<label style="display:flex;align-items:center;gap:3px;"><input type="checkbox" class="cam-ha-type" value="${t}" ${(cam.ha_event_types || []).includes(t) ? "checked" : ""}><span>${t}</span></label>`).join("")}
            </div>
            <div class="cam-ha-status" style="margin-top:6px;font-size:0.75rem;color:var(--text-muted);">Loading sync status...</div>
          </div>
        `;
 
        const saveChanges = async () => {
          const payload = {
            notifications_enabled: card.querySelector(".cam-notif-enabled").checked,
            notification_min_threat: card.querySelector(".cam-notif-threat").value || null,
            notification_event_types: Array.from(card.querySelectorAll(".cam-notif-type:checked")).map((el) => el.value),
            notify_pass1: card.querySelector(".cam-notif-pass1").checked,
            notify_pass2: card.querySelector(".cam-notif-pass2").checked,
            continuous_recording: card.querySelector(".cam-continuous-recording").checked,
          };
          try {
            const patchRes = await fetch(`/api/cameras/${encodeURIComponent(cam.id)}/settings`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            if (!patchRes.ok) throw new Error("Save failed");
            if (this.feedbackEl) {
              this.feedbackEl.style.color = "#10b981";
              this.feedbackEl.textContent = `Saved notification settings for ${cam.name || cam.id}`;
            }
          } catch (err) {
            if (this.feedbackEl) {
              this.feedbackEl.style.color = "#ef4444";
              this.feedbackEl.textContent = `Failed to save settings for ${cam.name || cam.id}`;
            }
          }
        };

        const saveHA = async () => {
          const payload = {
            ha_enabled: card.querySelector(".cam-ha-enabled").checked,
            ha_entity_name: card.querySelector(".cam-ha-name").value.trim() || null,
            ha_unique_id: card.querySelector(".cam-ha-unique").value.trim() || null,
            ha_device_class: card.querySelector(".cam-ha-class").value.trim() || null,
            ha_icon: card.querySelector(".cam-ha-icon").value.trim() || null,
            ha_event_types: Array.from(card.querySelectorAll(".cam-ha-type:checked")).map((el) => el.value),
          };
          try {
            const patchRes = await fetch(`/api/cameras/${encodeURIComponent(cam.id)}/settings`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            if (patchRes.ok) {
              if (this.feedbackEl) {
                this.feedbackEl.style.color = "#10b981";
                this.feedbackEl.textContent = `Saved HA settings for ${cam.name || cam.id}`;
              }
            }
          } catch (err) {
            if (this.feedbackEl) {
              this.feedbackEl.style.color = "#ef4444";
              this.feedbackEl.textContent = `Failed to save HA settings for ${cam.name || cam.id}`;
            }
          }
        };

        card.querySelectorAll(".camera-ha-section input").forEach((el) => {
          el.addEventListener("change", saveHA);
        });

        // Load sync status + last published for this camera.
        (async () => {
          const statusEl = card.querySelector(".cam-ha-status");
          try {
            const res = await fetch(resolveUrl(`/api/home-assistant/cameras/${encodeURIComponent(cam.id)}`));
            if (res.ok) {
              const data = await res.json();
              if (statusEl) {
                statusEl.textContent = `Last published: ${data.last_published || "never"}`;
              }
            } else if (statusEl) {
              statusEl.textContent = "Sync status unavailable.";
            }
          } catch (e) {
            if (statusEl) statusEl.textContent = "Sync status unavailable.";
          }
        })();

        card.querySelectorAll("input, select").forEach((el) => {
          el.addEventListener("change", saveChanges);
        });

        this.cameraNotificationsContainer.appendChild(card);
      }
    } catch (err) {
      this.cameraNotificationsContainer.innerHTML = '<div style="font-size:0.8rem;color:#ef4444;">Failed to load camera notification settings.</div>';
    }
  }

  async loadHomeAssistant() {
    if (!this.homeAssistantContainer) return;
    this.homeAssistantContainer.innerHTML = '<div style="font-size:0.8rem;color:var(--text-muted);">Loading Home Assistant settings...</div>';
    try {
      const [cfgRes, statusRes] = await Promise.all([
        fetch(resolveUrl("/api/home-assistant/config")),
        fetch(resolveUrl("/api/home-assistant/status")),
      ]);
      const cfg = cfgRes.ok ? await cfgRes.json() : {};
      const status = statusRes.ok ? await statusRes.json() : {};
      const st = status.status || status || {};

      const ha = cfg.home_assistant && typeof cfg.home_assistant === "object" ? cfg.home_assistant : cfg;
      const MQTT_ONLY_MSG = "MQTT-only mode — no HA credentials configured";
      const mode = st.mode || status.mode || cfg.mode || ha.mode || "";
      const mqttOnly = mode === "mqtt-only";
      const rawLastError = String(st.last_error || "");
      const isHttp401 = /HTTP\s*401/i.test(rawLastError);
      // A bare HTTP 401 in mqtt-only mode is misleading (no REST call is made
      // there). Suppress only that text; genuine sync/MQTT errors must stay
      // visible (AC8). In REST mode, keep the friendlier credential hint.
      let lastError = rawLastError || null;
      if (isHttp401) {
        lastError = mqttOnly
          ? null
          : rawLastError.replace(/HTTP\s*401/i, "Authentication failed (check HA credentials)");
      }

      this.homeAssistantContainer.innerHTML = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <label style="display:flex;align-items:center;gap:6px;font-size:0.8rem;grid-column:1 / -1;">
            <input type="checkbox" id="ha-enabled" ${ha.enabled !== false ? "checked" : ""}>
            <span>Enable Home Assistant sync</span>
          </label>
          <div>
            <label class="setting-desc" style="font-weight:600;" for="ha-base-url">HA Base URL</label>
            <input class="input-control" type="text" id="ha-base-url" value="${ha.base_url || ""}" placeholder="http://homeassistant:8123">
          </div>
          <div>
            <label class="setting-desc" style="font-weight:600;" for="ha-ingress-url">Ingress URL</label>
            <input class="input-control" type="text" id="ha-ingress-url" value="${ha.ingress_url || ""}" placeholder="https://ha.example/api/hassio_ingress/...">
          </div>
          <div>
            <label class="setting-desc" style="font-weight:600;" for="ha-mqtt-host">MQTT Host</label>
            <input class="input-control" type="text" id="ha-mqtt-host" value="${ha.mqtt_host || ""}" placeholder="127.0.0.1">
          </div>
          <div>
            <label class="setting-desc" style="font-weight:600;" for="ha-mqtt-port">MQTT Port</label>
            <input class="input-control" type="number" id="ha-mqtt-port" value="${ha.mqtt_port || ""}" placeholder="1883">
          </div>
          <div>
            <label class="setting-desc" style="font-weight:600;" for="ha-mqtt-username">MQTT Username</label>
            <input class="input-control" type="text" id="ha-mqtt-username" value="${ha.mqtt_username || ""}">
          </div>
        </div>
        <details id="ha-advanced" style="margin-top:8px;">
          <summary style="cursor:pointer;font-size:0.8rem;font-weight:600;">Advanced (access token)</summary>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;">
            <div>
              <label class="setting-desc" style="font-weight:600;" for="ha-access-token">Access Token</label>
              <input class="input-control" type="password" id="ha-access-token" value="" placeholder="${ha.access_token || "masked"}">
            </div>
            <div>
              <label class="setting-desc" style="font-weight:600;" for="ha-mqtt-password">MQTT Password</label>
              <input class="input-control" type="password" id="ha-mqtt-password" value="" placeholder="${ha.mqtt_password || "masked"}">
            </div>
          </div>
        </details>
        <div style="display:flex;gap:8px;margin-top:10px;align-items:center;">
          <button class="btn btn-primary" id="ha-save-btn" type="button">Save</button>
          <button class="btn" id="ha-test-btn" type="button">Test Connection</button>
          <button class="btn" id="ha-sync-btn" type="button">Sync Now</button>
          <span id="ha-test-result" style="font-size:0.75rem;"></span>
        </div>
        <div style="margin-top:10px;font-size:0.8rem;">
          <div><strong>Mode:</strong> ${mqttOnly ? "MQTT-only" : "REST"}</div>
          <div><strong>Last sync:</strong> ${st.last_sync || "never"}</div>
          <div><strong>Last error:</strong> ${lastError || "none"}</div>
          <div class="text-muted" style="font-size:0.7rem;">${mqttOnly ? MQTT_ONLY_MSG : "Connection settings under Advanced are optional for MQTT discovery."}</div>
        </div>
        <div style="margin-top:10px;">
          <div style="font-weight:600;font-size:0.8rem;margin-bottom:4px;">Published entities</div>
          <div id="ha-published-entities" style="font-size:0.75rem;color:var(--text-muted);"></div>
        </div>
      `;

      const publishedEl = this.homeAssistantContainer.querySelector("#ha-published-entities");
      const topics = st.published_topics || {};
      const topicKeys = Object.keys(topics);
      if (publishedEl) {
        if (topicKeys.length === 0) {
          publishedEl.textContent = "No entities published yet.";
        } else {
          publishedEl.innerHTML = topicKeys.map((k) => `<div>${k}: ${(topics[k] || []).length} topics</div>`).join("");
        }
      }

      const saveBtn = this.homeAssistantContainer.querySelector("#ha-save-btn");
      if (saveBtn) {
        saveBtn.addEventListener("click", async () => {
          const payload = {
            enabled: this.homeAssistantContainer.querySelector("#ha-enabled").checked,
            base_url: this.homeAssistantContainer.querySelector("#ha-base-url").value.trim() || null,
            ingress_url: this.homeAssistantContainer.querySelector("#ha-ingress-url").value.trim() || null,
            mqtt_host: this.homeAssistantContainer.querySelector("#ha-mqtt-host").value.trim() || null,
            mqtt_port: parseInt(this.homeAssistantContainer.querySelector("#ha-mqtt-port").value, 10) || null,
            mqtt_username: this.homeAssistantContainer.querySelector("#ha-mqtt-username").value.trim() || null,
          };
          const tokenVal = this.homeAssistantContainer.querySelector("#ha-access-token").value.trim();
          if (tokenVal) payload.access_token = tokenVal;
          const mqttPw = this.homeAssistantContainer.querySelector("#ha-mqtt-password").value.trim();
          if (mqttPw) payload.mqtt_password = mqttPw;
          try {
            const res = await fetch(resolveUrl("/api/home-assistant/config"), {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            const resultEl = this.homeAssistantContainer.querySelector("#ha-test-result");
            if (resultEl) {
              resultEl.textContent = res.ok ? "Saved" : "Save failed";
              resultEl.style.color = res.ok ? "#10b981" : "#ef4444";
            }
            if (res.ok) await this.loadHomeAssistant();
          } catch (e) {
            console.error("HA save failed:", e);
          }
        });
      }

      const testBtn = this.homeAssistantContainer.querySelector("#ha-test-btn");
      if (testBtn) {
        testBtn.addEventListener("click", async () => {
          const resultEl = this.homeAssistantContainer.querySelector("#ha-test-result");
          if (resultEl) resultEl.textContent = "Testing...";
          try {
            const res = await fetch(resolveUrl("/api/home-assistant/test"), { method: "POST" });
            const data = await res.json();
            if (resultEl) {
              const effectiveMode = data.mode || mode;
              if (effectiveMode === "mqtt-only") {
                resultEl.textContent = data.message || MQTT_ONLY_MSG;
                resultEl.style.color = "#10b981";
              } else {
                resultEl.textContent = data.ok ? `OK (${data.status || "connected"})` : `Failed: ${data.error || ""}`;
                resultEl.style.color = data.ok ? "#10b981" : "#ef4444";
              }
            }
          } catch (e) {
            // A thrown fetch/JSON error is always a failure - never show the
            // green MQTT-only placeholder success message here.
            if (resultEl) {
              resultEl.textContent = "Error";
              resultEl.style.color = "#ef4444";
            }
          }
        });
      }

      const syncBtn = this.homeAssistantContainer.querySelector("#ha-sync-btn");
      if (syncBtn) {
        syncBtn.addEventListener("click", async () => {
          try {
            await fetch(resolveUrl("/api/home-assistant/sync"), { method: "POST" });
            await this.loadHomeAssistant();
          } catch (e) {
            console.error("HA sync failed:", e);
          }
        });
      }
    } catch (err) {
      this.homeAssistantContainer.innerHTML = '<div style="font-size:0.8rem;color:#ef4444;">Failed to load Home Assistant settings.</div>';
      console.warn("Failed to load HA settings:", err);
    }
  }
}

