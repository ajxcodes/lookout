/**
 * Viewer-side display timezone helpers.
 *
 * When config.display_timezone is set, event/group timestamps are formatted in
 * that IANA zone via Intl.DateTimeFormat; the browser-local rendering is kept
 * as a secondary label / title ("show both"). With no configured zone every
 * helper degrades to the previous browser-local behaviour.
 *
 * Pure module: no DOM access, no network, no new dependencies.
 */

let configuredTimeZone = "";

export function isValidTimeZone(value) {
  if (typeof value !== "string" || !value.trim()) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(new Date(0));
    return true;
  } catch {
    return false;
  }
}

export function setDisplayTimezone(value) {
  const candidate = typeof value === "string" ? value.trim() : "";
  configuredTimeZone = isValidTimeZone(candidate) ? candidate : "";
  return configuredTimeZone;
}

export function getDisplayTimezone() {
  return configuredTimeZone;
}

function withZone(options) {
  const base = options || {};
  return configuredTimeZone ? { ...base, timeZone: configuredTimeZone } : base;
}

/** Primary display: configured zone when set, otherwise browser-local. */
export function formatInDisplayZone(date, options = {}) {
  try {
    return new Intl.DateTimeFormat(undefined, withZone(options)).format(date);
  } catch {
    return formatInBrowserZone(date, options);
  }
}

/** Always browser-local, used for the secondary "show both" label/title. */
export function formatInBrowserZone(date, options = {}) {
  try {
    return new Intl.DateTimeFormat(undefined, options).format(date);
  } catch {
    return "";
  }
}

const PART_FIELDS = ["year", "month", "day", "hour", "minute", "second"];

/** Calendar parts in the configured zone (or browser-local when unset). */
export function zonedParts(date) {
  const options = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  };
  const parts = new Intl.DateTimeFormat(
    "en-US",
    configuredTimeZone ? { ...options, timeZone: configuredTimeZone } : options
  ).formatToParts(date);
  const result = {};
  for (const part of parts) {
    if (PART_FIELDS.includes(part.type)) result[part.type] = part.value;
  }
  return result;
}

export function zonedDayKey(date) {
  const parts = zonedParts(date);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function zonedHour(date) {
  return Number(zonedParts(date).hour);
}

/**
 * Return the primary label plus a browser-local title when a display zone is
 * configured. Consumers add the title only when localTitle is non-empty.
 */
export function eventTimeWithLocalHint(
  isoString,
  options = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return { primary: "", local: "", localTitle: "" };
  const primary = formatInDisplayZone(date, options);
  const local = configuredTimeZone ? formatInBrowserZone(date, options) : "";
  const localTitle = configuredTimeZone
    ? `Display time (${configuredTimeZone}): ${primary} · Browser local time: ${local}`
    : "";
  return { primary, local, localTitle };
}

/** Clock string used by live timestamp overlays (no date component). */
export function formatClock(date = new Date()) {
  const parts = zonedParts(date);
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

/** Secondary browser-local clock for the "show both" tooltip. */
export function formatLocalClockTitle(date = new Date()) {
  if (!configuredTimeZone) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  })
    .formatToParts(date)
    .reduce((acc, part) => {
      if (PART_FIELDS.includes(part.type)) acc[part.type] = part.value;
      return acc;
    }, {});
  return `Browser local time: ${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
