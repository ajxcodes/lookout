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
