/**
 * AI inference provenance and execution latency telemetry component.
 */
export class TelemetryRenderer {
  static getProvenanceBadge(provenanceList) {
    if (!provenanceList || provenanceList.length === 0) {
      return '<span class="provenance-chip tier3">Tier 3 (Offline)</span>';
    }

    const latest = provenanceList[provenanceList.length - 1];
    const provider = (latest.provider || "").toLowerCase();

    if (provider.includes("ollama") || provider.includes("local")) {
      return `<span class="provenance-chip ollama">Local (${latest.model_name || "moondream"})</span>`;
    }
    if (provider.includes("opencode")) {
      return `<span class="provenance-chip opencode">OpenCode (${latest.model_name || "deepseek"})</span>`;
    }
    if (provider.includes("gemini")) {
      return `<span class="provenance-chip gemini">Gemini (${latest.model_name || "gemini-flash"})</span>`;
    }

    return `<span class="provenance-chip tier3">${latest.model_name || "Tier 3"}</span>`;
  }

  static getLatencyChips(eventItem) {
    const metrics = eventItem.metrics || {};
    const provenance = (eventItem.provenance && eventItem.provenance[0]) || {};

    const totalMs = Math.round(metrics.total_latency_ms || provenance.latency_ms || 0);
    const chips = [];

    if (totalMs > 0) {
      chips.push(`<span class="latency-chip">${totalMs}ms total</span>`);
    }

    if (provenance.latency_ms) {
      chips.push(`<span class="latency-chip">${Math.round(provenance.latency_ms)}ms ai</span>`);
    }

    return chips.join(" ");
  }
}
