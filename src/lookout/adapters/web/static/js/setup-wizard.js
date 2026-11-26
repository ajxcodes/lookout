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
        fallback_category: "abstained",
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
        sound_label_categories: {
          glass_break: "acoustic_anomaly",
          car_alarm: "acoustic_anomaly",
          smoke_alarm: "acoustic_anomaly",
          siren: "acoustic_anomaly",
          gunshot: "acoustic_anomaly",
          scream: "acoustic_anomaly",
          alarm: null,
          other: null,
          ambient: null,
          silence: null,
          doorbell: null,
          speech: null,
          bark: null,
          dog_bark: null,
          dog_whimper: null,
          cat_meow: null,
        },
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
    this.camLocationInput = document.getElementById("cam-location");
    this.camEnabledInput = document.getElementById("cam-enabled");
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

    // Real credential capture (no hardcoded credentials anywhere in the wizard).
    this.modalCredentials = document.getElementById("modal-credentials");
    this.credTitle = document.getElementById("cred-title");
    this.credDesc = document.getElementById("cred-desc");
    this.credEmail = document.getElementById("cred-email");
    this.credPassword = document.getElementById("cred-password");
    this.credApiId = document.getElementById("cred-api-id");
    this.credApiKey = document.getElementById("cred-api-key");
    this.credTotp = document.getElementById("cred-totp");
    this.credWyzeFields = document.getElementById("cred-wyze-fields");
    this.credStatus = document.getElementById("cred-status");
    this.btnCredSubmit = document.getElementById("btn-cred-submit");
    this.btnCredCancel = document.getElementById("btn-cred-cancel");
    this.btnCredClose = document.getElementById("cred-close");
    this.activeCredentialProvider = null;
    this.forceAuth = false;
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
      const location = this.camLocationInput ? this.camLocationInput.value.trim() || null : null;
      const enabled = this.camEnabledInput ? this.camEnabledInput.checked : true;

      if (!id || !stream_url) return;
      const isNative = camera_type === "ring" || camera_type === "wyze";
      this.configData.cameras.push({
        id,
        name,
        stream_url,
        camera_type,
        provider: camera_type === "ring" ? "ring" : camera_type === "wyze" ? "wyze" : (camera_type === "go2rtc" ? "go2rtc" : "generic"),
        native_provider: isNative ? camera_type : null,
        native_device_id: isNative ? id : null,
        location,
        enabled,
        sensitivity: 0.5,
        record_duration: 10.0,
      });

      this.renderCameraList();
      this.camIdInput.value = "";
      this.camNameInput.value = "";
      this.camUrlInput.value = "";
      if (this.camLocationInput) this.camLocationInput.value = "";
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
    if (this.btnCredSubmit) {
      this.btnCredSubmit.addEventListener("click", () => this.submitCredentials());
    }
    if (this.btnCredCancel) {
      this.btnCredCancel.addEventListener("click", () => this.closeCredentialsModal());
    }
    if (this.btnCredClose) {
      this.btnCredClose.addEventListener("click", () => this.closeCredentialsModal());
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
      // No default/mock camera is auto-seeded; cameras must be added explicitly.
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
      // 1. Fetch provider manifests to render dynamic brand cards
      try {
        const provRes = await fetch(resolveUrl("/api/providers"));
        if (provRes.ok) {
          const provData = await provRes.json();
          const providers = provData.providers || [];
          const container = document.getElementById("sidecar-providers-list");
          if (container && providers.length > 0) {
            providers.forEach((prov) => {
              let card = document.getElementById(`card-provider-${prov.id}`);
              if (!card) {
                card = document.createElement("div");
                card.className = "sidecar-card";
                card.id = `card-provider-${prov.id}`;
                card.style.cssText =
                  "border:1px solid var(--border-subtle);border-radius:var(--radius-md);padding:var(--space-sm);background:var(--bg-surface);";
                card.innerHTML = `
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-xs);">
                    <span style="font-weight:600;font-size:0.8125rem;">${prov.display_name || prov.name}</span>
                    <span class="badge" id="badge-${prov.id}-status" style="font-size:0.6875rem;padding:2px 6px;border-radius:4px;background:var(--bg-subtle);color:var(--text-muted);">Checking...</span>
                  </div>
                  <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:var(--space-xs);" id="desc-${prov.id}">${prov.description || ""}</div>
                  <div style="display:flex;gap:var(--space-xs);flex-wrap:wrap;">
                    <button class="btn btn-sm" id="btn-adopt-${prov.id}" type="button" style="font-size:0.75rem;padding:3px 8px;">Adopt Stream</button>
                    <button class="btn btn-sm btn-primary" id="btn-auth-${prov.id}" type="button" style="font-size:0.75rem;padding:3px 8px;">Authenticate</button>
                    <button class="btn btn-sm" id="btn-rotate-${prov.id}" type="button" style="font-size:0.75rem;padding:3px 8px;">Rotate credentials</button>
                    <button class="btn btn-sm" id="btn-logout-${prov.id}" type="button" style="font-size:0.75rem;padding:3px 8px;">Log out</button>
                  </div>
                `;
                container.appendChild(card);
                const adoptBtn = document.getElementById(`btn-adopt-${prov.id}`);
                if (adoptBtn) {
                  adoptBtn.addEventListener("click", () =>
                    this.adoptBridgeStream(
                      prov.id,
                      prov.default_stream_url || `rtsp://localhost:8554/${prov.id}`,
                      `${prov.id}-camera`,
                      prov.display_name
                    )
                  );
                }
                const authBtn = document.getElementById(`btn-auth-${prov.id}`);
                if (authBtn) {
                  authBtn.addEventListener("click", () => this.startAuth(prov.id));
                }
                const rotateBtn = document.getElementById(`btn-rotate-${prov.id}`);
                if (rotateBtn) {
                  rotateBtn.addEventListener("click", () => this.startAuth(prov.id, true));
                }
                const logoutBtn = document.getElementById(`btn-logout-${prov.id}`);
                if (logoutBtn) {
                  logoutBtn.addEventListener("click", () => this.logoutProvider(prov.id));
                }
              }
            });
          }
        }
      } catch (err) {
        console.warn("Could not fetch provider manifests:", err);
      }

      // 2. Query status report
      const res = await fetch(resolveUrl("/api/auth/status"));
      if (!res.ok) return;
      const data = await res.json();

      Object.keys(data).forEach((pid) => {
        const badge = document.getElementById(`badge-${pid}-status`);
        const pStatus = data[pid];
        if (badge && pStatus && typeof pStatus === "object") {
          if (pStatus.status === "pre_authenticated") {
            badge.textContent = "Connected (Pre-authenticated)";
            badge.style.background = "#10b981";
            badge.style.color = "#ffffff";
          } else if (pStatus.status === "authenticated" || pStatus.status === "connected") {
            badge.textContent = "Connected (Authenticated)";
            badge.style.background = "#10b981";
            badge.style.color = "#ffffff";
          } else if (pStatus.status === "2fa_required") {
            badge.textContent = "2FA required";
            badge.style.background = "#f59e0b";
            badge.style.color = "#ffffff";
          } else if (pStatus.status === "logged_out") {
            badge.textContent = "Logged out";
            badge.style.background = "#6b7280";
            badge.style.color = "#ffffff";
          } else if (pStatus.status === "error") {
            badge.textContent = "Authentication error";
            badge.style.background = "#ef4444";
            badge.style.color = "#ffffff";
          } else {
            badge.textContent = "Unconfigured";
            badge.style.background = "var(--bg-subtle)";
            badge.style.color = "var(--text-muted)";
          }
        }
      });
    } catch (err) {
      console.warn("Could not probe sidecar status:", err);
    }
  }

  startAuth(provider, force = false) {
    this.activeCredentialProvider = provider;
    this.forceAuth = Boolean(force);
    if (this.credTitle) {
      this.credTitle.textContent = force
        ? `Rotate ${provider.toUpperCase()} credentials`
        : `${provider.toUpperCase()} Login`;
    }
    if (this.credDesc) {
      this.credDesc.textContent = force
        ? "Re-enter your credentials to issue fresh tokens. Existing credentials are replaced."
        : "Enter your real provider credentials to issue authentication tokens.";
    }
    if (this.credEmail) this.credEmail.value = "";
    if (this.credPassword) this.credPassword.value = "";
    if (this.credApiId) this.credApiId.value = "";
    if (this.credApiKey) this.credApiKey.value = "";
    if (this.credTotp) this.credTotp.value = "";
    if (this.credWyzeFields) {
      this.credWyzeFields.style.display = provider === "wyze" ? "block" : "none";
    }
    if (this.credStatus) {
      this.credStatus.textContent = "";
      this.credStatus.style.color = "inherit";
    }
    if (this.modalCredentials) this.modalCredentials.style.display = "flex";
  }

  closeCredentialsModal() {
    if (this.modalCredentials) this.modalCredentials.style.display = "none";
    if (this.credPassword) this.credPassword.value = "";
    if (this.credApiKey) this.credApiKey.value = "";
    if (this.credTotp) this.credTotp.value = "";
  }

  async submitCredentials() {
    const provider = this.activeCredentialProvider;
    if (!provider) return;
    const creds = {
      email: (this.credEmail && this.credEmail.value.trim()) || "",
      password: (this.credPassword && this.credPassword.value) || "",
      api_id: (this.credApiId && this.credApiId.value.trim()) || undefined,
      api_key: (this.credApiKey && this.credApiKey.value) || undefined,
      totp_key: (this.credTotp && this.credTotp.value) || undefined,
      force: this.forceAuth,
    };
    if (!creds.email || !creds.password) {
      if (this.credStatus) {
        this.credStatus.textContent = "Email and password are required.";
        this.credStatus.style.color = "#ef4444";
      }
      return;
    }
    if (this.btnCredSubmit) {
      this.btnCredSubmit.disabled = true;
      this.btnCredSubmit.textContent = "Authenticating...";
    }
    try {
      const endpoint = this.forceAuth ? "rotate" : "login";
      const res = await fetch(resolveUrl(`/api/auth/${provider}/${endpoint}`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(creds),
      });
      const data = await res.json();
      if (data.status === "2fa_required") {
        this.closeCredentialsModal();
        this.open2faModal(provider, data.session_id, data.message);
      } else if (data.status === "pre_authenticated") {
        this.closeCredentialsModal();
        await this.checkSidecars();
      } else if (data.status === "authenticated") {
        this.closeCredentialsModal();
        await this.checkSidecars();
      } else {
        if (this.credStatus) {
          this.credStatus.textContent = data.message || "Authentication failed";
          this.credStatus.style.color = "#ef4444";
        }
      }
    } catch (err) {
      if (this.credStatus) {
        this.credStatus.textContent = `Authentication error: ${err.message}`;
        this.credStatus.style.color = "#ef4444";
      }
    } finally {
      if (this.btnCredSubmit) {
        this.btnCredSubmit.disabled = false;
        this.btnCredSubmit.textContent = "Authenticate";
      }
    }
  }

  async logoutProvider(provider) {
    try {
      const res = await fetch(resolveUrl(`/api/auth/${provider}/logout`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (res.ok) {
        await this.checkSidecars();
      }
    } catch (err) {
      console.warn(`logout failed for ${provider}:`, err);
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
  const wizard = new SetupWizard();
  // Expose the wizard so the viewer's "Reauthenticate" camera action can open
  // this same credential modal with force=true (the AC4 Rotate path).
  window.lookoutWizard = wizard;

  // AC10: "Reauthenticate" on a camera card may route here with ?reauth=<provider>.
  try {
    const params = new URLSearchParams(window.location.search);
    const reauthProvider = params.get("reauth");
    if (reauthProvider) {
      wizard.startAuth(reauthProvider, true);
    }
  } catch (_) {
    // Ignore malformed query strings; the wizard still initializes normally.
  }
});
