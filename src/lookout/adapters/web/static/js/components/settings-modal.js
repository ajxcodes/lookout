/**
 * Admin settings modal and face gallery management component.
 */
export class SettingsModal {
  constructor({ modalBackdrop, onClose, onAiModeChanged }) {
    this.modal = modalBackdrop;
    this.onClose = onClose;
    this.onAiModeChanged = onAiModeChanged;

    this.aiSelect = this.modal ? this.modal.querySelector("#setting-ai-mode") : null;
    this.dropzone = this.modal ? this.modal.querySelector("#face-dropzone") : null;
    this.facesContainer = this.modal ? this.modal.querySelector("#faces-grid") : null;
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
          const res = await fetch("/api/settings/ai-mode", {
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
  }

  close() {
    if (!this.modal) return;
    this.modal.classList.remove("open");
    if (this.onClose) {
      this.onClose();
    }
  }

  async loadSettings() {
    try {
      const res = await fetch("/api/settings/ai-mode");
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
      const res = await fetch("/api/settings/faces");
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
        card.innerHTML = `
          <img class="face-img" src="/media/faces/${encodeURIComponent(f.resident_id)}/${encodeURIComponent(f.filename)}" alt="${f.filename}" onerror="this.src='/static/placeholder.jpg'" />
          <button class="face-card-del" title="Delete face">✕</button>
        `;

        const delBtn = card.querySelector(".face-card-del");
        delBtn.addEventListener("click", async () => {
          await fetch(`/api/settings/faces/${encodeURIComponent(f.resident_id)}/${encodeURIComponent(f.filename)}`, {
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
      const res = await fetch("/api/settings/faces/upload", {
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
}
