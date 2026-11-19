// Minimal DOM shim + LiveGrid render used by the AC10 DOM-level test.
// Renders camera tiles via LiveGrid.setCameras() and reports the management
// controls that actually landed on each tile. Invoked as:
//   node camera_management_dom_check.mjs <absolute-path-to-static/js>

import { pathToFileURL } from "node:url";

const jsDir = process.argv[2];
if (!jsDir) {
  console.error("usage: node camera_management_dom_check.mjs <js-dir>");
  process.exit(2);
}

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.style = {};
    this._class = "";
    this._text = "";
    this._html = "";
    this.listeners = {};
    this.disabled = false;
    this.value = "";
    this.type = "";
  }
  get className() {
    return this._class;
  }
  set className(v) {
    this._class = String(v ?? "");
  }
  get classList() {
    const self = this;
    const parts = () => new Set(self._class.split(/\s+/).filter(Boolean));
    return {
      add(...c) {
        const s = parts();
        c.forEach((x) => s.add(x));
        self._class = [...s].join(" ");
      },
      remove(...c) {
        const s = parts();
        c.forEach((x) => s.delete(x));
        self._class = [...s].join(" ");
      },
      toggle(c, force) {
        const s = parts();
        const on = force === undefined ? !s.has(c) : Boolean(force);
        on ? s.add(c) : s.delete(c);
        self._class = [...s].join(" ");
        return on;
      },
      contains(c) {
        return parts().has(c);
      },
    };
  }
  get textContent() {
    return this.children.map((c) => c.textContent).join("") + this._text;
  }
  set textContent(v) {
    this.children = [];
    this._text = String(v ?? "");
  }
  get innerHTML() {
    return this._html;
  }
  set innerHTML(v) {
    this.children = [];
    this._text = "";
    this._html = String(v ?? "");
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener(type, fn) {
    const a = this.listeners[type] || [];
    const i = a.indexOf(fn);
    if (i >= 0) a.splice(i, 1);
  }
  dispatch(type) {
    const e = {
      type,
      target: this,
      stopPropagation() {
        e._stopped = true;
      },
      preventDefault() {},
    };
    (this.listeners[type] || []).forEach((fn) => fn(e));
    return e;
  }
  setAttribute(name, val) {
    this[name] = String(val);
  }
  getAttribute(name) {
    return this[name] ?? null;
  }
  matches(sel) {
    if (sel.startsWith(".")) return this.classList.contains(sel.slice(1));
    if (sel.startsWith("[")) {
      const m = sel.match(/^\[data-([a-z-]+)(?:=["']?([^"'\]]+)["']?)?\]$/);
      if (m) {
        const key = m[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        if (!(key in this.dataset)) return false;
        return m[2] === undefined || this.dataset[key] === m[2];
      }
      return false;
    }
    return this.tagName === sel.toUpperCase();
  }
  _walk(out = []) {
    for (const c of this.children) {
      out.push(c);
      c._walk(out);
    }
    return out;
  }
  querySelector(sel) {
    return this._walk().find((e) => e.matches(sel)) || null;
  }
  querySelectorAll(sel) {
    return this._walk().filter((e) => e.matches(sel));
  }
}

const documentShim = {
  hidden: true,
  createElement(tag) {
    return new El(tag);
  },
  querySelector() {
    return null;
  },
  querySelectorAll() {
    return [];
  },
  getElementById() {
    return null;
  },
  addEventListener() {},
  body: new El("body"),
};
globalThis.document = documentShim;
globalThis.window = {
  location: { protocol: "http:", host: "localhost" },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  addEventListener() {},
  document: documentShim,
};
globalThis.localStorage = globalThis.window.localStorage;
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

const { LiveGrid } = await import(
  pathToFileURL(`${jsDir}/components/live-grid.js`).href
);

const container = documentShim.createElement("div");
const calls = { remove: [], reauthenticate: [] };
const grid = new LiveGrid({
  container,
  densitySelector: null,
  onFocusCamera: () => {},
  onRemoveCamera: (cam) => calls.remove.push(cam.id),
  onReauthenticateCamera: (cam) => calls.reauthenticate.push(cam.id),
});

const cameras = [
  {
    id: "native-ring",
    name: "Door",
    native_provider: "ring",
    auth_required: true,
    enabled: true,
    stream_mode: "on_demand",
  },
  {
    id: "native-wyze",
    name: "Office",
    native_provider: "wyze",
    auth_required: false,
    enabled: true,
    stream_mode: "on_demand",
  },
  {
    id: "rtsp",
    name: "RTSP",
    camera_type: "generic",
    stream_url: "rtsp://example/cam",
    enabled: true,
    stream_mode: "on_demand",
  },
  {
    id: "go2",
    name: "Go2",
    camera_type: "go2rtc",
    enabled: true,
    stream_mode: "on_demand",
  },
];
grid.setCameras(cameras);

const tiles = container.querySelectorAll(".camera-grid-tile");
const result = {
  tileCount: tiles.length,
  tiles: tiles.map((tile) => {
    const bar = tile.querySelector(".camera-management-controls");
    const badge = bar ? bar.querySelector(".auth-required-badge") : null;
    return {
      cameraId: tile.dataset.cameraId,
      hasBar: Boolean(bar),
      buttons: bar ? bar.querySelectorAll("button").map((b) => b.textContent) : [],
      hasBadge: Boolean(badge),
      badgeText: badge ? badge.textContent : null,
    };
  }),
  calls,
};

// Exercise the handlers to prove the controls are wired, not just present.
const ringBar = tiles
  .find((t) => t.dataset.cameraId === "native-ring")
  ?.querySelector(".camera-management-controls");
if (ringBar) {
  const reauthBtn = ringBar
    .querySelectorAll("button")
    .find((b) => b.textContent === "Reauthenticate");
  const removeBtn = ringBar
    .querySelectorAll("button")
    .find((b) => b.textContent === "Remove camera");
  if (reauthBtn) reauthBtn.dispatch("click");
  if (removeBtn) removeBtn.dispatch("click");
}
result.callsAfterClick = calls;

console.log("DOM_RESULT=" + JSON.stringify(result));
