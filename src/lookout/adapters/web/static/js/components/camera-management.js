/**
 * Shared builder for the per-camera management bar (AC10).
 *
 * Every camera card — Ring, Wyze, generic/RTSP, go2rtc or any future
 * provider — gets an explicit "Remove camera" control with confirmation.
 * An unauthorized native camera (logged out or expired/invalid token)
 * additionally gets an informational "Authentication required" badge and a
 * "Reauthenticate" suggestion.  Neither suggestion is automatic: removal is
 * always the user's explicit choice and is never triggered by logout or a
 * failed poll.
 *
 * This module is imported by LiveGrid so the controls are rendered as part of
 * setCameras()/render(), which guarantees they land on the *real* tiles that
 * the viewer produces (`.camera-grid-tile`) instead of a stale selector.
 */

export const CAMERA_MANAGEMENT_LABELS = {
  remove: "Remove camera",
  reauthenticate: "Reauthenticate",
  authRequired: "Authentication required",
};

function isNativeCamera(cam) {
  return (
    cam.native_provider === "ring" ||
    cam.native_provider === "wyze" ||
    cam.provider_badge === "ring" ||
    cam.provider_badge === "wyze" ||
    cam.camera_type === "ring" ||
    cam.camera_type === "wyze"
  );
}

/**
 * Build the management bar for a single camera card.
 * @param {object} cam camera descriptor
 * @param {object} handlers { onRemoveCamera, onReauthenticateCamera, labels }
 * @returns {HTMLDivElement}
 */
export function buildCameraManagementControls(cam, handlers = {}) {
  const labels = { ...CAMERA_MANAGEMENT_LABELS, ...(handlers.labels || {}) };
  const bar = document.createElement("div");
  bar.className = "camera-management-controls";
  bar.dataset.managementAttached = "1";

  // Informational auth badge + suggestion for unauthorized native cameras.
  const authRequired = isNativeCamera(cam) && cam.auth_required === true;
  if (authRequired) {
    const badge = document.createElement("span");
    badge.className = "auth-required-badge";
    badge.textContent = labels.authRequired;
    bar.appendChild(badge);

    if (typeof handlers.onReauthenticateCamera === "function") {
      const reauth = document.createElement("button");
      reauth.type = "button";
      reauth.className = "camera-reauthenticate-btn";
      reauth.textContent = labels.reauthenticate;
      reauth.addEventListener("click", (e) => {
        e.stopPropagation();
        handlers.onReauthenticateCamera(cam);
      });
      bar.appendChild(reauth);
    }
  }

  // Universal, non-gated explicit removal control (with confirmation in app.js).
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "camera-remove-btn";
  remove.textContent = labels.remove;
  remove.addEventListener("click", (e) => {
    e.stopPropagation();
    if (typeof handlers.onRemoveCamera === "function") {
      handlers.onRemoveCamera(cam);
    }
  });
  bar.appendChild(remove);

  return bar;
}
