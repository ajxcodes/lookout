/**
 * Acoustic waveform canvas and VU decibel meter.
 *
 * When an AnalyserNode is supplied (the app creates one for the playing event
 * video), the waveform and the VU reading come from *real* PCM/frequency data
 * read from that analyser. Without an analyser the canvas deliberately draws
 * a neutral flat line and reports "-- dB" instead of inventing a level.
 */
export class AcousticVisualizer {
  /**
   * @param {object} options
   * @param {HTMLCanvasElement?} options.canvasElement
   * @param {HTMLElement?} options.vuBarElement
   * @param {HTMLElement?} options.vuTextElement
   * @param {AnalyserNode|function|null} [options.analyser] An AnalyserNode (or a
   *   provider function returning one) wired to the event video's Web Audio
   *   graph. Optional: analysis stays inert until setAnalyser() is called.
   */
  constructor({ canvasElement, vuBarElement, vuTextElement, analyser = null } = {}) {
    this.canvas = canvasElement;
    this.vuBar = vuBarElement;
    this.vuText = vuTextElement;
    this.ctx = this.canvas ? this.canvas.getContext("2d") : null;
    this.animationId = null;
    this.analyser = analyser;
    this.timeData = null;
    this.freqData = null;
    this.audioFrame = null;
    this.currentDb = -60;
    this.hasSignal = false;
    this.isPlaying = false;

    this.resizeCanvas();
    window.addEventListener("resize", () => this.resizeCanvas());

    // The canvas lives in the focus stage, which is hidden at construction
    // time, so its first getBoundingClientRect() is 0x0. Resize it whenever
    // its CSS box changes (e.g. when focus view becomes visible) instead of
    // relying only on a window resize event, which never fires on entering
    // focus view. #acoustic-canvas is width/height:100% inside a fixed-height
    // wrapper, so resizing the backing store cannot feed back into the
    // observer.
    if (this.canvas && typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver(() => this.resizeCanvas());
      this.resizeObserver.observe(this.canvas);
    }

    if (this.analyser) {
      this.setAnalyser(this.analyser);
    }
  }

  resizeCanvas() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = rect.width * (window.devicePixelRatio || 1);
    this.canvas.height = rect.height * (window.devicePixelRatio || 1);
  }

  /** Attach (or replace) the analyser used for real audio analysis. */
  setAnalyser(analyser) {
    this.analyser = analyser || null;
    this.timeData = null;
    this.freqData = null;
    this.audioFrame = null;
    this.currentDb = -60;
    this.hasSignal = false;
  }

  /**
   * Legacy hook kept for API compatibility. It no longer fabricates a level:
   * the analyser (when present) is the only source of the VU reading.
   */
  setAudioDecibels(_db) {
    // Intentionally inert -- see setAnalyser() / update().
  }

  start() {
    this.isPlaying = true;
    if (!this.animationId) {
      this.renderLoop();
    }
  }

  stop() {
    this.isPlaying = false;
    this.audioFrame = null;
    this.hasSignal = false;
    this.currentDb = -60;
  }

  renderLoop() {
    this.update();
    this.draw();
    this.animationId = requestAnimationFrame(() => this.renderLoop());
  }

  _getAnalyser() {
    return typeof this.analyser === "function" ? this.analyser() : this.analyser;
  }

  /** Read one frame of real analyser data (time domain preferred). */
  _readAudioFrame() {
    const analyser = this._getAnalyser();
    if (!analyser) return null;

    if (typeof analyser.getByteTimeDomainData === "function") {
      if (!this.timeData || this.timeData.length !== analyser.fftSize) {
        this.timeData = new Uint8Array(analyser.fftSize);
      }
      analyser.getByteTimeDomainData(this.timeData);
      return { kind: "time", data: this.timeData };
    }

    if (typeof analyser.getByteFrequencyData === "function") {
      if (!this.freqData || this.freqData.length !== analyser.frequencyBinCount) {
        this.freqData = new Uint8Array(analyser.frequencyBinCount);
      }
      analyser.getByteFrequencyData(this.freqData);
      return { kind: "frequency", data: this.freqData };
    }

    return null;
  }

  update() {
    const frame = this.isPlaying ? this._readAudioFrame() : null;

    if (frame) {
      // RMS of the actual samples -> dBFS, floored at -60 dB.
      let sumSquares = 0;
      if (frame.kind === "time") {
        for (let i = 0; i < frame.data.length; i++) {
          const sample = (frame.data[i] - 128) / 128;
          sumSquares += sample * sample;
        }
      } else {
        for (let i = 0; i < frame.data.length; i++) {
          const sample = frame.data[i] / 255;
          sumSquares += sample * sample;
        }
      }
      const rms = Math.sqrt(sumSquares / frame.data.length);
      const measuredDb = rms > 0 ? 20 * Math.log10(rms) : -Infinity;
      const targetDb = Math.max(-60, Math.min(0, measuredDb));

      // Fast attack, slower release, so the meter tracks speech/clips
      // without jittering on every frame.
      const weight = targetDb > this.currentDb ? 0.5 : 0.15;
      this.currentDb += (targetDb - this.currentDb) * weight;
      this.audioFrame = frame;
      this.hasSignal = true;
    } else {
      // No analyser / nothing playing: never show a made-up level.
      this.audioFrame = null;
      this.hasSignal = false;
      this.currentDb = -60;
    }

    if (this.vuBar) {
      const percentage = this.hasSignal
        ? Math.max(0, Math.min(100, ((this.currentDb + 60) / 60) * 100))
        : 0;
      this.vuBar.style.width = `${percentage}%`;
    }

    if (this.vuText) {
      this.vuText.textContent = this.hasSignal ? `${Math.round(this.currentDb)} dB` : "-- dB";
    }
  }

  draw() {
    if (!this.ctx || !this.canvas) return;
    const { width, height } = this.canvas;
    this.ctx.clearRect(0, 0, width, height);

    const midY = height / 2;
    this.ctx.lineWidth = 2 * (window.devicePixelRatio || 1);
    this.ctx.strokeStyle = this.hasSignal ? "#8b5cf6" : "#64748b";
    this.ctx.beginPath();

    const frame = this.audioFrame;
    if (frame) {
      // Map real analyser samples across the full canvas width.
      const step = Math.max(1, Math.floor(frame.data.length / Math.max(1, width / 2)));
      let first = true;
      for (let x = 0, i = 0; x < width; x += 2, i += step) {
        const value = frame.kind === "time"
          ? (frame.data[i] - 128) / 128
          : frame.data[i] / 255;
        const y = frame.kind === "time"
          ? midY + value * midY * 0.92
          : midY - value * midY * 0.92;
        if (first) {
          this.ctx.moveTo(x, y);
          first = false;
        } else {
          this.ctx.lineTo(x, y);
        }
      }
    } else {
      // Neutral flat line: no audio to visualise.
      this.ctx.moveTo(0, midY);
      this.ctx.lineTo(width, midY);
    }

    this.ctx.stroke();
  }
}
