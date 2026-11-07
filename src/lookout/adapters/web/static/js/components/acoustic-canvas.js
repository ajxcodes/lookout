/**
 * Acoustic waveform sine animation and dynamic VU decibel meter component.
 */
export class AcousticVisualizer {
  constructor({ canvasElement, vuBarElement, vuTextElement }) {
    this.canvas = canvasElement;
    this.vuBar = vuBarElement;
    this.vuText = vuTextElement;
    this.ctx = this.canvas ? this.canvas.getContext("2d") : null;
    this.animationId = null;
    this.phase = 0;
    this.targetDb = -36;
    this.currentDb = -36;
    this.isPlaying = false;

    this.resizeCanvas();
    window.addEventListener("resize", () => this.resizeCanvas());
  }

  resizeCanvas() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = rect.width * (window.devicePixelRatio || 1);
    this.canvas.height = rect.height * (window.devicePixelRatio || 1);
  }

  setAudioDecibels(db) {
    if (db === null || db === undefined) {
      this.targetDb = -36;
    } else {
      this.targetDb = Math.max(-36, Math.min(0, db));
    }
  }

  start() {
    this.isPlaying = true;
    if (!this.animationId) {
      this.renderLoop();
    }
  }

  stop() {
    this.isPlaying = false;
    this.targetDb = -36;
  }

  renderLoop() {
    this.update();
    this.draw();
    this.animationId = requestAnimationFrame(() => this.renderLoop());
  }

  update() {
    // Smooth decibel meter interpolation
    this.currentDb += (this.targetDb - this.currentDb) * 0.15;
    this.phase += this.isPlaying ? 0.08 : 0.01;

    // Update VU meter bar (0% to 100% mapped from -36dB to 0dB)
    if (this.vuBar) {
      const percentage = Math.max(0, Math.min(100, ((this.currentDb + 36) / 36) * 100));
      this.vuBar.style.width = `${percentage}%`;
    }

    if (this.vuText) {
      this.vuText.textContent = `${Math.round(this.currentDb)} dB`;
    }
  }

  draw() {
    if (!this.ctx || !this.canvas) return;
    const { width, height } = this.canvas;
    this.ctx.clearRect(0, 0, width, height);

    const midY = height / 2;
    const amplitudeFactor = (this.currentDb + 36) / 36;
    const amplitude = this.isPlaying ? Math.max(2, midY * 0.8 * amplitudeFactor) : 2;

    this.ctx.lineWidth = 2 * (window.devicePixelRatio || 1);
    this.ctx.strokeStyle = this.isPlaying ? "#8b5cf6" : "#64748b";
    this.ctx.beginPath();

    for (let x = 0; x < width; x += 4) {
      const y = midY + Math.sin(x * 0.02 + this.phase) * amplitude * Math.sin((x / width) * Math.PI);
      if (x === 0) {
        this.ctx.moveTo(x, y);
      } else {
        this.ctx.lineTo(x, y);
      }
    }

    this.ctx.stroke();
  }
}
