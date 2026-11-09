/**
 * URL resolution helper respecting dynamic <base href="..."> for Standalone and Home Assistant Ingress.
 */

export function getBasePath() {
  const baseEl = document.querySelector("base");
  if (baseEl && baseEl.getAttribute("href")) {
    let href = baseEl.getAttribute("href");
    if (!href.endsWith("/")) {
      href += "/";
    }
    return href;
  }
  return "/";
}

export function resolveUrl(path) {
  if (!path) return path;
  // If already absolute URL with protocol or data URI, return as-is
  if (
    path.startsWith("http://") ||
    path.startsWith("https://") ||
    path.startsWith("data:") ||
    path.startsWith("blob:")
  ) {
    return path;
  }

  const basePath = getBasePath();
  const cleanPath = path.startsWith("/") ? path.slice(1) : path;
  return `${basePath}${cleanPath}`;
}

export function resolveWsUrl(path) {
  if (!path) return path;
  if (path.startsWith("ws://") || path.startsWith("wss://")) {
    return path;
  }
  const resolved = resolveUrl(path);
  const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
  if (resolved.startsWith("http://")) {
    return "ws://" + resolved.slice("http://".length);
  }
  if (resolved.startsWith("https://")) {
    return "wss://" + resolved.slice("https://".length);
  }
  const host = window.location.host;
  const clean = resolved.startsWith("/") ? resolved : "/" + resolved;
  return `${wsProto}//${host}${clean}`;
}
