/**
 * 24-Hour continuous visual scrubber timeline bar with heatmap density.
 */
import { resolveUrl } from "../utils/url.js";

export class TimelineScrubber {
  constructor({ container, onTimeSelect }) {
    this.container = container;
    this.onTimeSelect = onTimeSelect;
    this.events = [];
    this.bucketCount = 48; // 30-minute buckets across 24h
    this.playheadPosition = 0; // 0 to 1
  }

  setEvents(events) {
    this.events = events || [];
    this.render();
  }

  render() {
    if (!this.container) return;
    this.container.innerHTML = "";

    const scrubberWrap = document.createElement("div");
    scrubberWrap.className = "timeline-scrubber";

    const header = document.createElement("div");
    header.className = "scrubber-header";
    header.innerHTML = `
      <span>24-Hour Continuous Scrubber</span>
      <span>${this.events.length} Events</span>
    `;
    scrubberWrap.appendChild(header);

    const trackContainer = document.createElement("div");
    trackContainer.className = "scrubber-track-container";

    // Calculate 24-hour event density
    const now = new Date();
    const dayAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const buckets = new Array(this.bucketCount).fill(0);

    this.events.forEach((evt) => {
      const evtTime = new Date(evt.timestamp);
      const diff = evtTime.getTime() - dayAgo.getTime();
      const frac = diff / (24 * 60 * 60 * 1000);
      if (frac >= 0 && frac <= 1) {
        const idx = Math.min(this.bucketCount - 1, Math.floor(frac * this.bucketCount));
        buckets[idx]++;
      }
    });

    const maxCount = Math.max(1, ...buckets);

    buckets.forEach((count, i) => {
      const seg = document.createElement("div");
      seg.className = "heat-segment";
      const density = count / maxCount;
      if (count > 0) {
        const alpha = Math.max(0.25, density);
        seg.style.backgroundColor = `rgba(59, 130, 246, ${alpha})`;
      } else {
        seg.style.backgroundColor = "transparent";
      }
      seg.title = `${count} event(s)`;
      trackContainer.appendChild(seg);
    });

    // Playhead line
    const playhead = document.createElement("div");
    playhead.className = "scrubber-playhead";
    playhead.style.left = `${this.playheadPosition * 100}%`;
    trackContainer.appendChild(playhead);

    // Hover tooltip
    const tooltip = document.createElement("div");
    tooltip.className = "scrubber-preview-tooltip";
    scrubberWrap.appendChild(tooltip);

    // Track interactions
    trackContainer.addEventListener("mousemove", (e) => {
      const rect = trackContainer.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      const frac = x / rect.width;

      tooltip.style.display = "flex";
      tooltip.style.left = `${x + rect.left - scrubberWrap.getBoundingClientRect().left}px`;

      const targetTime = new Date(dayAgo.getTime() + frac * 24 * 60 * 60 * 1000);
      const timeStr = targetTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

      // Find closest event
      let closest = null;
      let minDiff = Infinity;
      this.events.forEach((evt) => {
        const d = Math.abs(new Date(evt.timestamp).getTime() - targetTime.getTime());
        if (d < minDiff) {
          minDiff = d;
          closest = evt;
        }
      });

      if (closest) {
        const rawThumb = closest.snapshot_uri || `/media/${closest.camera_id}/${closest.id}.jpg`;
        const thumbUrl = resolveUrl(rawThumb);
        tooltip.innerHTML = `
          <img src="${thumbUrl}" alt="Preview" />
          <span>${closest.summary || "Event"} at ${timeStr}</span>
        `;
      } else {
        tooltip.innerHTML = `<span>${timeStr} (No events)</span>`;
      }
    });

    trackContainer.addEventListener("mouseleave", () => {
      tooltip.style.display = "none";
    });

    trackContainer.addEventListener("click", (e) => {
      const rect = trackContainer.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      const frac = x / rect.width;
      this.playheadPosition = frac;
      playhead.style.left = `${frac * 100}%`;

      const targetTime = new Date(dayAgo.getTime() + frac * 24 * 60 * 60 * 1000);
      if (this.onTimeSelect) {
        this.onTimeSelect(targetTime);
      }
    });

    scrubberWrap.appendChild(trackContainer);
    this.container.appendChild(scrubberWrap);
  }
}
