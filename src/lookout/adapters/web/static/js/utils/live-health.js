/**
 * Shared, dependency-free helpers for bounded live-start retries and the
 * "live unavailable, showing snapshot" fallback UX.
 *
 * Nothing here talks to the network or touches the DOM: both LivePlayer
 * (focus view) and LiveGrid (tile view) use the same attempt limits, backoff
 * delays and user-facing copy so the two views cannot drift apart.
 */

export const LIVE_MAX_ATTEMPTS = 3;
export const LIVE_RETRY_DELAYS_MS = [1000, 2500, 5000];
export const SNAPSHOT_FALLBACK_POLL_MS = 2000;
export const LIVE_MEDIA_READY_TIMEOUT_MS = 8000;

/** True for HTTP 5xx stream/signaling failures (the 502 retry storm source). */
export function isLiveHttpFailure(status) {
  const code = Number(status);
  return Number.isFinite(code) && code >= 500;
}

/** Delay before the next automatic attempt, or null once the budget is spent. */
export function retryDelayForAttempt(failedAttempts) {
  const index = Math.max(0, Number(failedAttempts) || 0);
  if (index >= LIVE_MAX_ATTEMPTS) return null;
  return LIVE_RETRY_DELAYS_MS[Math.min(index, LIVE_RETRY_DELAYS_MS.length - 1)];
}

function providerOf(camera) {
  if (!camera) return "";
  return String(
    camera.provider_badge || camera.native_provider || camera.camera_type || ""
  ).toLowerCase();
}

/** Ring On-Demand tiles need a provider-specific explanation. */
export function isRingOnDemand(camera) {
  return providerOf(camera) === "ring" && (camera?.stream_mode || "on_demand") === "on_demand";
}

/**
 * Short status line plus a provider-aware hint. The literal first sentence is
 * intentionally stable because the UX suite and the audit assert on it.
 */
export function liveUnavailableMessage(camera, reason = "") {
  let hint = "No playable live media was delivered; the latest snapshot is shown instead.";
  if (isRingOnDemand(camera)) {
    hint =
      "Ring On-Demand needs an active Ring plan and a reachable doorbell; showing the latest snapshot.";
  } else if (providerOf(camera) === "wyze") {
    hint = "Wyze live streaming may be starting or unavailable; showing the latest snapshot.";
  }
  const suffix = reason ? ` (${reason})` : "";
  return `Live unavailable — showing snapshot. ${hint}${suffix}`;
}

/** Accessible label/title used on disabled pause & mute controls. */
export function liveUnavailableControlTitle() {
  return "Live video unavailable — showing snapshot. Playback controls are disabled.";
}
