/**
 * Chronological timeline cards component.
 */
import { TelemetryRenderer } from "./telemetry.js";
import { resolveUrl } from "../utils/url.js";

export class TimelineList {
  constructor({ container, onSelectEvent, onDeleteEvent }) {
    this.container = container;
    this.onSelectEvent = onSelectEvent;
    this.onDeleteEvent = onDeleteEvent;
    this.events = [];
    this.selectedEventId = null;
  }

  setEvents(events) {
    this.events = events;
    this.render();
  }

  addEvent(eventItem) {
    // Prepend new event to timeline
    this.events.unshift(eventItem);
    this.render();
  }

  removeEvent(eventId) {
    this.events = this.events.filter((e) => e.id !== eventId);
    this.render();
  }

  setSelectedEvent(eventId) {
    this.selectedEventId = eventId;
    this.render();
  }

  formatRelativeTime(isoString) {
    try {
      const date = new Date(isoString);
      const now = new Date();
      const diffSec = Math.floor((now - date) / 1000);

      if (diffSec < 60) return "just now";
      if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
      if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch {
      return "";
    }
  }

  getCategoryPill(category) {
    const cat = (category || "").toLowerCase();
    let pillClass = "unknown";

    if (cat.includes("resident")) pillClass = "resident";
    else if (cat.includes("courier")) pillClass = "courier";
    else if (cat.includes("food")) pillClass = "food";
    else if (cat.includes("pet_elimination")) pillClass = "pet_elimination";
    else if (cat.includes("pet")) pillClass = "pet";
    else if (cat.includes("intrusion") || cat.includes("threat")) pillClass = "threat";
    else if (cat.includes("acoustic") || cat.includes("audio")) pillClass = "audio";

    const display = cat.replace(/_/g, " ");
    return `<span class="cat-pill ${pillClass}">${display}</span>`;
  }

  getEntityBadges(evt) {
    const badges = [];
    if (evt.is_pet_elimination || evt.category === "pet_elimination") {
      const petName =
        (evt.matched_entities &&
          evt.matched_entities.find((e) => !e.toLowerCase().includes("civic"))) ||
        "Mochi";
      badges.push(`<span class="entity-badge elimination">🐶 ${petName}: Pooping</span>`);
    }

    if (evt.matched_entities && Array.isArray(evt.matched_entities)) {
      evt.matched_entities.forEach((m) => {
        const lower = m.toLowerCase();
        if (
          lower.includes("civic") ||
          lower.includes("honda") ||
          lower.includes("car") ||
          lower.includes("vehicle")
        ) {
          badges.push(`<span class="entity-badge vehicle">🚗 ${m}</span>`);
        } else if (lower.includes("mochi") || lower.includes("dog") || lower.includes("cat")) {
          if (!evt.is_pet_elimination && evt.category !== "pet_elimination") {
            badges.push(`<span class="entity-badge pet">🐶 ${m}</span>`);
          }
        } else {
          badges.push(`<span class="entity-badge resident">👤 ${m}</span>`);
        }
      });
    }

    if (badges.length === 0) return "";
    return `<div class="entity-badges-wrap">${badges.join("")}</div>`;
  }

  getGroupBucket(isoString) {
    try {
      const date = new Date(isoString);
      const now = new Date();
      const isToday = date.toDateString() === now.toDateString();

      const yesterday = new Date(now);
      yesterday.setDate(now.getDate() - 1);
      const isYesterday = date.toDateString() === yesterday.toDateString();

      const hour = date.getHours();
      let partOfDay = "Morning";
      if (hour >= 12 && hour < 17) partOfDay = "Afternoon";
      else if (hour >= 17 && hour < 21) partOfDay = "Evening";
      else if (hour >= 21 || hour < 5) partOfDay = "Night";

      if (isToday) return `Today - ${partOfDay}`;
      if (isYesterday) return `Yesterday - ${partOfDay}`;
      return `${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })} - ${partOfDay}`;
    } catch {
      return "Earlier";
    }
  }

  render() {
    if (!this.container) return;
    this.container.innerHTML = "";

    if (this.events.length === 0) {
      const emptyCard = document.createElement("div");
      emptyCard.className = "text-muted";
      emptyCard.style.padding = "var(--space-lg)";
      emptyCard.style.textAlign = "center";
      emptyCard.style.fontSize = "0.875rem";
      emptyCard.textContent = "No events match current filter";
      this.container.appendChild(emptyCard);
      return;
    }

    let currentGroup = null;

    this.events.forEach((evt) => {
      const groupName = this.getGroupBucket(evt.timestamp);
      if (groupName !== currentGroup) {
        currentGroup = groupName;
        const groupHeader = document.createElement("div");
        groupHeader.className = "timeline-group-header";
        groupHeader.textContent = currentGroup;
        this.container.appendChild(groupHeader);
      }

      const card = document.createElement("div");
      card.className = `event-card ${evt.id === this.selectedEventId ? "selected" : ""}`;
      card.dataset.eventId = evt.id;

      const rawThumb = evt.snapshot_uri || `/media/${evt.camera_id}/${evt.id}.jpg`;
      const thumbUrl = resolveUrl(rawThumb);
      const placeholderUrl = resolveUrl("/static/placeholder.jpg");
      const provBadge = TelemetryRenderer.getProvenanceBadge(evt.provenance);
      const latencyChips = TelemetryRenderer.getLatencyChips(evt);
      const entityBadges = this.getEntityBadges(evt);

      card.innerHTML = `
        <img class="event-thumb" src="${thumbUrl}" alt="Event poster" onerror="this.src='${placeholderUrl}'" />
        <div class="event-info">
          <div class="event-header-row">
            ${this.getCategoryPill(evt.category)}
            <span class="event-time">${this.formatRelativeTime(evt.timestamp)}</span>
          </div>
          <div class="event-summary" title="${evt.summary || ""}">${evt.summary || "Motion detected"}</div>
          ${entityBadges}
          <div class="telemetry-row">
            ${provBadge}
            ${latencyChips}
          </div>
        </div>
        <button class="btn-icon-delete" title="Delete event">✕</button>
      `;

      card.addEventListener("click", (e) => {
        if (e.target.closest(".btn-icon-delete")) return;
        this.selectedEventId = evt.id;
        this.render();
        if (this.onSelectEvent) {
          this.onSelectEvent(evt);
        }
      });

      const delBtn = card.querySelector(".btn-icon-delete");
      if (delBtn) {
        delBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (this.onDeleteEvent) {
            this.onDeleteEvent(evt.id);
          }
        });
      }

      this.container.appendChild(card);
    });
  }
}
