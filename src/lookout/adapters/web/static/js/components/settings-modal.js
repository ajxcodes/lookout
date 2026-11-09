/**
 * Admin settings modal and face gallery management component.
 */
import { resolveUrl } from "../utils/url.js";

export class SettingsModal {
  constructor({ modalBackdrop, onClose, onAiModeChanged }) {
    this.modal = modalBackdrop;
    this.onClose = onClose;
    this.onAiModeChanged = onAiModeChanged;

    this.aiSelect = this.modal ? this.modal.querySelector("#setting-ai-mode") : null;
    this.dropzone = this.modal ? this.modal.querySelector("#face-dropzone") : null;
    this.facesContainer = this.modal ? this.modal.querySelector("#faces-grid") : null;
    this.entitiesContainer = this.modal ? this.modal.querySelector("#entity-catalog-list") : null;
    this.integrationsContainer = this.modal ? this.modal.querySelector("#sidecar-integrations-list") : null;
    this.fileInput = this.modal ? this.modal.querySelector("#face-file-input") : null;

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

    this.modal.addEventListener("click", (e) => {
      if (e.target === this.modal) {
        this.close();
      }
    });

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

  async open() {
    if (!this.modal) return;
    this.modal.classList.add("open");
    await this.loadSettings();
    await this.loadFaces();
    await this.loadEntities();
    await this.loadIntegrations();
    await this.loadStorage();
  }

  close() {
    if (!this.modal) return;
    this.modal.classList.remove("open");
    if (this.onClose) {
      this.onClose();
    }
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

      entities.forEach((ent) => {
        const row = document.createElement("div");
        row.className = "entity-row";

        let icon = "👤";
        if (ent.entity_type === "vehicle") icon = "🚗";
        else if (ent.entity_type === "pet") icon = "🐶";

        row.innerHTML = `
          <div class="entity-meta">
            <span>${icon}</span>
            <div>
              <div style="font-size:0.8125rem;font-weight:600;">${ent.name}</div>
              <div class="text-muted" style="font-size:0.6875rem;">${ent.entity_type} ${ent.species ? `(${ent.species})` : ""}</div>
            </div>
          </div>
          <div class="entity-actions">
            <button class="btn" style="padding:2px 6px;font-size:0.6875rem;" title="Delete entity">✕</button>
          </div>
        `;

        const delBtn = row.querySelector("button");
        delBtn.addEventListener("click", async () => {
          await fetch(resolveUrl(`/api/entities/${encodeURIComponent(ent.id)}`), {
            method: "DELETE",
          });
          await this.loadEntities();
        });

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
}

