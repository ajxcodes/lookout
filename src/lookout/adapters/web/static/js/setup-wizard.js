/**
 * 5-Step First-Run Onboarding Setup Wizard Controller.
 */
import { resolveUrl } from "./utils/url.js";

export class SetupWizard {
  constructor() {
    this.currentStep = 1;
    this.totalSteps = 5;

    this.configData = {
      system_name: "Lookout",
      residents: [],
      pets: [],
      cameras: [],
      notification_targets: [],
      storage: {
        base_path: "./data",
        database_path: "./data/lookout.db",
        wal_mode: true,
        routine_retention_days: 7,
        critical_retention_days: 30,
        busy_timeout_ms: 5000,
        retention_days: 30,
        max_storage_gb: 100.0,
      },
      go2rtc: {
        api_url: "http://localhost:1984",
        timeout_seconds: 10.0,
      },
      ring: {
        auth_token: null,
        api_url: "https://api.ring.com",
        timeout_seconds: 15.0,
      },
      transcode: {
        ffmpeg_path: "ffmpeg",
        ffprobe_path: "ffprobe",
        use_stream_copy: true,
        max_threads: 2,
        nice_level: 10,
        temp_dir: "/dev/shm",
        gif_fps: 6,
        gif_width: 320,
        gif_duration: 2.5,
        thumbnail_count: 5,
      },
      cascade: {
        tiers: ["ollama", "opencode_go", "gemini", "opencode_free", "local_edge", "offline"],
        total_timeout_seconds: 25.0,
        fallback_category: "unknown_visitor",
      },
      ollama: {
        host: "http://localhost:11434",
        model: "moondream",
        timeout_seconds: 5.0,
        enabled: true,
      },
      opencode: {
        api_key: null,
        api_url: "https://api.opencode.ai/v1",
        model_go: "deepseek-v4.1-flash",
        model_free: "deepseek-v4.1-flash-free",
        timeout_seconds: 8.0,
        enabled: true,
      },
      gemini: {
        api_key: null,
        api_url: "https://generativelanguage.googleapis.com/v1beta",
        model: "gemini-3.5-flash-lite",
        timeout_seconds: 8.0,
        enabled: true,
      },
      face_recognition: {
        similarity_threshold: 0.6,
        model_name: "facenet-light",
        gallery_dir: null,
        enabled: true,
      },
      immich: {
        url: "http://localhost:2283",
        api_key: null,
        sync_interval_hours: 24,
        include_persons: [],
        enabled: true,
      },
      audio_classifier: {
        model_path: "models/yamnet.tflite",
        confidence_threshold: 0.5,
        sample_rate: 16000,
        emergency_labels: ["glass_break", "alarm", "smoke_alarm", "gunshot", "scream"],
        pet_labels: ["dog_bark", "dog_whimper", "cat_meow"],
        enabled: true,
      },
    };

    this.initElements();
    this.bindEvents();
    this.updateStepView();
  }

  initElements() {
    this.indicators = document.querySelectorAll(".step-indicator");
    this.panels = document.querySelectorAll(".step-panel");
    this.btnPrev = document.getElementById("btn-prev");
    this.btnNext = document.getElementById("btn-next");
    this.btnFinalize = document.getElementById("btn-finalize");

    // Step 1: AI
    this.aiProviderSelect = document.getElementById("ai-provider");
    this.aiHostInput = document.getElementById("ai-host");
    this.aiKeyInput = document.getElementById("ai-key");
    this.aiModelInput = document.getElementById("ai-model");
    this.btnTestAi = document.getElementById("btn-test-ai");
    this.aiStatusPill = document.getElementById("ai-status-pill");

    // Step 2: Camera
    this.camIdInput = document.getElementById("cam-id");
    this.camNameInput = document.getElementById("cam-name");
    this.camUrlInput = document.getElementById("cam-url");
    this.camTypeSelect = document.getElementById("cam-type");
    this.btnTestCam = document.getElementById("btn-test-cam");
    this.camStatusPill = document.getElementById("cam-status-pill");
    this.btnAddCam = document.getElementById("btn-add-cam");
    this.cameraListEl = document.getElementById("cameras-list");

    // Step 2: Sidecar Integrations & 2FA Modal
    this.badgeWyzeStatus = document.getElementById("badge-wyze-status");
    this.badgeRingStatus = document.getElementById("badge-ring-status");
    this.btnAdoptWyze = document.getElementById("btn-adopt-wyze");
    this.btnAdoptRing = document.getElementById("btn-adopt-ring");
    this.btnAuthWyze = document.getElementById("btn-auth-wyze");
    this.btnAuthRing = document.getElementById("btn-auth-ring");
    this.modal2fa = document.getElementById("modal-2fa");
    this.modal2faClose = document.getElementById("modal-2fa-close");
    this.modal2faTitle = document.getElementById("modal-2fa-title");
    this.modal2faDesc = document.getElementById("modal-2fa-desc");
    this.code2faInput = document.getElementById("code-2fa");
    this.btnVerify2fa = document.getElementById("btn-verify-2fa");
    this.btnCancel2fa = document.getElementById("btn-cancel-2fa");
    this.btnResend2fa = document.getElementById("btn-resend-2fa");
    this.countdown2fa = document.getElementById("countdown-2fa");
    this.status2fa = document.getElementById("status-2fa");
    this.active2faProvider = null;
    this.active2faSessionId = null;
    this.countdownTimer = null;

    // Step 3: Residents & Pets
    this.residentNameInput = document.getElementById("resident-name");
    this.residentIdInput = document.getElementById("resident-id");
    this.faceDropzone = document.getElementById("setup-face-dropzone");
    this.faceFileInput = document.getElementById("setup-face-file");
    this.faceUploadStatus = document.getElementById("face-upload-status");
    this.petNameInput = document.getElementById("pet-name");
    this.petSpeciesSelect = document.getElementById("pet-species");

    // Step 4: Notifications
    this.notifNameInput = document.getElementById("notif-name");
    this.notifTypeSelect = document.getElementById("notif-type");
    this.notifDestInput = document.getElementById("notif-dest");
    this.btnTestNotif = document.getElementById("btn-test-notif");
    this.notifStatusPill = document.getElementById("notif-status-pill");

    // Step 5: Finalize
    this.yamlPreview = document.getElementById("yaml-preview");
  }

  bindEvents() {
    this.btnPrev.addEventListener("click", () => this.prevStep());
    this.btnNext.addEventListener("click", () => this.nextStep());
    this.btnFinalize.addEventListener("click", () => this.finalize());

    // Step 1: AI Test
    this.btnTestAi.addEventListener("click", async () => {
      const provider = this.aiProviderSelect.value;
      const host = this.aiHostInput.value.trim() || undefined;
      const apiKey = this.aiKeyInput.value.trim() || undefined;
      const model = this.aiModelInput.value.trim() || undefined;

      this.setStatus(this.aiStatusPill, "loading", "Testing connection...");
      try {
        const res = await fetch(resolveUrl("/api/setup/test-ai"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider, host, api_key: apiKey, model }),
        });
        const data = await res.json();
        if (data.status === "ok") {
          this.setStatus(this.aiStatusPill, "success", `Connected: ${data.message}`);
        } else {
          this.setStatus(this.aiStatusPill, "error", data.message || "Failed");
        }
      } catch (err) {
        this.setStatus(this.aiStatusPill, "error", err.message);
      }
    });

    // Step 2: Camera Test & Add
    this.btnTestCam.addEventListener("click", async () => {
      const streamUrl = this.camUrlInput.value.trim();
      if (!streamUrl) return;

      this.setStatus(this.camStatusPill, "loading", "Probing stream...");
      try {
        const res = await fetch(resolveUrl("/api/setup/test-camera"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stream_url: streamUrl }),
        });
        const data = await res.json();
        if (data.reachable) {
          this.setStatus(this.camStatusPill, "success", "Stream Reachable");
        } else {
          this.setStatus(this.camStatusPill, "error", data.message || "Unreachable");
        }
      } catch (err) {
        this.setStatus(this.camStatusPill, "error", err.message);
      }
    });

    this.btnAddCam.addEventListener("click", () => {
      const id = this.camIdInput.value.trim();
      const name = this.camNameInput.value.trim() || id;
      const stream_url = this.camUrlInput.value.trim();
      const camera_type = this.camTypeSelect.value;

      if (!id || !stream_url) return;
      this.configData.cameras.push({
        id,
        name,
        stream_url,
        camera_type,
        enabled: true,
        sensitivity: 0.5,
        record_duration: 10.0,
      });

      this.renderCameraList();
      this.camIdInput.value = "";
      this.camNameInput.value = "";
      this.camUrlInput.value = "";
    });

    // Sidecar bridge & 2FA events
    if (this.btnAuthWyze) {
      this.btnAuthWyze.addEventListener("click", () => this.startAuth("wyze"));
    }
    if (this.btnAuthRing) {
      this.btnAuthRing.addEventListener("click", () => this.startAuth("ring"));
    }
    if (this.btnAdoptWyze) {
      this.btnAdoptWyze.addEventListener("click", () =>
        this.adoptBridgeStream("wyze", "rtsp://localhost:8554/wyze_cam", "wyze-camera", "Wyze Cam Bridge")
      );
    }
    if (this.btnAdoptRing) {
      this.btnAdoptRing.addEventListener("click", () =>
        this.adoptBridgeStream("ring", "ring://doorbell_1", "ring-doorbell", "Ring Doorbell")
      );
    }
    if (this.btnVerify2fa) {
      this.btnVerify2fa.addEventListener("click", () => this.submit2fa());
    }
    if (this.btnCancel2fa) {
      this.btnCancel2fa.addEventListener("click", () => this.close2faModal());
    }
    if (this.modal2faClose) {
      this.modal2faClose.addEventListener("click", () => this.close2faModal());
    }
    if (this.btnResend2fa) {
      this.btnResend2fa.addEventListener("click", () => this.resend2fa());
    }

    // Step 3: Face Upload
    if (this.faceDropzone && this.faceFileInput) {
      this.faceDropzone.addEventListener("click", () => this.faceFileInput.click());
      this.faceFileInput.addEventListener("change", async (e) => {
        if (e.target.files && e.target.files[0]) {
          await this.uploadFace(e.target.files[0]);
        }
      });
    }

    // Step 4: Notification Test
    this.btnTestNotif.addEventListener("click", async () => {
      const targetType = this.notifTypeSelect.value;
      const destination = this.notifDestInput.value.trim();
      if (!destination) return;

      this.setStatus(this.notifStatusPill, "loading", "Dispatching ping...");
      try {
        const res = await fetch(resolveUrl("/api/setup/test-notification"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_type: targetType, destination }),
        });
        const data = await res.json();
        if (data.sent) {
          this.setStatus(this.notifStatusPill, "success", "Ping Delivered");
        } else {
          this.setStatus(this.notifStatusPill, "error", data.message || "Failed");
        }
      } catch (err) {
        this.setStatus(this.notifStatusPill, "error", err.message);
      }
    });
  }

  async uploadFace(file) {
    const residentId = this.residentIdInput.value.trim() || "resident-1";
    const formData = new FormData();
    formData.append("file", file);
    formData.append("resident_id", residentId);

    try {
      this.faceUploadStatus.textContent = "Uploading face...";
      const res = await fetch(resolveUrl("/api/setup/upload-face"), {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        this.faceUploadStatus.textContent = `Uploaded ${file.name} for ${residentId}`;
        this.faceUploadStatus.style.color = "var(--color-pet)";
      }
    } catch (err) {
      this.faceUploadStatus.textContent = `Upload failed: ${err.message}`;
      this.faceUploadStatus.style.color = "var(--color-threat)";
    }
  }

  renderCameraList() {
    if (!this.cameraListEl) return;
    this.cameraListEl.innerHTML = "";

    this.configData.cameras.forEach((c, idx) => {
      const item = document.createElement("div");
      item.style.display = "flex";
      item.style.alignItems = "center";
      item.style.justifyContent = "space-between";
      item.style.padding = "var(--space-xs) var(--space-sm)";
      item.style.background = "var(--bg-subtle)";
      item.style.borderRadius = "var(--radius-md)";
      item.style.fontSize = "0.8125rem";
      item.innerHTML = `
        <span><strong>${c.name}</strong> (${c.id}) - <code>${c.stream_url}</code></span>
        <button class="btn btn-danger" style="padding:2px 8px;font-size:0.75rem;">Remove</button>
      `;

      item.querySelector("button").addEventListener("click", () => {
        this.configData.cameras.splice(idx, 1);
        this.renderCameraList();
      });

      this.cameraListEl.appendChild(item);
    });
  }

  setStatus(pillEl, type, text) {
    if (!pillEl) return;
    pillEl.className = `status-pill ${type}`;
    pillEl.textContent = text;
  }

  nextStep() {
    this.gatherStepData();
    if (this.currentStep < this.totalSteps) {
      this.currentStep += 1;
      this.updateStepView();
    }
  }

  prevStep() {
    if (this.currentStep > 1) {
      this.currentStep -= 1;
      this.updateStepView();
    }
  }

  gatherStepData() {
    if (this.currentStep === 1) {
      const p = this.aiProviderSelect.value;
      if (p === "ollama") {
        this.configData.ollama.host = this.aiHostInput.value.trim() || this.configData.ollama.host;
        this.configData.ollama.model = this.aiModelInput.value.trim() || this.configData.ollama.model;
      } else if (p === "gemini") {
        this.configData.gemini.api_key = this.aiKeyInput.value.trim() || null;
      } else if (p === "opencode") {
        this.configData.opencode.api_key = this.aiKeyInput.value.trim() || null;
      }
    } else if (this.currentStep === 3) {
      const resName = this.residentNameInput.value.trim();
      const resId = this.residentIdInput.value.trim() || "resident-1";
      if (resName) {
        this.configData.residents = [{ id: resId, name: resName, notification_targets: ["default-target"], enrolled_faces: [] }];
      }
      const petName = this.petNameInput.value.trim();
      if (petName) {
        this.configData.pets = [{ id: petName.toLowerCase(), name: petName, species: this.petSpeciesSelect.value }];
      }
    } else if (this.currentStep === 4) {
      const targetName = this.notifNameInput.value.trim() || "default-target";
      const targetType = this.notifTypeSelect.value;
      const dest = this.notifDestInput.value.trim() || "http://localhost:8080/webhook";
      this.configData.notification_targets = [
        {
          name: targetName,
          target_type: targetType,
          destination: dest,
          min_threat_level: 1,
          enabled: true,
        },
      ];
      if (this.configData.residents.length > 0) {
        this.configData.residents[0].notification_targets = [targetName];
      }
    }
  }

  updateStepView() {
    this.indicators.forEach((ind, idx) => {
      const stepIdx = idx + 1;
      ind.classList.toggle("active", stepIdx === this.currentStep);
      ind.classList.toggle("completed", stepIdx < this.currentStep);
    });

    this.panels.forEach((panel, idx) => {
      panel.classList.toggle("active", idx + 1 === this.currentStep);
    });

    this.btnPrev.style.display = this.currentStep === 1 ? "none" : "inline-flex";
    this.btnNext.style.display = this.currentStep === this.totalSteps ? "none" : "inline-flex";
    this.btnFinalize.style.display = this.currentStep === this.totalSteps ? "inline-flex" : "none";

    if (this.currentStep === 2) {
      this.checkSidecars();
    }

    if (this.currentStep === 5) {
      this.gatherStepData();
      // Ensure at least one default camera exists if none was added
      if (this.configData.cameras.length === 0) {
        this.configData.cameras.push({
          id: "front-porch",
          name: "Front Porch",
          stream_url: "rtsp://localhost:8554/front_porch",
          camera_type: "generic",
          enabled: true,
          sensitivity: 0.5,
          record_duration: 10.0,
        });
      }
      if (this.configData.notification_targets.length === 0) {
        this.configData.notification_targets.push({
          name: "default-target",
          target_type: "webhook",
          destination: "http://localhost:8080/webhook",
          min_threat_level: 1,
          enabled: true,
        });
      }
      this.yamlPreview.textContent = JSON.stringify(this.configData, null, 2);
    }
  }

  async finalize() {
    this.btnFinalize.disabled = true;
    this.btnFinalize.textContent = "Writing Configuration...";

    try {
      const res = await fetch(resolveUrl("/api/setup/finalize"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.configData),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Setup finalization failed");
      }

      // Success - Redirect to root viewer
      window.location.href = resolveUrl("/");
    } catch (err) {
      alert(`Error finalizing setup: ${err.message}`);
      this.btnFinalize.disabled = false;
      this.btnFinalize.textContent = "Finalize & Launch";
    }
  }

  async checkSidecars() {
    try {
      const res = await fetch(resolveUrl("/api/auth/status"));
      if (!res.ok) return;
      const data = await res.json();

      if (this.badgeWyzeStatus && data.wyze) {
        if (data.wyze.status === "pre_authenticated") {
          this.badgeWyzeStatus.textContent = "Connected (Pre-authenticated)";
          this.badgeWyzeStatus.style.background = "#10b981";
          this.badgeWyzeStatus.style.color = "#ffffff";
        } else if (data.wyze.status === "authenticated") {
          this.badgeWyzeStatus.textContent = "Connected (Authenticated)";
          this.badgeWyzeStatus.style.background = "#10b981";
          this.badgeWyzeStatus.style.color = "#ffffff";
        } else {
          this.badgeWyzeStatus.textContent = "Unconfigured";
          this.badgeWyzeStatus.style.background = "var(--bg-subtle)";
          this.badgeWyzeStatus.style.color = "var(--text-muted)";
        }
      }

      if (this.badgeRingStatus && data.ring) {
        if (data.ring.status === "pre_authenticated") {
          this.badgeRingStatus.textContent = "Connected (Pre-authenticated)";
          this.badgeRingStatus.style.background = "#10b981";
          this.badgeRingStatus.style.color = "#ffffff";
        } else if (data.ring.status === "authenticated") {
          this.badgeRingStatus.textContent = "Connected (Authenticated)";
          this.badgeRingStatus.style.background = "#10b981";
          this.badgeRingStatus.style.color = "#ffffff";
        } else {
          this.badgeRingStatus.textContent = "Unconfigured";
          this.badgeRingStatus.style.background = "var(--bg-subtle)";
          this.badgeRingStatus.style.color = "var(--text-muted)";
        }
      }
    } catch (err) {
      console.warn("Could not probe sidecar status:", err);
    }
  }

  async startAuth(provider) {
    const creds = { email: `${provider}_user@example.com`, password: "password123" };
    try {
      const res = await fetch(resolveUrl(`/api/auth/${provider}/login`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(creds),
      });
      const data = await res.json();
      if (data.status === "2fa_required") {
        this.open2faModal(provider, data.session_id, data.message);
      } else if (data.status === "authenticated") {
        alert(`${provider.toUpperCase()} authenticated successfully.`);
        await this.checkSidecars();
      }
    } catch (err) {
      alert(`Login failed: ${err.message}`);
    }
  }

  open2faModal(provider, sessionId, message) {
    this.active2faProvider = provider;
    this.active2faSessionId = sessionId;
    if (this.modal2faTitle) {
      this.modal2faTitle.textContent = `${provider === "wyze" ? "Wyze" : "Ring"} 2FA Verification`;
    }
    if (this.modal2faDesc && message) {
      this.modal2faDesc.textContent = message;
    }
    if (this.code2faInput) {
      this.code2faInput.value = "";
    }
    if (this.status2fa) {
      this.status2fa.textContent = "";
      this.status2fa.style.color = "inherit";
    }
    if (this.modal2fa) {
      this.modal2fa.style.display = "flex";
    }

    // Start 60s countdown
    let timeLeft = 60;
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    if (this.countdown2fa) this.countdown2fa.textContent = `Expires in: ${timeLeft}s`;
    this.countdownTimer = setInterval(() => {
      timeLeft -= 1;
      if (this.countdown2fa) {
        this.countdown2fa.textContent = timeLeft > 0 ? `Expires in: ${timeLeft}s` : "Code expired";
      }
      if (timeLeft <= 0) {
        clearInterval(this.countdownTimer);
      }
    }, 1000);
  }

  close2faModal() {
    if (this.modal2fa) {
      this.modal2fa.style.display = "none";
    }
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
    }
  }

  async resend2fa() {
    if (this.status2fa) {
      this.status2fa.textContent = "Resending 2FA code...";
    }
    await this.startAuth(this.active2faProvider || "wyze");
  }

  async submit2fa() {
    const code = this.code2faInput ? this.code2faInput.value.trim() : "";
    if (!code) {
      if (this.status2fa) {
        this.status2fa.textContent = "Please enter the 6-digit code";
        this.status2fa.style.color = "#ef4444";
      }
      return;
    }

    if (this.btnVerify2fa) {
      this.btnVerify2fa.disabled = true;
      this.btnVerify2fa.textContent = "Verifying...";
    }

    try {
      const res = await fetch(resolveUrl(`/api/auth/${this.active2faProvider}/verify`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: this.active2faSessionId,
          code: code,
        }),
      });

      const data = await res.json();
      if (data.status === "authenticated") {
        if (this.status2fa) {
          this.status2fa.textContent = "Verification successful!";
          this.status2fa.style.color = "#10b981";
        }
        setTimeout(() => {
          this.close2faModal();
          this.checkSidecars();
        }, 600);
      } else {
        if (this.status2fa) {
          this.status2fa.textContent = data.message || "Invalid verification code";
          this.status2fa.style.color = "#ef4444";
        }
      }
    } catch (err) {
      if (this.status2fa) {
        this.status2fa.textContent = `Verification error: ${err.message}`;
        this.status2fa.style.color = "#ef4444";
      }
    } finally {
      if (this.btnVerify2fa) {
        this.btnVerify2fa.disabled = false;
        this.btnVerify2fa.textContent = "Verify Code";
      }
    }
  }

  adoptBridgeStream(provider, defaultUrl, defaultId, defaultName) {
    if (this.camIdInput) this.camIdInput.value = defaultId;
    if (this.camNameInput) this.camNameInput.value = defaultName;
    if (this.camUrlInput) this.camUrlInput.value = defaultUrl;
    if (this.camTypeSelect) this.camTypeSelect.value = provider;
    if (this.btnAddCam) this.btnAddCam.click();
  }
}


document.addEventListener("DOMContentLoaded", () => {
  new SetupWizard();
});
