# Wyze app → Lookout: Protocol Notes (SD playback, PTZ, Speak, Photo, PanScan, Motion Tagging, Auth, Notifications)

**Date:** 2026-11-19 (updated same day with §8a PTZ, §8b Speak, §8c Photo, §8d PanScan, §8e Motion Tagging)
**Source:** Wyze app `com.hualai` v3.25.1.1037 (APKM, arm64-v8a), static analysis with androguard (DEX disassembly; no implementation code copied).
**Purpose:** Understand how the official app implements these features against Wyze cameras/cloud, to evaluate Lookout integration.
**Status:** Investigation notes. Not legal advice. Artifacts live only in `/tmp/opencode/wyze/` and are disposable; the APKM and this doc are not in the repo.

> **Legal posture.** Independent interoperability research for personal, self-hosted use. Only protocol facts (endpoint paths, command names, JSON shapes, bit layouts) are recorded. Wyze ToS likely prohibit this kind of analysis; scope is documentation, not circumvention.

---

## 1. Executive summary

- All five feature families split into two protocol "flavors" plus one cloud REST family:
  - **Hualai flavor** — legacy/current camera-sdk JSON commands (`playback_start`, `speech_control`, `get_sdcard_info`, `pan_tilt_*` / binary `11009` push).
  - **IoT capability flavor** — `get_property` / `set_property` / `run_action` envelopes with namespaced keys (`camera-position::…`, `camera-pnt::…`, `sd-card::…`, `motion-detection::…`), used by newer plugins.
  - **Cloud REST** — `app.wyzecam.com` ("wcsa" + device-management APIs) for connection params and for IoT-capability property writes that do **not** go to the camera at all.
- SD media never leaves the camera except through Agora RTC / RDT (V3 class) or gwell/Tencent IoT Video over TUTK (legacy class).
- Snapshots ("take photo") involve **no camera command at all** — the client captures the currently decoded frame locally.
- PTZ, wake/position, and PanScan on modern cameras are `run_action` / `set_property` calls on the `camera-position::` and `camera-pnt::` namespaces over the same RTC data channel used for live view.
- Motion tagging is a **boolean IoT property** `motion-detection::motion-tag`. On the newest plugin generation it is written via the **cloud** device-management API (`/device-management/api/action/run_action`), not the camera channel; on Cam v3 it goes through the HLApi `ConnectControl.setCameraProperty` path with an obfuscated boolean property class.

---

## 2. Analysis environment

- APKM → `base.apk` (35 DEX files, 149,084 classes) + `split_config.arm64_v8a.apk` (native libs).
- Tooling: Python 3.12 + androguard 3.x; targeted disassembly of ~40 classes; raw strings inventory across all DEX files.
- Native stacks present: Agora RTC + RDT (`libagora-rtc-sdk.so`, `libRDTAPIs.so`), Gwell/Tencent IoT Video (`libiotvideo.so`, `libgwplayer.so`, `libAVAPIs.so`), TUTK (`libIOTCAPIs.so`), FFmpeg.
- Repo untouched. Raw dumps: `dump_*.txt`, `all_classes.txt`, scripts in `/tmp/opencode/wyze/`.

---

## 3. Cloud handshake ("wcsa" API)

Base: `https://app.wyzecam.com` (Prod; Test/Beta variants use `test-api.wyzecam.com` / `beta-app.wyzecam.com`).
All calls: dynamic signature (`WpkWyzeExService.isDynamicSignature(true)`, app key constant `9319141212m2ik` in the APK) + `Authorization: <access_token>`; Wyze account auth as usual (`wyze-sdk` covers this today).

| Endpoint | Body | Returns |
|---|---|---|
| `POST /app/v4/camera/get_streams` | `{"device_list":[{device_id, device_model, provider, …custom}]}` | batch stream info |
| `POST /app/v4/wcsa/create-connection` | `{"device_id", "device_model", "uid": <int rtc_client_uid>}` | `CreateConnectModel`: `{code, msg, ts, data:{app_id, channel, device_uid, uid, rtc_token, encryption_key, encryption_salt, encryption_mode, p2p_mode, protocol}}` |
| `POST /app/v4/wcsa/renew-token` | same | renewed `rtc_token` |
| `POST /app/v4/wcsa/start-playback` | `{"device_id", "device_model"}` | playback session params (SD entry) |
| `POST /app/v4/device/wakeup` | `{"device_id","device_model","params":{"mode":<int>,"user_id":<str>,"rtc_client_uid":<int>}}` | wake token (battery cams) |

`uid` sent by the app is a **random persistent int** cached per device (`WpkDeviceScopedCache`, key `"agora"/"rtc_uid"`), not an account field.

These calls are the entry point for every V3-class camera interaction (live, SD playback, PTZ, speak). They are **not** in `wyze-sdk`; see §9.

---

## 4. SD-card playback control (V3-class)

### 4.1 Transport

Commands ride the Agora RTC **data-stream** channel (`RtcEngineEx.sendStreamMessageEx`, replies via the RTC stream-message event). While the RDT channel is open, critical commands (`sd-card::*`, PTZ) are duplicated via `sendRdtMessageEx`. JSON payload, UTF-8.

### 4.2 Message flavors

**Hualai** — `command` + typed params, e.g.:

| Request | Key params | Response |
|---|---|---|
| `get_sdcard_info` | `rdt_replay: bool, single_channel: bool, speed_control: bool` | `status`, `total_capacity`, `free_capacity`, `rdt_replay`, `single_channel`, `speed_control` |
| `get_playback_info` | `start_time`, `end_time` (epoch **seconds int**) | `recordings[]` |
| `get_playback_info_by_minute` | `start_time`, `duration: 60` | `start_time`, `bit_mask` (base64) |
| `playback_start` | `start_time`, `play_speed`, `rdt_replay` | `playback_start_ack`; push: `NotifyPlaybackState` (`state`, `message_type`, `message`) |
| `playback_stop` | — | `playback_stop_ack` |
| `playback_speed` | `play_speed` | `playback_speed_ack` |
| `playback_control` | (pause/resume) | `playback_control_ack` |
| `get_sdcard_info` ack fields | — | `NotifyPlaybackState`-style errors surfaced to UI |

**IoT capability** — `run_action`/`get_property`/`set_property` envelope: `{"cmd":…, "tid":int, "ts":epoch_ms, …}`.
Observed action names: `sd-card::query-playback-info`, `sd-card::playback-start`, `sd-card::playback-start-rdt`, `sd-card::playback-end`, `sd-card::playback-method`, `sd-card::playback-speed`, `sd-card::playback-speed-dynamic`, `sd-card::sd-card-status`, `sd-card::total-capacity`, `sd-card::free-capacity`, `sd-card::not-enough-space`.

### 4.3 Minute bitmap (timeline)

`bit_mask` → `Base64Decode` → bytes; bits read **LSB-first** (`bit i` of byte k = logical minute i of 60); slot truthy ⇒ recording exists at `start_time + i*60`; entries materialize `[t*1000, t*1000+60000) ms` intervals for the UI list. Timestamps are 32-bit epoch seconds. 60 slots per call; app pages windows to cover a day.

### 4.4 Media plane

- **RDT** (modern, gated by client flag `camera_sdk_enable_rdt_playback` + device `rdt_replay`): camera opens an RDT channel (config keys `RDT_ENABLE`, `RDT_CRYPTO_ENABLE`, `RDT_AUTO_CONNECT`); payloads are fragmented MPEG-TS decoded by `TsStreamPlayer` / `RdtTsDataSource` (`libwyzeplayer.so`); dedicated playback uid (`PLAYBACK_CHANNEL_UID`); playout delay knob (`PLAYBACK_PLAYOUT_DELAY`); minute granularity `* 1000 ms`; entries spaced by `+60000 ms`.
- **RTC fallback**: recorded stream published in the normal Agora channel (playback uid), decoded by the same TS/H264 pipeline as live.
- `single_channel`: some SD streams mux A+V on one RTC stream (vs live's split publish).

### 4.5 Legacy gwell/Tencent IoT Video path (Pan v1/v2, v2-class)

P2P pass-through binary commands (`INNER_PASS_THROUGH_CMD_PLAYBACK_GET_LIST[_NEW]`, `…_EXISTED_DATE_LIST`, `…_PLAY_DELETE_FILES`; `INNER_USER_DATA_CMD_PLAYBACK_SEEK/RESUME/PAUSE/DOUBLE_SPEED/FINISHED/START_TIME/DOWNLOAD_FILE_*`).
`PlaybackPlayer.getPlaybackListV2(startMs, endMs, page, pageSize=42, …)`, `getExistRecordDateList`; messages are versioned, paged (`currentPage/pageCount/isLastPage`), ≥17-byte records with `startTime/endTime/recordType` + `baseTime`. Media over TUTK SRTP-ish (see `TUTKAVModel`); snapshot/thumb downloads ride `PLAYBACK_PLAY_FILE_THUMB_DOWNLOAD`.

---

## 5. PTZ (Pan-Tilt-Zoom) — §8a

### 5.1 Transport

Same RTC data-stream (`sendStreamMessageEx`) / RDT duplication as SD playback. For legacy cams: gwell/Tencent P2P path.

### 5.2 IoT capability flavor (Cam v3 / Pan v3 / v4-class)

**Namespace `camera-position::`** — request as `run_action` (params map) unless noted:

| Command / property | Params | Notes |
|---|---|---|
| `camera-position::move-position` | `direction` = lowercase enum name (`up`\|`down`\|`left`\|`right`), `step` = `1000` (fixed) | relative move; duration ≈ held command |
| `camera-position::move-position-ex` | `dx`: int, `dy`: int | gesture/drag-style move |
| `camera-position::set-position` | `horizontal`: int, `vertical`: int | absolute position |
| `camera-position::get-position` | (none) | response `params: {horizontal: int, vertical: int}` |
| `camera-position::stop-move-position` | (none) | stop |
| `camera-position::reset-position` | (none) | home position |
| `camera-position::goto-waypoint` | `home`: bool, `index`: int | jump to preset (or home) |
| `camera-position::edit-waypoint` | key = waypoint index… | `set_property`, e.g. save/clear presets |
| `camera-position::moving-speed` | (property, get/set) | pan/tilt speed |
| `camera-position::position-warning`, `camera-position::position-alignment` | (get/set properties) | limit warnings / alignment offset |

Mapping from UI enum: `PanTiltCameraProperty.command ∈ {UP, RIGHT, DOWN, LEFT, STOP, UNKNOWN}` → `move-position` with direction = enum name lowercased, `step=1000`; `STOP` → `camera-position::stop-move-position`.
`PanTiltByDegreesCameraProperty { command, horizontalDegrees, verticalDegrees }` → same channel (move-by-degrees).

**Namespace `camera-pnt::`** (Pan-specific):

| Property | Meaning |
|---|---|
| `camera-pnt::scan` | Pan Scan (see §8d) — bool ON/OFF via get/set property |
| `camera-pnt::scan-nodes` | Pan-scan waypoint node list (`set_property`) |
| `camera-pnt::tracking`, `camera-pnt::tracking-ai`, `camera-pnt::tracking-setting` | Smart motion/person tracking enable + settings |
| `camera-pnt::schedule`, `camera-pnt::schedule-enable`, `camera-pnt::repeat-interval` | Tracking schedule/interval |

**Push from camera:** `pan_tilt_end` — IoT flavor `ResponsePanTiltEnd` (`PanPosition`/`TiltPosition` enums incl. `LEFT_END_REACHED`, `RIGHT_END_REACHED`, `CENTER_REACHED`, `UNKNOWN`, and `ResetResult`), and hualai binary command **11009** (2-byte payload: `[pan, tilt]` end status; validated "Size of 11009 message should be 2"). This is the unsolicited "motor hit a limit" notification.

### 5.3 Hualai flavor

PTZ property serializers (`PanTiltIotCameraPropertySerializer` / `PanTiltByDegrees…`) resolve to the same IoT envelope on modern firmware; on legacy HL stack, `ConnectControl.setCameraProperty(PanTiltCameraProperty)` is used (same property classes; wire mapping inside HLApi native code).

### 5.4 Legacy gwell/TUTK PTZ

`DuoPtzCmd2Bean { cmd, stop }` (Duo/outdoor cams); PTZ runs through the gwell command channel over P2P — cmd ids not resolved (obfuscated constants). Wyze's own PTZ-over-P2P approach is what `docker-wyze-bridge`'s `ptz` support does via TUTK on v2/Pan class cams.

---

## 6. Speak / Talk (two-way audio) — §8b

**Modern (Agora, V3-class):**

1. App sets `SpeechControlCameraProperty` (Hualai flavor) — `speech_control` command, params `{uid: <int rtc_client_uid>, status: "ENABLED"|"DISABLED"}`; response `{result}`; this asks the camera to open its speaker path and accept the user's RTC audio track.
2. App switches RTC audio profile to **voice communication** (`setCameraAudioUseVoiceCommunication(true)` on all video views), unmutes local mic (`muteLocalAudioStreamEx(false)`), and publishes local audio into the same channel.
3. On talk release/failure: `speech_control` with `status: DISABLED`. UI states surface `SPEAKER_CONTROL_STATE` events with `SpeakerControlCameraProperty` status — `NO_EXISTENCE`, `OCCUPIED`, `SUCCESS`, `STOP`, `FAILED`, `UNKNOWN` (= speaker busy / absent / failure reporting).
4. `startAudioListen()` is a separate always-on flag for listening to camera mic (muted unmute in reverse).

**Legacy (HLApi / Hualai):** separate native "talking channel" opened/closed by `HualaiNetworkThread.openSpeakerConnectionAsync/closeSpeakerConnectionAsync` — native-managed RTP-ish audio path through HLApi (`Loo5` session, channel state `OPENING→OPENED→CLOSING/CLOSED`, codec negotiated natively; exact codec not resolvable statically). Logged as "Talking channel …".

Wire summary: **speak = `speech_control` ENABLED + publish mic on the existing Agora channel**; no separate REST call, no separate stream URL.

---

## 7. Take photo (snapshot) — §8c

**No camera command is involved.** The live-view "Snapshot" (and waypoint's `saveWaypointSnapshotLocally`, Apollo, etc.) captures the currently decoded frame from the video surface/`Bitmap`:

- `CameraSurfaceView` → `onSnapshotCompleted(Bitmap, ts)` → `CacheUtils.saveSnapshotFromSurfaceToCache(...)` → JPEG/PNG write to cache file (atomic `.tmp` → replace; backs up old file first), with sanity checks (`visibleRatio`, average luma — skips "mostly black" frames).
- `LiveCameraLayout.makeSnapshot(...)` → `SnapshotCallback` → menu/UI action. Same mechanism behind waypoint thumbnails.
- Legacy gwell path identical in spirit (`GmLiveManager.snapShot()` → `GmMediaHelper` file save of current frame).
- `WyzeMediaSnapShotUtils.getSnapshotFromSurfaces(...)` for event-player frames.

**Integration implication:** in Lookout this is already possible via the go2rtc stream → frame grab (e.g., `go2rtc api` snapshot or ffmpeg), with zero protocol work. Native Wyze app snapshot = decoded-frame capture, nothing more.

---

## 8. Pan Scan — §8d

Wyze Pan Scan = camera automatically & continuously pans across the set/visible span (a "patrol" using the full pan range, complementing waypoint cruise).

Implementation (modern cameras):

- `PanScanCameraProperty` with `PanScanStatus ∈ {ON, OFF, UNKNOWN}` — IoT property **`camera-pnt::scan`** (bool).
  - Get: `get_property ["camera-pnt::scan"]`; Set: `set_property {"camera-pnt::scan": <bool>}`.
- Related: `camera-pnt::scan-nodes` (`set_property`, string/list of scan node indices — the scan span definition, set from waypoint UI), `camera-pnt::repeat-interval`.
- Coasting patrol of presets: `CruisePosition` (index, horAngle, verAngle, waitTimeInSecond, `Cruise11011` message family, hualai property id ~11011) + `RequestSetWaypointCruise` (`set_property` `camera-pnt::scan-nodes`).
- UI hook points: `PanScanMenuItem` (live menu toggle), `LiveCameraLayout.panScanObserver`; disabled when smart tracking owns the motor (`smart_track_and_pan_scan_have_been_disabled` strings; `camera-pnt::tracking` precedence).
- Not present on fixed cameras — capability-gated.

---

## 9. Motion tagging — §8e

Wyze "Motion Tagging" = on-device AI draws a box/tracks around detected moving person(s) in the live view/events ("motion tags").

### 9.1 Property model

- **IoT property: `motion-detection::motion-tag`** — boolean (`true` = on). Sibling props in the same capability: `motion-detection::motion-zone`, `motion-detection::sensitivity-motion`, `motion-detection::camplus`, `motion-detection::area-*`, `motion-detection::pir-wakeup`, `motion-detection::radar-wakeup`, `motion-detection::standby_enable/mode`, `motion-detection::motion-warning-*`.
- Device-capability (cloud settings model): `MotionTag` data class — `{ id: "motion-tag", propertyCapability: <capability ref>, value: Boolean }`; exposed via `CapabilityValueResponse$DeviceProperties$IMotionTag` (device settings capability, read path for the settings screen).

### 9.2 Write paths (two, model-dependent)

**A. Newest plugin generation (Ascent / Cam4 / BulbCam family)** — **cloud REST**, camera not on the path:

```
POST <devicemgmt_service_wyze>/device-management/api/action/run_action
Authorization: <access_token>   (+ dynamic signature)
Content-Type: application/json
Body (WpkIotRequest):
{
  "targetInfo": {"id": <deviceId>, "productModel": <deviceModel>, "type": "DEVICE"},
  "userId": <user_id>,
  "capabilities": [
    {
      "name": "motion-detection",
      "iid": <capability iid from device capability map>,   // looked up via iotIidMaps, may be null
      "properties": [
        {"separator": "::", "prop": "motion-detection::motion-tag", "value": true|false}
      ]
    }
  ]
}
```
(via `WpkIotCapabilityCloud.runAction(url)` → `ActionRequestBuilder.setPropsWithName("::", "motion-detection::motion-tag", bool)`; read via `get_iot_prop` with `/device-management/api/device-property/get_iot_prop`, same `capabilities/targetInfo/userId` envelope.)

**B. Cam v3 native settings (Hualai/HL stack)** — camera data-channel:

- `NewDetectionSettingsViewModel.setMotionTaggingEnableTask` builds `new Lpnk` (obfuscated 1-bool class `a: Boolean`) → `ConnectControl.setCameraProperty(prop, callback)`; UI toggles `CameraInfo.showTrackingBorder`. Property→wire-id mapping lives in obfuscated HLApi serializer code (not resolvable statically).

#### 9.2.1 Resolved HL contract (phase 11 addendum)

- **Property / bridge topic:** `motion_tagging`.
- **Underlying Hualai/TUTK commands:** read `K10290GetMotionTagging`, write `K10292SetMotionTagging`; device parameter code `21` (`motion_tagging`).
- **Boolean encoding:** `1` = on, `2` = off. Decoding accepts the tolerant set `{1, "1", "on", "true", True}` → on and `{2, "2", "off", "false", False}` → off; anything else is "not readable" and degrades to `supported: false` rather than a guessed boolean.
- **Bridge read:** `GET {base}/api/{bridge_cam}/motion_tagging` → JSON. **Bridge write:** `POST {base}/api/{bridge_cam}/motion_tagging` with form body `value=1|2`.
- **Status mapping:** HTTP `404`/`501` (the bridge does not know the camera or topic) maps to "unsupported"; any other HTTP error, timeout, or connection failure is a transport failure (`502`).
- **Layering:** the bridge is the TUTK control transport boundary. Lookout owns no native TUTK control channel and does not implement `ConnectControl.setCameraProperty` itself; `HlCameraPropertyPort` is the seam a future native transport can implement without a domain or route change.
- **Default model split:** cloud device-management lane `HL_CAM4`; HL camera-property lane `WYZE_CAKP2JFUS` (Cam v3) and `HL_PAN3` (Pan v3). `WYZECP1_JEF` (original Pan) exposes neither path and stays unsupported/hidden.

### 9.3 Read paths

- Via the same cloud `get_iot_prop` call (capability name list `["motion-tag"]`-family) for the settings screen (`motionTaggingStatusTask`).
- Via the camera property subscription for live rendering (show/hide tracking border in the live view).

### 9.4 Notes

- The `MotionTag`/`Speaker`/… entries under `network/…/devicemanagement/data/` are the **device-management capability model** shared with `/device-management/api/device-property/get_iot_prop` — the same API family Lookout would need for a cloud-driven settings toggle, and it does not require the Agora channel at all.
- `CamV3` path A vs B distinction likely maps to HL vs non-HL models via `BindableDevice.isIOTSpace3Dev(deviceModel)`-style model gating (`isIOTSpace3Dev` guards the IoT path globally — non-IOT-Space-3 devices fall back to callback with an error).

---

## 10. What Lookout can use today vs. what blocks it

| Feature | Transport Lookout needs | Feasibility |
|---|---|---|
| Snapshot/photo | none (go2rtc frame grab or ffmpeg) | **Trivial — do this** |
| PTZ (V3-class) | wcsa sign + Agora data-channel `camera-position::*` | Hard (§11.1); partial via existing MQTT/P2P path for legacy cams |
| PTZ (legacy Pan) | TUTK pass-through/`DuoPtzCmd2Bean`-class path | Medium; overlaps with existing wyze-bridge-style PTZ |
| SD playback | wcsa + Agora RTC/RDT or gwell P2P | Hard (§11) |
| Speak | wcsa + `speech_control` + publish mic on Agora channel | Hard; requires native Agora SDK in the media path |
| PanScan | wcsa + `camera-pnt::scan` set_property on data channel (V3-class) | Same blocker as PTZ |
| Motion tagging | **Cloud REST only** (`/device-management/api/action/run_action` + `get_iot_prop`) for ascent-class; HL data channel for cam v3 | **Most promising of the new set** — needs the dynamic-signature replication, no media path |

Common blocker for everything cloud-wcsa: replicating Wyze's **dynamic signature** (`WpkWyzeExService`/`WpkSignature2Service`, app key `9319141212m2ik` in APK) outside the app. Note that existing Wyze integrations (go2rtc included) authenticate differently; this specific signing is what gates §10 rows 2, 3, 4, 5, 6, 7-B.

## 11. Integration verdicts

1. **wcsa signing** is the single gating task for all camera-control-over-cloud work. Not in `wyze-sdk`. Approach: capture app↔cloud traffic once on-device (with the user's account) to derive the canonical string/HMAC, then reproduce. Bounded but fiddly; Wyze may change it anytime.
2. **Media paths** (SD playback, speak): effectively require Agora RTC (+RDT) or gwell P2P. For Lookout (Linux server), the only practical route is go2rtc-style TUTK for legacy cams + a native Agora SDK bridge — large. Recommendation: **do not** build SD playback/speak from scratch.
3. **Motion tagging (cloud twin)**: worth a spike — pure REST once signing is solved, and `MotionTag` capability read/write gives Lookout a real, low-risk win on newer firmware. For cam v3 it would still need HL-channel work (skip initially).
4. Snapshot: free.

**Suggested order if integration is green-lit:** (1) snapshot (done via ffmpeg/go2rtc), (2) wcsa signing spike, (3) motion-tag cloud toggle behind a flag (newer models only), (4) reassess PTZ/PanScan via data channel, (5) SD playback last.

---

## 12. Verification checklist (on-device)

- [ ] `adb logcat` during: PTZ drag on Cam v3 (expect `camera-position::move-position` in data-channel traces), speak (expect `speech_control` + `muteLocalAudioStream` events), PanScan toggle (expect `camera-pnt::scan`), motion-tag toggle (expect `motion-detection::motion-tag` via `run_action`), snapshot (expect **no** camera traffic).
- [ ] Confirm per model which PTZ flavor (HL `setCameraProperty` vs IoT `run_action`) on Cam v3 vs Pan v3.
- [ ] Record one `run_action` request body verbatim (headers + JSON) for the motion-tag write; confirm `iid` presence and capability map source.
- [ ] Confirm `get_iot_prop` response shape for `motion-tag`.
- [ ] For SD playback: confirm `playback_start` carries `rdt_replay` and RDT actually opens (`RDT_STATE_OPENED`) vs RTC fallback.
- [ ] Confirm `11009` binary pushes on hitting PTZ limits.
- [ ] Re-derive `camera_sdk_enable_rdt_playback` remote-config default for current release.

---

## 13. Appendix — key observed constants (facts, for cross-checking)

- Agora `ConnectModel` fields: `app_id, channel, device_uid, uid, rtc_token, encryption_key, encryption_salt, encryption_mode, p2p_mode, protocol`.
- wcsa endpoints: `/app/v4/wcsa/create-connection`, `/app/v4/wcsa/renew-token`, `/app/v4/wcsa/start-playback`, `/app/v4/camera/get_streams`, `/app/v4/device/wakeup`, app key `9319141212m2ik`.
- Device-management REST: `/device-management/api/action/run_action`, `/device-management/api/device-property/get_iot_prop`.
- Message namespaces: `sd-card::*`, `camera-position::*`, `camera-pnt::*`, `motion-detection::*`.
- Commands (hualai): `playback_start/stop/speed/control`, `get_playback_info[_by_minute]`, `get_sdcard_info`, `speech_control`, `playback_start_ack`…; binary: `11009` (pan-tilt end), `Cruise11011` (cruise), `10150` (replay).
- IDs/flags: `camera_sdk_enable_rdt_playback`; `SP_IS_SUPPORT_RDT_PLAYBACK-<id>`, `SP_IS_SUPPORT_SINGLE_CHANNEL-<id>`; `PLAYBACK_CHANNEL_UID`, `RDT_ENABLE`, `RDT_CRYPTO_ENABLE`, `RDT_AUTO_CONNECT`, `RDT_STATE_PENDING/OPENED/BROKEN`; `PLAYBACK_PLAYOUT_DELAY`.
- Enum ordinals: `PanTiltCameraProperty.PanTiltCommand UP=? RIGHT=1 DOWN=3 STOP=4 UNKNOWN=5` (UP=0/LEFT? — verify by capture); `PanScanStatus ON/OFF/UNKNOWN`; `SpeakerControlCameraProperty.SpeakerStatus NO_EXISTENCE/OCCUPIED/SUCCESS/STOP/FAILED/UNKNOWN`; `ResponsePanTiltEnd.PanPosition LEFT_END_REACHED/RIGHT_END_REACHED/CENTER_REACHED/UNKNOWN(=-1)`.

---

## 14a. Auth (Wyze cloud) — follow-up pass

**Base URLs:** prod `https://app.wyzecam.com`; test `https://test-api.wyzecam.com`; beta `https://beta-app.wyzecam.com`. Device-management host (separate): `https://devicemgmt-service.wyze.com` (prod) / `https://devicemgmt-service-beta.wyze.com` (beta).

**Auth headers observed:** `Authorization` (access token), `Wyze-Authorization` (legacy), `appid` (signature2), `Access-Token`, `Accept-Language`, `User-Agent`, `appversion`. Tokens persisted by Lookout's native adapter as `WyzeConfig.auth_token` / `refresh_token` (see `_persist_wyze_tokens`).

**Token lifecycle (Ktor shared module):**
- Access token + refresh token pair. On `401` / error code `2001` (access-token expired), the Ktor interceptor (`KtorDiModuleInterceptUtilKt.handle2001OrAccessTokenError`) calls `POST /app/user/refresh_token` and retries the original request; `RefreshTokenRepositoryViewModel` surfaces invalid-access/invalid-refresh-token errors to the UI.
- `WyzeKtorAuth` / `WyzeKtorSignature` generate `signature2` using an `appid` header — this is the *newer* signing scheme (coexists with the legacy `WpkWyzeExService.isDynamicSignature(true)` + app key `9319141212m2ik` used by wcsa/device-management and the rest of the REST surface).
- Lookout's native data plane is token-first (`wyze_sdk.Client(token=…, refresh_token=…)`); the login handshake itself is owned by Lookout (see below) and never collects a TOTP shared secret or Developer API key. The Ktor `signature2` scheme is *not* reproduced in Lookout today (only the legacy dynamic signature is partially relevant to wcsa).

**2FA / login variants observed:**
- Passkey: `/app/login/passkey/option`, `/app/login/passkey/assertion`.
- OAuth: `oauth/authorize?response_type=code&client_id=…&redirect_uri=wyze://wyzeapp:8080/v2?id=wyze/auth/login&scope=authcode-read%20native%20openid%20email&kount_session_id=…` (Kount session id for fraud scoring).
- Account info: `/app/user/get_user_info`, `/user/account`, `gateway-oauth/user/account`, `gateway-oauth/passkey`.

**Lookout-owned login handshake (one-time code, never the TOTP secret):** `wyze-sdk` is used only as the HTTP transport/header layer (`AuthServiceClient`); the login handshake lives in `providers/wyze/authenticator.py`. Initial login is `POST /api/user/login` with `{email, password: triple_md5(password), nonce}`. A response that carries `access_token` completes immediately (no 2FA prompt). Otherwise the challenge is normalized from `mfa_details.primary_option` / `mfa_options`:
- **Email:** `POST /v2/user/login/sendEmailCode` with params `{userId, sessionId: email_session_id}`; the response `session_id` becomes `verification_id`. A 404/405 falls back once to `POST /api/user/login/email/mfa_sending_email` with `{email, user_id}`.
- **SMS:** `POST /user/login/sendSmsCode` with params `{mfaPhoneType: "Primary", sessionId: sms_session_id, userId}`; the response `session_id` becomes `verification_id`.
- **Authenticator app:** no send; `verification_id` is `mfa_details.totp_apps[0].app_id`.

The user's one-time code is then sent verbatim as `verification_code` (with `mfa_type`/`verification_id`) to `POST /api/user/login`. It is never passed to `mintotp.totp`, never treated as a shared TOTP secret, and never logged. `totp_key` is **not** collected anywhere in the UI or config.

**Integration note:** The native data plane is token-first (`wyze_sdk.Client(token=…, refresh_token=…)`). Developer API keys (`key_id`/`api_key`) are **not required** for login or event polling and have been removed from `WyzeConfig`, the setup wizard, sidecar sync, credential storage, discovery, and the manifest; the login endpoint still tolerates optional `api_id`/`api_key` from manual API callers as `keyid`/`apikey` headers. The remaining gap is the *dynamic signature* (`signature2` / legacy) for wcsa and device-management calls (see §15.7); if that lane (#68) proves to need Developer API keys, they will be reintroduced scoped to it with captured evidence.

## 14b. Notifications (push + in-app) — follow-up pass

**Delivery mechanism:** standard Firebase Cloud Messaging (FCM). `com.hualai.home.fcm.WyzeFcmReceiver` handles `com.google.android.c2dm.intent.RECEIVE`, parses the FCM `RemoteMessage` into `PushMessageModel`, and routes via `com.hualai.home.fcm.b` (notification builder). FCM token registration with Wyze cloud is not fully resolved statically (likely a device-registration call during login/setup).

**Push payload schema (`PushMessageModel`):** `type`, `title`, `content`, `mac`, `device_name`/`deviceName`, `device_model`/`deviceModel`, `alarm_id`, `message_id`, `group_id`, `template_id`, `product_type`/`productType`, `mt` (int), `mt_cat` (int), `od_flag` (bool), `is_owner`, `is_t3`, `is_call911`, `media_url`, `video_url`, `v_url`, `url`, `voip`, `payload`, `sound`, `collapse_key`, `priority`/`originalPriority`.

**Routing types:** `TYPE_POPUP`, `TYPE_SILENCE`, `TYPE_SIMPLE`, `TYPE_ROUTER`, `TYPE_URL` — determine whether a system notification is shown, handled silently in-app, or deep-links via router/URL.

**Push settings are device-management capabilities** (toggled through the same cloud REST as motion-tag): `MotionPush`, `SoundPush`, `SmartDetectionPush`, `LowBatteryPush`, `AiPush`, `PushSwitch`, `PushNotificationAction` (all in `network/…/devicemanagement/data/`). These are the per-device/per-event push toggles the settings screen edits.

**Lookout implementation note (#85) — settings sync only.** Lookout exposes the six *boolean* capabilities (`MotionPush`, `SoundPush`, `SmartDetectionPush`, `LowBatteryPush`, `AiPush`, `PushSwitch`) through `adapters/wyze_push_settings.py` over the same `get_iot_prop` read / `run_action` write lane as motion tagging (§15.6). The key→property table is pinned in `PUSH_SETTING_PROPERTIES` as `<capability>::push`; that leaf name is **unverified against a live device** — operator confirmation on a capable device is a post-merge step that must update this section. `PushNotificationAction` is excluded from the boolean table and is never written (it is an action/enum in the APK, not a toggle, and its value type is unconfirmed). Writes additionally require the key to have been observed by a prior read, so Lookout never writes a property it has not read back. FCM push *delivery* remains out of scope: Lookout is pull-based and cannot register with FCM.

**In-app notification center endpoints:** `/wyze/pushnotification`, `/wyze/notification/combined`, `/wyze/notification/page`, `/wyze/iamnotification`, `/open/device/notification/`, `/wyze/notification/setting`.

**Wyze-side event→notification mapping:** events polled via `/app/v4/device/get_event_list` etc. are correlated to pushes by `alarm_id`/`message_id`/`mac`; media attached via `media_url`/`video_url`.

**Lookout implication:** Lookout is **pull-based** (native event poller + SSE broadcast hub + HA egress), not push-based. It cannot receive FCM pushes (no FCM registration). Mirroring Wyze's push taxonomy into Lookout means mapping `PushMessageModel` fields (`mt`, `mt_cat`, `media_url`, …) onto Lookout's event model and re-delivering via SSE/HA — feasible for event types (motion/sound/person/low-battery) but the delivery mechanism and latency differ. Push settings (`MotionPush` etc.) overlap with Lookout's existing `notification_event_types`/`notification_min_threat`/`notify_pass1/pass2` config, but they are Wyze-cloud-side toggles, not Lookout-side ones. No protocol change needed in Lookout unless we want to drive Wyze's push settings from the Lookout UI (would reuse the device-management `run_action` lane from §15.6).

## 15. Integration notes for Lookout (base: `phase/10-fixes`)

> **Base identification.** The canonical base is `origin/phase/10-fixes-phase-10-fixes-native-wyze-ring-ingest-thumbnails-live-stream-and-detection-parity` (commit `1b151b2`), **not** the local checkout (`738b968`, the same-named branch, ~5 commits behind; 49 files differ). Differences that matter for this plan: `adapters/mqtt_ptz.py` is **deleted** on the remote branch (so no wyze-bridge/MQTT PTZ path — native cloud PTZ only), `native_stream_manager.py` and `native_event_poller.py` gained live/event work, and `domain/ptz_service.py` exists. Do not build on the stale local branch; create a worktree off `origin/phase/10-fixes-…` (e.g. via `opencode worktree_create`) before implementing.

### 15.0 Ground rules (from `docs/ARCHITECTURE.md` + repo conventions)

- `lookout.domain` must not import `fastapi`/`httpx`/any adapter; ports in `ports/egress.py` are `@runtime_checkable Protocol`s; no sibling adapter cross-imports (existing precedent: `wyze_native.py` imports `NativeStreamManager`, which is accepted practice).
- New capability = new port method or new port in `ports/egress.py` + adapter in `adapters/` + wiring in the DI/bootstrap (`adapters/web` `app.state.*` and provider plugin), never domain→adapter shortcuts. Enforced by `tests/test_architecture.py`.
- Adapters get unit tests next to existing ones (`tests/test_native_*.py`, `tests/test_provider_registry.py`); integration tests behind env flags; docs updated (`ARCHITECTURE.md` port table) if a new port is added.
- PTZ already has the full vertical: `PtzControlPort` (`move/stop/step/goto_preset/set_preset/list_presets/get_position`) → `PtzControlService` (validation, `ptz_max_speed` clamping, audit) → routes `POST /api/cameras/{camera_id}/ptz` and `/api/cameras/{camera_id}/snapshot`, `.../record` → adapters (`WyzeTutkPtzAdapter` via cloud `run_action`, `Go2RtcPtzAdapter` via go2rtc/ONVIF, `MockPtzControlAdapter` for tests). `WyzeNativeAdapter` covers `VideoRecorderPort + PtzControlPort` and delegates PTZ to `WyzeTutkPtzAdapter`.
- Provider plugin system: `providers/wyze/{manifest.json, driver.py, discovery.py, sync.py, routes.py}` declares auth fields/manifest; discovery probes creds; registry auto-discovery is asserted in `tests/test_provider_registry.py`.
- Embedded go2rtc (`NativeStreamManager`) is the media plane for native Wyze: `wyze://ip?uid=…&enr=…&mac=…&model=…&subtype=hd&dtls=true`. Live, clip recording, snapshot all flow through it.

### 15.1 Take photo (snapshot) — already built on this branch (verify only)

- APK finding: snapshot = client-side frame capture, **no camera command**. Lookout equivalent: `WyzeNativeAdapter.capture_snapshot(camera_id)` → `NativeStreamManager.ensure_stream()` + `manager.frame()` → JPEG bytes (magic-byte checked) written to temp; exposed via `POST /api/cameras/{camera_id}/snapshot` (returns path/URI, copied into `<base_path>/snapshots/`) and `GET /api/cameras/{camera_id}/snapshot` (latest JPEG, falls back to cached event snapshots).
- **Action:** no code. Verification item (§12): run against a real V3/Pan v3 with native ingestion; confirm `Journal` line shows `ensure_stream` + frame fetch; confirm fallback ordering (live go2rtc frame → newest cached event snapshot → placeholder).
- Optional polish (not RE-derived): Luma/visible-ratio black-frame rejection like the app does, if the frame API ever returns dark frames (go2rtc can return a black JPEG pre-playout).

### 15.2 PTZ — two viable lanes

**Lane A (works today, no RE): keep cloud `run_action` PTZ.**
`WyzeTutkPtzAdapter._run_action()` calls wyze-sdk's private `api_client().run_action(mac, provider_key, action_key, action_params)`:

- `move(pan, tilt)` → `rotary_degree {horizontal, vertical, speed}` (pan/tilt in degrees, ±90 int, speed mapped to 1–9).
- `step(direction)` → `rotary_action {horizontal: 1|2, vertical: 1|2, speed}` (`left`=1, `right`=2, `up`=vertical 1, `down`=vertical 2), `stop()` → `rotary_action {0,0}`.
- `goto_preset(n)` → `cruise_point {point: n}` (numeric cruise points only); `set_preset` unsupported; zoom unsupported (explicit `status:"unsupported"`).
- Gaps vs. app: no numeric `set-position`, no `get-position` (adapter echoes last command), int-only cruise presets, no waypoint naming — fine for Lookout's current `PtzControlPort` surface.

**Lane B (RE-derived, only if/when wcsa signing lands): Agora data-channel PTZ.**
Add `WyzeAgoraPtzAdapter(PtzControlPort)` (new file, e.g. `adapters/wyze_agora_ptz.py`) **only after** §15.7 spike proves the signing:

- `step()` → `run_action { action: "camera-position::move-position", params: { direction: "<dir lowercase>", step: 1000 } }` over RTC data-stream (`sd-card::` style envelope confirmed: `{cmd:"run_action", tid, ts, action, params}`, `result==0` = OK).
- `move()` continuous → `move-position` repeat/hold (model on Lane A's mapping, or `set-position {horizontal, vertical}` for absolute) — semantics of `step=1000` fixed: send one "nudge" per call, or hold semantics need on-device validation before committing.
- `get_position()` → `get_property`-equivalent action `camera-position::get-position` (params `{}`), decode `{horizontal: int, vertical: int}` → normalized [-1..1] for `PtzControlPort`.
- `stop()` → `camera-position::stop-move-position`.
- `goto_preset(i)` → `camera-position::goto-waypoint { home: bool, index: int }` — map numeric preset → index; treat `"home"` as index 0/home=true.
- `move-position-ex {dx, dy}` available for gesture-style drags if a future UI wants it.
- Wire it in `WyzeNativeAdapter.__init__` behind a config flag `wyze.agora_ptz: bool = False` choosing between `WyzeTutkPtzAdapter` and the new adapter, keeping `PtzControlService` + routes untouched.
- Audit through the existing `PtzControlService` audit path — do not bypass it.

**Limited-scope alternative — cloud IoT `set_property` PTZ (untested for PTZ, plausible).** `camera-position::*` writes may be reachable through the same device-management `run_action` REST (`§15.6`) as `camera-pnt::scan`; if the spike confirms, Lane B's adapter can target the cloud endpoint instead of the RTC channel — much cheaper, no Agora media needed. Validate on device before choosing.

**Lookout implementation note (#71).** Lane B ships as `adapters/wyze_agora_ptz.py` (`WyzeAgoraPtzAdapter`), selected by `wyze.agora_ptz` (default `false`; Lane A stays the default and the fallback).

- **Shipped transport is the cloud `run_action` variant:** `POST <devicemgmt>/device-management/api/action/run_action` with `capability: "camera-position"`, `iid: null` until a capability map is captured, and one property `{separator: "::", prop: "camera-position::<action>", value: <params>}` signed by `WyzeSigner` (`signature2`). The protocol-exact data-channel envelope (`{cmd: "run_action", tid, ts, action, params}`) also ships, behind an injected async sender (`DataChannelCameraPositionTransport`); `create_app` does not populate it.
- `step(direction)` sends `move-position {direction: <lowercase up|down|left|right>, step: 1000}`; the step magnitude is **fixed** at 1000 and `amount`/`speed` are accepted but **not transmitted**. `stop()` sends `stop-move-position {}`. `move()` maps to absolute `set-position {horizontal, vertical}` in degrees (`pan*90`), matching Lane A's scale; zoom is unsupported.
- `get-position` is sent with empty params and decoded from the response `params`/`data`/`result`/`response` container, then **normalised against ±90** and clamped to `[-1, 1]` (`source: "camera-position::get-position"`). No `last_command` fallback: a response without a position raises a 502-mapped error.
- Presets are **numeric indices only**: `goto_preset("0".."N")` → `goto-waypoint {home: false, index: N}` and `"home"` → `{home: true, index: 0}`. `set_preset` / `list_presets` are explicitly unsupported (`edit-waypoint`'s value shape is not captured, so no speculative write).
- All commands flow through `PtzControlService` (audit untouched); unsupported commands are returned values, not exceptions.
- **Caveat:** the cloud envelope is unverified on hardware. If the operator's on-device smoke check shows a different envelope, only `CloudCameraPositionTransport.run_action` needs to change.

### 15.3 SD-card playback — explicitly out of scope for phase/10-fixes

- Requires wcsa signing (§15.7) **and** either (a) Agora RTC/RDT media path (no Linux userland SDK available) or (b) legacy gwell/TUTK pass-through (`INNER_PASS_THROUGH_CMD_PLAYBACK_*`) below evidence we should extend go2rtc's wyze:// with it — bounded but touchy, and it only covers v2/Pan-class SD.
- Closest existing primitive on branch: `record_clip` (embedded go2rtc, live segments) and `poll_events` + `native_event_poller.py` (event clips, two-pass 10 s windows). SD playback would be "the same, with a seek + coverage bitmap (`get_playback_info_by_minute`)" — the bitmap query alone is a cheap, useful feature: render SD coverage per camera. That is the only piece of SD functionality worth a candidate issue for a later phase, and only after the wcsa spike succeeds.
- Do not add a port/adapter for SD playback in this branch. Keep this document as the record.

### 15.4 Speak (two-way audio) — defer; document why

- App flow: `speech_control {uid: <rtc_client_uid>, status: ENABLED|DISABLED}` (hualai JSON, data channel) then publish local mic as voice-communication RTC audio; camera speaker plays it. Legacy HL cams: native "talking channel" (HLApi). Both need an Agora/HL session Lookout doesn't have and a **playback/publish** go2rtc path (it is ingest-only today).
- Note for future: if Agora Linux SDK is ever adopted for SD media, speak falls out of the same stack: `PtzControlService`-style new `AudioTalkService` + `AudioTalkPort` + `WyzeAgoraTalkAdapter` implementing "hold to talk" (push-to-talk semantics; `SPEAKER_CONTROL_STATE` events map to `SpeakerControlCameraProperty` statuses `OCCUPIED/SUCCESS/STOP/FAILED/NO_EXISTENCE`).
- Do not attempt now; a half-baked one-way path is worse than none.

### 15.5 PanScan — reuse the IoT-capabilities pattern (spike)

- Modern cams: boolean property **`camera-pnt::scan`** (get/set via `get_property`/`set_property` on the data channel; `ON`/`OFF` with `SP` gate in app). Related: `camera-pnt::scan-nodes` (set scan span), `camera-pnt::tracking*` (smart-track preempts motor).
- Two implementation lanes:
  1. **Cloud lane (preferred spike):** treat it like motion-tag (§15.6): `device-management/api/action/run_action` with `setPropsWithName("::", "camera-pnt::scan", true)`-shaped envelope (`capabilities[].properties[] = { separator:"::", prop:"camera-pnt::scan", value: bool }`) and read via `device-property/get_iot_prop`. No camera channel needed; guard by model capability (`isIOTSpace3Dev`-style gating — verify via `get_iot_prop` returning a value, fallback to hidden toggle).
  2. **Agora lane:** same as 15.2-B (data-channel `set_property`) behind the same signing spike.
- Suggested surface: a camera-settings toggle `pan_scan_enabled: bool` next to existing `ptz_enabled` in `CameraConfig`; new route `POST /api/cameras/{camera_id}/pan_scan {enabled: bool}` (mirror `/ptz` shape) or a generic `POST /api/cameras/{camera_id}/settings` extension via `PtzControlService.update_settings` — the latter is already the sink for `enabled/sensitivity/mute/notifications_*` and audits for free. Prefer the generic settings extension if it lands cleanly; do **not** add a dedicated port.
- Gate UI on capability detection; Pan Scan absent on fixed cams.

### 15.6 Motion tagging — best RE-derived candidate for this phase

- App flow (modern plugin family, e.g. Cam v4/Ascent): **cloud-only** —
  - Read: `POST <devicemgmt_service_wyze>/device-management/api/device-property/get_iot_prop` with `{ userId, targetInfo:{id:<deviceId>, productModel, type:"DEVICE"}, capabilities:[{name:"motion-detection", iid:<from capability map>, properties:[{separator:"::", prop:"motion-detection::motion-tag", value:…}]}] }`;
  - Write: `…/device-management/api/action/run_action`, same envelope, `value: bool` (dynamic signature + `Authorization`).
  - `MotionTag` shape confirms `id="motion-tag"`, value bool, capability container `motion-detection`.
- Cam v3 native settings instead use the HL stack (`ConnectControl.setCameraProperty` with an obfuscated bool property class; wire id unresolved) — **skip cam v3 for the first iteration**; support ascent/cam4-class only, via explicit model allowlist, and degrade silently otherwise.
- Design (small, mirrors existing patterns):
  - New port `IotCapabilityPort` in `ports/egress.py` (or `MotionTagPort` if strict): `async def get_motion_tag(camera_id) -> bool`, `async def set_motion_tag(camera_id, enabled: bool) -> Any`.
  - New adapter `adapters/wyze_iot_cloud.py` (`WyzeIotCloudAdapter`): owns the device-management signing + capability-map resolution (from device-management device capabilities call, cached per device; `motion-detection` capability iid is required for the write path — if the device doesn't advertise it, mark unsupported).
  - New domain service? No — fold into `PtzControlService`-style `CameraSettingsService`? Resist: reuse `PtzControlService.update_settings` sink by adding `motion_tag` key → optional `motion_tag_adapter` injected the same way `ptz_adapters` is; keep domain free of transport.
  - Config: `CameraConfig.motion_tag_enabled: bool | None = None` (tri-state; None = unknown/hidden), plus `WyzeConfig.iot_capability_api_url` only if the default `${devicemgmt}` base differs from what auth path yields — the APK uses `WpkDomain.devicemgmt_service_wyze("/device-management/api/...")` (same host family as `app.wyzecam.com`, exact host to confirm in traffic).
  - routes: `POST /api/cameras/{camera_id}/motion_tag {enabled: bool}` (mirror `/ptz` + audit), and surface the toggle next to `ptz_enabled`/`notifications_*` in the camera settings UI; Home Assistant egress (already on phase-5 lineage, re-check against this branch) can expose `switch` if desired.
- **Blocker remains** the dynamic-signature spike (§15.7). Until it lands, ship nothing of this.
- Tests: golden-envelope unit tests with the signer mocked (do **not** capture real tokens into tests); capability-map fixture; `isIOTSpace3Dev`-style negative tests (model gate → `503/unsupported`); architecture tests pass (adapter isolation).

### 15.7 The gating spike: Wyze "dynamic signature" for wcsa/device-management calls

Everything in 15.2-B/15.5/15.6 hangs on this. Plan:

1. Obtain one known-good captured pair (request body + headers + response) for `create-connection` and one `run_action`/`get_iot_prop`, **from a consented on-device capture** (`adb logcat` won't show bodies; use a rooted emulator or a test APK build, or proxy with custom CA where pinning does/doesn't apply — the app uses no cert pinning that we verified; confirm during capture).
2. Reconstruct `WpkSignature2Service`'s canonical string from the APK (class was present; semantics not fully resolved) against the captured sample until signatures match.
3. Deliver as `adapters/wyze_signing.py` with golden-vector tests; if it takes >2–3 days or Wyze changed the scheme, stop and revert to Lane A + document.
4. Tokens: reuse existing `WyzeConfig.auth_token`/`refresh_token` (native_ingestion already maintains them via the adapter's `_persist_wyze_tokens`).

### 15.8 Suggested sequencing inside a future phase

1. (done on branch) snapshot; 2. wcsa signing spike (time-boxed); 3. motion-tag toggle for ascent/cam4-class + `camera-pnt::scan` if the same spike covers it; 4. PTZ Lane B or cloud `set_property` PTZ; 5. SD-playback bitmap query (read-only coverage) if still interested; 6. Speak/SD media — only with a dedicated Agora decision.

### 15.9 Risk register

- **Wyze changes signing/endpoints** → all of 15.2-B/15.5/15.6/15.7 silently break; keep Lane A fallbacks and feature flags off by default.
- **ToS**: same posture as the rest of native ingestion (personal, self-hosted; documented; no DRM circumvention). Not legal advice.
- **Capability gating drift** (`isIOTSpace3Dev`, `SP_IS_SUPPORT_*`): features vanish per model/firmware; every toggle must degrade to hidden/disabled, never error-spam.
- **Audit/privacy**: PTZ/speak/motion writes must flow through `PtzControlService`-style audit (already exists) — do not add silent command paths.
- **Local checkout is stale** relative to the remote phase branch — wrong base will silently reintroduce `mqtt_ptz.py`-era assumptions and conflict with the native-ingest fixes.



## 16. Appendix — artifacts & hygiene

This document lives in the repository at `docs/wyze-protocol-notes.md` (committed as
documentation only). The reverse-engineering scratch artifacts (APKM, base.apk,
extracted DEX, dumps, scripts) live under `/tmp/opencode/wyze/` and are **not**
committed — delete them with `rm -rf /tmp/opencode/wyze` once the phase-11 work
is done. The APKM must never enter git.

The protocol facts in this document are derived from static analysis of the Wyze
app v3.25.1.1037 for personal interoperability research; no implementation code
from the app was copied. Not legal advice.
