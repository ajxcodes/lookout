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


    this.bindEvents();
  }

  bindEvents() {
    if (!this.modal) return;

    const closeBtn = this.modal.querySelector("#modal-close-btn");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => this.close());
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

      const provRes = await fetch(resolveUrl("/api/auth/providers"));
      const providers = provRes.ok ? (await provRes.json()).providers || [] : [];

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
}

