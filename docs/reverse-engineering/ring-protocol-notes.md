# Ring app → Lookout: Protocol Notes (Auth & API surface, Live stream, Cloud recordings, Motion events, Motion tagging)

**Date:** 2026-11-19 (updated same day with §11 — focused pass on OS push notifications / FCM·ADM payloads)
**Source:** Ring app `com.ringapp` v3.117.0 (version code 70498401), APKM arm64-v8a / 480–640dpi / Android 10+, posted 2026-11-15. Static analysis with androguard 4.1.4 (DEX disassembly + annotation decoding; **no implementation code copied**).
**Purpose:** Understand how the official Ring app implements these features against Ring's cloud/cameras, to evaluate Lookout integration.
**Status:** Investigation notes. Artifacts live only in `/tmp/opencode/ring/` and are disposable; the APKM and this doc are not in the repo.

> **Legal posture.** Independent interoperability research for personal, self-hosted use. Only protocol facts (hostnames, endpoint paths, command/field names, JSON/protobuf shapes) are recorded. Ring ToS likely prohibit this kind of analysis; scope is documentation, not circumvention. No credentials, accounts, or live traffic were used. Everything below is inferred from static analysis only (no runtime capture), so wire-level details are marked with a confidence level.

---

## 1. Executive summary

- **Auth is standard OAuth 2.0 against `oauth.ring.com`**, plus a "scoped token" PKCE flow for device-access tokens. All REST calls use `Authorization: Bearer <access_token>`.
- **REST surface** is split across a few bases on `api.ring.com` (`clients_api/`, `devices/v1/`, `honeycomb/api/v1/`) plus a large "EVM/orchestrator" API (`evm/v2/*`, `evm/v3/*`) that serves the event/timeline/history system. 919 Retrofit endpoints were enumerated (see `all_endpoints.txt`).
- **Live view and recording playback both use WebRTC**, signaled over a **SignalR WebSocket** at `wss://api.prod.signalling.ring.devices.a2z.com/ws`. Signal messages are JSON envelopes `{"type": …, "body": …}` (and a second `{"method": …, "body": …}` dialog family). ICE/SDP are exchanged over that socket; the media plane is WebRTC, with encrypted variants for E2EE devices.
- **Playback control** (`play`, `pause`, `seek`, `set_playback_speed`, `switch_feed`, `standby`, `share_keys`) rides a WebRTC **data channel**, not REST.
- **Cloud recordings** are exposed as event/media objects (`OrchestratorEventResponse`, `OrchestratorCloudMediaResponse`, `OrchestratorLocalMediaResponse`) with `url`, `manifestId`, `fileType`/`fileFamily`, `preRollDuration`, `playbackDuration`, `isE2ee`. Legacy "ding" APIs still exist (`dings/{id}`, `dings/{id}/share/play`).
- **Motion events** arrive as push notifications (`push_event` signaling messages) and are queryable via `evm/v2|v3/history/events`, `evm/v2/activity_feed/events`, and the 24x7 timeline endpoints. CV metadata (`personDetected`, `otherMotion`, detection details, profiles) is embedded in event responses.
- **OS push notifications (FCM/ADM) are a separate system**, documented in §11: a versioned payload (`version` 2.0.0–2.4.0) with namespaced fields (`android_config:*`, `analytics:*`, `img:*`, `video:*`, `navigation:*`), a typed `PushData` model, and ~24 Android notification channels. FCM token registration goes through Amazon Identity, not a Ring REST endpoint. This is **not** the same as the SignalR `push_event` stream in §6.
- **Motion tagging** (the green boxes) is a **rendering-control protobuf over the same WebRTC data channel**: `RenderingControlMessages` with `AztBoundingBox` / `AztState` / `Coordinate` messages (`coordinateTopLeft`, `coordinateBottomRight`, `confidence`). The on/off setting is the `cv_person_detection` capability; the client only renders boxes when `show_radar_data` / the person-detection overlay setting is enabled.
- **E2EE** is Amazon "Cerulean" + `libsecureframe.so` with content-encryption keys delivered via `auth/mobile/encryptionkey` / Cerulean CKMS. E2EE media is tagged `isE2ee: true` and uses encrypted WebRTC playback sessions.

---

## 2. Analysis environment

- APKM → `base.apk` (17 DEX files, ~184,353 classes) + `split_config.arm64_v8a.apk` (native libs).
- Tooling: Python 3.12 + androguard 4.1.4; scripts in `/tmp/opencode/ring/` (`classlist.py`, `strings.py`, `endpoints.py`, `annots.py`, `dumpall.py`, `findclassstr.py`, `dumpmethod.py`); a fresh `.venv/`.
- Native stacks in arm64 split (no Agora/TUTK, unlike Wyze): `libjingle_peerconnection_so.so` (WebRTC), `libfsv-converter-*`, `libmp4econverter`, `libmp4v2`, `libsrtp2-fsv.so`, `libsecureframe.so`, `libceruleancore.so`, `libamzn_greco_core.so`, `libsqlcipher.so`.
- Raw artifacts: `all_classes.txt`, `all_strings.txt`, `strings_clean.txt`, `all_endpoints.txt`, `dump_*.txt`.

---

## 3. Auth & API surface

### 3.1 Primary OAuth (confidence: high)

Host: `https://oauth.ring.com`

| Item | Value |
|---|---|
| Token endpoint | `POST /oauth/token` |
| Grant types (`OAuthTokenRequest$GrantType`) | `PASSWORD`, `REFRESH_TOKEN` |
| Request fields | `client_id`, `grant_type`, `username`, `password`, `refresh_token`, `scope`, `expires_in` |
| `client_id` constant | `ring_official_android` |
| Response fields (`OAuthTokenResponse`) | `access_token`, `refresh_token`, `token_type`, `expires_in`, `created_at`, `scope` |
| Error fields | `error`, `error_description` |

Evidence: `dump_auth_dto.txt`, `dump_auth_okhttp.txt`.

`AuthenticateInterceptor` adds `Authorization: Bearer %s` to all authenticated calls. `RefreshAuthenticator` handles 401-style refresh with exponential backoff (`ExponentialBackoffTracker`), retry headers `retry-after`/`retry_after`, and revocation detection. Tokens are stored encrypted (`SecureStore`, AES/CBC/PKCS5Padding, key via SHA-256).

### 3.2 Scoped / PKCE token flow (confidence: high)

`ScopedTokenApi` (`dump_auth_scoped.txt`), used for device-access / partner / "Bold" smart-lock style access:

| Endpoint | Method |
|---|---|
| `GET oauth/v2/authorize` | `authorize(code_challenge, …)` — PKCE with method `S256` (`PkceUtil`, SHA-256) |
| `POST oauth/token` | `exchange(code, code_verifier, …)` / `refresh(refresh_token, …)` |

`ScopedTokenProvider` caches tokens per scope, mints/renews/exchanges them, and tracks attempts (`ScopedTokenAttempts`). There is also a `POST oauth/v2/verify` / `POST /oauth/v2/verify_otp` path for 2FA verification.

### 3.3 REST bases (confidence: high)

| Base | Used for |
|---|---|
| `https://api.ring.com/clients_api/` | legacy device/ding/session APIs (`profile`, `session`, `dings/{id}`, `doorbots/{id}/subscribe`, `ring_devices/{id}`) |
| `https://api.ring.com/devices/v1/` | device/location management (`devices/v1/locations`, `devices/v1/devices/{deviceId}`) |
| `https://api.ring.com/honeycomb/api/v1/` | device-control/commands |
| `https://api.ring.com/cv_identity/` | CV identity |
| `https://app-content.ring.com` | static assets |
| `https://eventstream.ring.com` | event stream |
| `https://neighbors-api.ring.com` | Neighbors feed (largely out of scope here) |

### 3.4 Key endpoint families (full inventory in `all_endpoints.txt`)

EVM/orchestrator history & timeline (`TimelineOrchestratorApi`, `com.ringapp.orchestratorapi.data`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `evm/v2/events` | event query |
| `POST` | `evm/v3/history/events` | history query (v3) |
| `GET` | `evm/v2/history/events/{eventId}` | single event |
| `GET` | `evm/v2/history/devices`, `evm/v3/history/devices` | history by device |
| `GET` | `evm/v2/timeline/devices/{doorbotId}` | device timeline |
| `GET` | `evm/v2/timeline/24/devices/{source_id}` | 24x7 timeline |
| `GET` | `evm/v2/timeline/events/eventito/{source_id}` | eventito timeline |
| `POST` | `evm/v2/history/extendedsearch`, `GET ees/v2/history/extendedsearchmetadata` | search |
| `POST` | `evm/v2/events/watch` | mark watched |
| `PUT`/`DELETE` | `events/favourite`, `events/unfavourite`, `evm/v2/dings/{id}/favorite` | favorites |
| `DELETE` | `evm/v2/dings`, `dings/{id}`, `evm/v2/events/time-based-deletion/{source_id}` | deletion |
| `GET` | `evm/v2/activity_feed/events` | activity feed |
| `GET` | `evm/v2/history/unwatched/count`, `evm/v2/metadata/history/devices` | metadata |

Legacy/clients ding APIs (`com.ringapp.net.api.ClientsApi`, `com.ringapp.orchestratorapi.data.ClientsApi`): `GET dings/{id}`, `DELETE dings/{id}`, `PUT dings/{id}/favorite`, `GET dings/{id}/share/play`, `GET ring_devices/{deviceId}`, `GET doorbots/{id}/throughput_test`, `POST doorbots/{id}/subscribe`.

Other relevant: `GET next/{device_id}` (`SnapshotsApi`), `POST /api/v1/rs/history` (`HistoryApi`), `GET doorbots/{deviceId}/motion_sensitivity_profile` / `POST doorbots/{id}/motions_subscribe` / `motions_unsubscribe` (`MotionClientsApi`), `DELETE recordings/public/footages/{deviceId}` (`FootageApi`).

---

## 4. Live stream (WebRTC + SignalR signaling) — confidence: high

### 4.1 Signaling transport

- Endpoint: `wss://api.prod.signalling.ring.devices.a2z.com/ws` (SignalR / ASP.NET Core, `com.microsoft.signalr` classes in the APK).
- Headers set by `SignalingHeadersInterceptor` (`dump_signaling_config.txt`):
  - `X-Sig-API-Version: 4.1`
  - `X-Sig-Auth-Type` — populated from the auth provider
  - `X-Sig-Client-ID`
  - `X-App-Correlation-ID`
  - `User-Agent: Ring/<version> (Platform/Android; …)`
- Auth: `SignalingAuthProviderImpl` uses `RingAuth.getAccessToken()` and checks expiry via `AuthenticateInterceptor` — i.e. the same OAuth bearer token as REST.
- Config holder: `SignalingConfigImpl(endpoint=…)`; `Signaling4SessionInfo` carries `createdAt`, `sessionId`, `authType`, `rmsIpAddress`, `rmsVersion`, `serverRegion`, `serverCellId`, `signalingCellId`.

### 4.2 Message envelope

Two families were observed:

- `SignalingServerMessage` / `SignalingClientMessage`: top-level `{"type": "<type>", "body": {…}}` (`type` + `body` keys confirmed in `SignalingMessageMarshallerImpl`; `body.doorbot_id` (long) and `body.dialog_id` (string) read by `getDeviceIdSafe` / `getDialogIdSafe`).
- `DialogServerMessage` / `DialogClientMessage`: `{"method": "<method>", "body": {…}}`.

Other confirmed wire keys: `session_id`, `sdp`, `ice`, `ice_removed`, `entry_point`, `stream_info`, `camera_options`, `stream_options`, `mic_overridden`, `notification`, `notification_scope`, `notification_type`, `push_event`, `push_error`, `push_subscription_ack`, `push_update_ack`, `subscription_id`, `filter_identifiers`, `requested_notifications`, `ingestion_time_ms`, `telemetry_metadata`, `timed_metadata`, `pong`, `preroll_response`, `close`, `rpc`, `stream_duration_warning`, `seconds_remaining`.

### 4.3 Message types (from `SignalingClientMessage` / `SignalingServerMessage` inner classes + string corpus)

**Client → server:** `live_view`, `playback`, `activate_session`, `extend_stream_duration`, `sdp`, `ice`, `ice_removed`, `rpc`, `mic_enable`, `ping`, `close`, `pre_roll_request`, `notification`, `push_subscribe`, `push_unsubscribe`, `push_update`, `push_heartbeat`, `push_subscribe_requested_notification`, `stream_options`, `camera_options`.

**Server → client:** `session_created`, `session_started`, `stream_info`, `stream_duration_warning`, `sdp`, `ice`, `ice_restart`, `ice_removed`, `pong`, `preroll_response`, `rpc`, `notification`, `mic_overridden`, `push_event`, `push_error`, `push_subscription_ack`, `push_update_ack`, `telemetry_metadata`, `timed_metadata`, `close`, `malformed`, `unknown`.

### 4.4 Session setup

1. App opens the WS with the headers above.
2. Sends `live_view` or `playback` (body: `device_id`/`doorbot_id`, `session_id?`, `entry_point`, `stream_options` — `LiveStreamOptions` has `videoEnabled`, `audioEnabled`, `forceAlive`, `stealthMode`, `cooldownTimeout`, `invalidateBefore`), plus `searchPartyMetadata`.
3. Server replies `session_created` (`sessionId`, `sessionInfo`, `eventId`, `eventKind`), then `session_started` and `stream_info` (`isRecording`, `isTranscoding`, `transcodingReason`, `url`).
4. Standard WebRTC offer/answer via `sdp` and trickle ICE via `ice` / `ice_removed`; server may send `ice_restart`.
5. Live view uses `BaseV4LiveSession` / `RmsV4LiveSession` / `JawsLiveSession` / `WebRtcPlaybackSessionFactoryImpl` (see `all_classes.txt`); peer connection manager wraps `org.webrtc.PeerConnection`.

---

## 5. Cloud recordings & playback — confidence: high

### 5.1 Event/media model (from `dump_event_models.txt`)

`OrchestratorEventResponse` (the history/timeline event object) fields:

`schema`, `eventId`, `sourceId`, `sourceType`, `eventType`, `startTime`, `endTime`, `durationMs`, `state`, `isActive`, `isFavorite`, `isWatched`, `lowConfidence`, `hadSubscription`, `userActionDisabled`, `userSpecifiedRecordingDisabled`, `deleteInProgress`, `updatedAt`, `errorCode`, `errorMessage`, `origin`, `originDisplayName`, `ownerId`, `credentials`, `cv` (CV metadata), `properties`, `visualizations`, `device`, `mission`.

`OrchestratorCvResponse`: `personDetected`, `otherMotion`, `detectionType`, `profiles`, `detectionDetails`, `streamBroken`, `shortSmartVideoDescription`, `fullSmartVideoDescription`, `similarity`, `anomaly`.

Media objects:

- `OrchestratorCloudMediaResponse`: `startTime`, `endTime`, `url`, `manifestId`, `fileType`, `fileFamily`, `customMetadata`, `isE2ee`, `preRollDuration`, `playbackDuration`.
- `OrchestratorLocalMediaResponse`: same plus `schema`, `frameTimestamps` (list).
- `OrchestratorFootageResponse`: `sourceId`, `startTime`, `endTime`, `durationMs`, `url`, `storageType`, `fileType`, `isDeleted`, `perLensFootages`, `customMetadata`.
- `OrchestratorMediaTrack`: `qualityLevel`, `type`, `lensId`.
- `MediaResponse`, `MetadataResponse$Cloud|$Local`, `HasMedias`, `HasUrl`, `HasE2ee`, `HasPreRollDuration`, `HasDynamicResolution`, `HasRadarData` — interface-style field contracts.

Media formats seen in strings: `video/mp4`, `application/x-mpegURL` (HLS `m3u8`), `DASH`/`mpd`, `webm`; local files named `video.mp4`, `video_decrypted.mp4`, `_live_preroll.mp4`.

### 5.2 Playback session

`WebRtcPlaybackSessionPoolService.SessionParams` has `Cloud` and `Local` variants; `PlaybackEntryPoint` enum = `TIMELINE`, `TIMELINE_24_7`, `EVENT_HISTORY`. The signaling `Playback` message carries `deviceId`, `sdp`, `sessionId`, `type`.

`WebRtcPlaybackSession` / `WebRtcPlaybackSuperSession` manage the session; `PlaybackCommandSender` and `DataChannelManagerImpl` send control commands over the WebRTC data channel. Command literals (from `dump_datachannel.txt`):

| Command | Purpose |
|---|---|
| `hi` | handshake (`version`) |
| `live` / `getUpdate` | live/sync (`sequenceNumber`) |
| `play`, `pause` | playback start/pause |
| `seek` | seek (`startTimestamp`, `endTimestamp`, `smoothScrub`, `noGapPlayback`, `timelapseSqueezed`) |
| `set_playback_speed` | speed (`speed`) |
| `switch_feed` | switch feed (`angle`, `autoplay`) |
| `standby` | standby |
| `share_keys` | E2EE key sharing (`keysPayload`, `grecoVersion`) |

`SnapshotLoadingApi` / `SnapshotsApi` (`GET next/{device_id}`) handle event snapshots; the legacy `GET dings/{id}/share/play` returns a shareable recording URL.

---

## 6. Motion events & dings — confidence: high (API), medium (push payload)

- **Push channel:** signal messages `push_event`, `push_error`, `push_subscription_ack`, `push_update_ack`, driven by `PushSubscribe` / `PushUnsubscribe` / `PushUpdate` / `PushHeartbeat` client messages. `PushSubscribeFilters` uses `doorbotIds` / `filter_identifiers` and `requested_notifications`; `push_event` carries `subscription_id`, `event`, `deviceData`, `ingestion_time_ms`. The marshaller logs `Unsupported push_event type=` and `Failed to parse push event message`.
- **Polling / history:** the EVM endpoints in §3.4. `evm/v2/activity_feed/events` is the activity feed; `evm/v2|v3/history/events` is the paginated history with `cv` motion/person metadata.
- **Legacy dings:** `ClientsApi` `dings/{id}` family; `ActivityFeedApi` `evm/v2/dings/{event_id}` (GET/DELETE/favorite).
- **Motion settings:** `MotionClientsApi` (`GET doorbots/{deviceId}/motion_sensitivity_profile`, `PUT doorbots/{deviceId}`, `POST motions_subscribe/unsubscribe`), `SnoozeApi` (`POST/DELETE doorbots/{device_id}/snooze/{event_type}`), `NotificationSettingsApi` (`GET/POST/DELETE doorbots/{deviceId}/event_types[/{eventType}]`).
- **Event types** seen in strings include motion/ding/person-detection variants (`cv_motion_detection`, `person_detection`, `event_motion_detection`, `motion_detection`).

---

## 7. Motion tagging (bounding-box overlays) — confidence: medium

- Overlay data is a **protobuf on the WebRTC data channel**, not REST: `com.ringapp.streaming.service.webrtc.data.renderingcontrol.model.RenderingControlMessages` with messages `RenderingControl`, `RenderInfo`, `VideoInfo`, `ViewPort`, `Resolution`, `Coordinate`, `AztBoundingBox`, `AztState`, `Invalid`, `MessageType`.
- `RenderingControlRepositoryImpl` / `DataMappersKt` map between protobuf and domain models (`Azt`, `TimedRadarObject`, `IncomingMessage.Valid` carrying `radarData` and `VideoInfo`). `RenderingControlSdpExchangeHelperImpl` negotiates a dedicated data channel.
- Proto field names present in the corpus: `azt_bounding_box`, `azt_state`, `coordinate_top_left`, `coordinate_bottom_right`, `confidence`, `show_radar_data`, `client_device_settings`, `message_type`, `resolution`, `radar_data`, `person_detected`, `person_detection_outline`. The Java API exposes `aztBoundingBoxOrNull`, `coordinateTopLeftOrNull`, `coordinateBottomRightOrNull`, `AztBoundingBoxOrBuilder`, `AztStateOrBuilder`.
- Client toggle: `ClientDeviceSettings(show_radar_data=…)`; the capability/trait is `cv_person_detection` (`CVPersonDetection`) and the UI lives under `video_settings`/`person_detection`. The event-side flag is `OrchestratorCvResponse.personDetected`.
- **Caveat:** exact protobuf field numbers and the wire framing of the data channel were not recovered by static analysis alone; a runtime capture is needed to confirm byte layout.

---

## 8. E2EE (relevant to playback) — confidence: medium

- Amazon **Cerulean** SDK (`com.amazon.cerulean.*`: CAS/CIS/CKMS clients, keychains, AEKs, content-security public keys) plus native `libceruleancore.so`, `libsecureframe.so`, `libamzn_greco_core.so`.
- Content-encryption keys: `auth/mobile/encryptionkey`, `account/e2ee/`, `account_settings/e2ee`, `/auth/mobile/encryptionkey`, `CredentialRequestProvider`, `EncryptionCekManagerImpl`, `KeyDelivery` APIs.
- Media responses carry `isE2ee`; encrypted playback uses `WebRtcEncrypted` / `CloudWebRtcEncrypted` session types and the `share_keys` data-channel command.
- Local device setup exposes an AP-mode HTTP surface at `http://192.168.240.1/ring/...` and `/gainspan/...`: `system/config/app_info`, `system/config/network`, `system/config/reg_domain`, `system/prov/ap_list`, `e2ee/status`, `e2ee/certificate`, `e2ee/enrollment`.

---

## 9. Relevance to Lookout / suggested next steps

> The high-level points below are general. For the concrete, file-by-file plan against the
> `phase/10-fixes` branch, see **§10**.

1. **Auth is the gating item.** Lookout would need to reproduce the OAuth password grant (or a scoped/PKCE grant) with `client_id=ring_official_android`; there is no HMAC/dynamic-signature scheme like Wyze's `wcsa`, which simplifies things, but 2FA and rate limiting/backoff must be handled.
2. **Live view is WebRTC, not a simple stream URL.** A Lookout integration needs a full WebRTC peer connection plus the SignalR signaling client (`X-Sig-*` headers, `type`/`body` envelopes, ICE/SDP state machine). The endpoint host is AWS-based (`api.prod.signalling.ring.devices.a2z.com`), not `api.ring.com`.
3. **Recordings are event→media→WebRTC.** Query `evm/v3/history/events`, read `cv`/media fields, then open a `playback` signaling session and issue data-channel commands. HLS/DASH URLs exist in the model but are not necessarily sufficient without E2EE keys.
4. **Motion events can be consumed either via push (`push_event`) or by polling the EVM history endpoints.** The latter is far easier for a self-hosted integration.
5. **Motion tagging overlay is a protobuf data-channel side channel** (`RenderingControlMessages`); reproducing it would require recovering the exact proto schema/field numbers (best done with a runtime capture), and is likely low priority for Lookout.
6. **Dynamic validation is the logical next phase** (if in scope): an instrumented client / TLS intercept to capture real signaling frames and media metadata would resolve the remaining unknowns (exact `type` strings' casing, proto field numbers, token scopes, E2EE key exchange). This doc deliberately stops at static analysis.

---

## 10. Adding Ring into Lookout (base: `phase/10-fixes`)

This section maps the findings above onto the actual Lookout codebase as it exists on the
`phase/10-fixes` branch (full branch name
`phase/10-fixes-phase-10-fixes-native-wyze-ring-ingest-thumbnails-live-stream-and-detection-parity`,
tip `738b968`, clean tree, inspected 2026-11-19). It is a **change map**, not a patch: all source
changes in the repo must still go through the normal CodeLoop `IMPLEMENT` stage and the
implementer subagent (per `AGENTS.md`). These notes themselves live only in `/tmp/opencode/ring/`.

### 10.1 What already exists on this branch (verified)

| Concern | Current implementation | Reference |
|---|---|---|
| Config | `RingConfig`: `auth_token`, `api_url` (default `https://api.ring.com`), `oauth_url` (default `https://oauth.ring.com`), `refresh_token`, `email`, `session_id`, `two_factor_auth_required`, `native_ingestion`, `native_device_ids`, `timestamp_overlay` | `src/lookout/config/schema.py:254` |
| Auth/token envelope | Base64 outer token decoded to `{rt, hid, pnc}` by `decode_ring_token()` / `encode_ring_token()`; `Auth(f"lookout/{version}", token={"refresh_token": inner["rt"]}, hardware_id=inner["hid"])` + `async_refresh_tokens()` from `ring_doorbell`; refreshed `rt` is re-encoded and persisted, `pnc` preserved | `adapters/ring_native.py:41,56,164,196` |
| Discovery | `ring.async_update_devices()` → `_video_devices()` → `_map_device()` (id, name, model, kind, connection_status, `native_provider="ring"`, `stream_source`) | `ring_native.py:226–280` |
| Live source | `build_stream_source()` → `ring:?camera_id={device_api_id}&device_id={device_id}&refresh_token={urlquoted token}`, optional `&snapshot`; consumed by embedded go2rtc (ring-mqtt / `ring-client-api` underneath) | `ring_native.py:282`, `native_stream_manager.py` |
| Media capture | `NativeStreamManager.ensure_stream/frame/clip` + `ffmpeg:{id}#video=h264` transcode (`{id}_h264`); snapshot-polling fallback for H.265 devices (commits `5d3eaeb`, `7ff2faa`, reverted by `738b968`) | `ring_native.py:376–433`, `native_stream_manager.py:320–371` |
| Events | `device.async_history(limit=20)` → `_parse_datetime(created_at)`, `cv_properties.detection_type`, `kind`, `id`/`ding_id`, `recording`, `snapshot_url` → payload → `NativeEventPoller` (15 s tick, dedup `(provider,event_id)`) → `ingest_event()` → `CameraEvent` | `ring_native.py:441–521`, `native_event_poller.py:21–188` |
| Provider surface | `providers/ring/{driver,discovery,sync,routes}.py` + `manifest.json`; auto-discovered by `ProviderRegistry`; sidecar `data/sidecars/ring-mqtt.env` (mode `0600`); **stub**: `verify_2fa()` currently returns `mock-ringtoken-12345` | `providers/ring/driver.py`, `sync.py`, `registry.py:36` |
| Wiring | One shared `NativeStreamManager` + `RingNativeAdapter` constructed in app startup and attached to `app.state.ring_native`; PTZ service uses `NativeAwareRecorder` with ring/wyze adapters | `adapters/web/app.py:491–543` |
| Tests | `tests/test_native_ring_ingestion.py` (token round-trip, stream source, snapshot/clip, discovery, history mapping, `ingest_event`, epoch parsing), `test_native_event_poller.py`, `test_native_stream_manager.py` | `tests/` |

**Longer-running work already planned:** issue #64 (`artifacts/plans/64-…/implementation_plan.md`)
replaces the mock 2FA with real issuance (`RingTokenIssuer` defaulting to a Node 20
`ring-auth-cli` PTY), adds credential probing/rotation/logout, atomic `0600` config writes,
poller health, and Node provisioning in the Dockerfile. The notes below are designed to
complement that plan, not duplicate it.

### 10.2 Where the reverse-engineered facts plug in

| Finding (section) | Current code | Proposed use |
|---|---|---|
| OAuth token endpoint, grants, fields (§3.1) | `RingNativeAdapter._ring()` delegates token refresh to `ring_doorbell` | Add a Python `RingTokenIssuer` implementation of #64's protocol: `POST {oauth_url}/oauth/token` with `client_id=ring_official_android`, `grant_type=PASSWORD`/`REFRESH_TOKEN`, parse `access_token`/`refresh_token`/`expires_in`/`created_at` → re-encode into the `{rt,hid,pnc}` envelope. Avoids the Node/`ring-client-api` runtime dependency. |
| 2FA endpoints (§3.2) | `RingProviderDriver.verify_2fa()` stub | `POST /oauth/v2/verify` and `/oauth/v2/verify_otp` (legacy `2fa/verify_code`); observed headers `2fa-Code`, `2fa-Support`, `mfa-support-version`, `totp-version`. |
| REST bases + EVM endpoints (§3.3–3.4) | `RingConfig.api_url` currently only used by the legacy `RingAdapter` (`clients_api/...`) | Add `RingEvmClient` using `api_url` + `evm/v3/history/events`, `evm/v2/history/events/{id}`, `evm/v2/timeline/devices/{id}`; use as primary event source with `device.async_history()` as fallback. |
| Event/media models (§5.1) | `poll_events()` reads `created_at`, `kind`, `cv_properties`, `recording`, `snapshot_url` | Map `eventId`/`startTime`/`eventType`/`cv`/media objects into the existing payload keys; put `url`, `manifestId`, `fileType`, `isE2ee`, `preRollDuration` into `metadata` and `CameraEvent.media_uri`. |
| WebRTC/SignalR signaling (§4) | go2rtc `ring:` source (bridge) | Optional native live path: SignalR WS + `live_view`/`playback` messages, SDP/ICE, feed media into go2rtc; keep `ring:` source and snapshot polling as fallbacks. |
| Data-channel commands (§5.2) | No playback control | Map event-player seek/pause/speed to `seek`, `pause`, `play`, `set_playback_speed`, `switch_feed`; `share_keys` for E2EE. |
| Rendering-control proto (§7) | `DetectionMetrics.bounding_boxes` exists but is populated by Lookout's own inference | Optionally map `AztBoundingBox` → `BoundingBox(label="person", confidence, x_min/y_min/x_max/y_max)` and record `InferenceProvenance(provider="ring", model_name="cv_person_detection")`. |
| Cerulean E2EE (§8) | No E2EE handling | Gate with `isE2ee`: initially skip media download / prefer live-only; key delivery is a separate project. |
| FCM/ADM push payloads (§11) | `NativeEventPoller` polls EVM; no FCM consumer | Typed, offline parser for captured payloads + channel/category mapping (#85); keep EVM polling as the authoritative event source; no FCM SDK dependency. |

### 10.3 Suggested staging

**Stage A — EVM history enrichment (low risk, recommended first).**
Add `src/lookout/adapters/ring_evm.py` (or private methods in `ring_native.py`) with an async client
for `POST {api_url}/evm/v3/history/events` and `GET .../history/events/{eventId}` using the same
bearer token from `RingAuth`. Extend `RingNativeAdapter.poll_events()` to merge EVM results with
`device.async_history()` (dedupe on `event_id`), and extend `payload["metadata"]` with the media
and CV fields above. Keep the existing keys untouched so pinned tests
(`test_poll_events_maps_real_history_fields`, `test_poll_events_filters_by_since`,
`test_ingest_event_builds_camera_event_from_payload`) keep passing. Add an opt-in flag to
`RingConfig` (e.g. `evm_history: bool = False`) so rollout is reversible. **Unknown:** the exact
EVM request body/pagination — keep the raw response shape in `metadata` during the first
integration and confirm against a real account before relying on it.

**Stage B — real auth via OAuth (land together with issue #64).**
Implement the `RingTokenIssuer` protocol from #64 as `RingOAuthIssuer` using §3.1/§3.2. Reuse
`decode_ring_token()`/`encode_ring_token()` and the plan's `pnc`-preserving rotation rule
(AC6). Mirror `RefreshAuthenticator` behaviour: retry-after/backoff on `429`, clear errors on
`error`/`error_description`, never log token material, keep config and sidecar files `0600`.
Wire through the shared `auth_service` in all three route families (direct provider route,
`/api/auth/*`, and the generic `/api/providers/*` binder) — #64 already requires this.

**Stage C — native WebRTC live/playback (highest effort, separate issue).**
Introduce a `ring_webrtc.py` adapter: SignalR client with `X-Sig-*` headers (§4.1), JSON
`{"type","body"}` envelopes (§4.2), SDP/ICE exchange (§4.4), then hand the WebRTC media track to
go2rtc via a producer or relay. Feature-flag it (e.g. `webrtc_live: bool = False`) and retain the
current `ring:` source + snapshot fallback. Add data-channel playback commands only after the
live path is stable; use `share_keys` and `isE2ee` to detect encrypted sessions that cannot yet be
decoded. This is the only stage that changes the media plane, and the one most likely to need a
runtime capture to resolve exact message casing, encryption negotiation, and protobuf framing.

### 10.4 Concrete data mapping for `poll_events()` / `ingest_event()`

```
Ring EVM field                    Lookout payload / CameraEvent
─────────────────────────────────────────────────────────────────────
eventId                           payload["event_id"] → CameraEvent.id = f"ring-{event_id}"
sourceId / device.id              payload["camera_id"]  (match CameraConfig.native_device_id)
startTime (ISO)                   payload["timestamp"] → CameraEvent.timestamp via _parse_datetime
eventType                         payload["kind"] → _category_from() (existing mapping)
cv.detectionType / detectionDetails  payload["detection_type"] → category + summary text
cv.personDetected / otherMotion   metadata["cv_properties"] (keep as dict)
cv.detectionDetails[*].confidence DetectionMetrics.confidence (max/first — document choice)
AztBoundingBox (if available)     DetectionMetrics.bounding_boxes → BoundingBox(...) + provenance
media[0].url                      metadata["recording"] + CameraEvent.media_uri
media[0].manifestId/fileType/     metadata["media"] (preserve verbatim for debugging)
       fileFamily/preRollDuration/playbackDuration/isE2ee
visualization/thumbnail URL       metadata["snapshot_url"] (confirm field name at runtime)
```

Keep `provider="ring"` and the dedup key `(provider, event_id)` unchanged — `NativeEventPoller`
already uses them, so no poller change is required for Stage A. `CameraEvent` already has
`media_uri`, `snapshot_uri`, `metadata`, and `DetectionMetrics.bounding_boxes`
(`domain/models.py:37–93`).

### 10.5 Auth wiring details

- The `{rt, hid, pnc}` envelope is **client-side Ring token metadata** (the RE work only confirms
  the underlying OAuth fields: `access_token`, `refresh_token`, `expires_in`, `created_at`,
  `scope`). Preserve `pnc` exactly on rotation as #64 AC6 requires.
- Direct token refresh: `POST https://oauth.ring.com/oauth/token` with
  `client_id=ring_official_android`, `grant_type=REFRESH_TOKEN`, `refresh_token=<rt>`, and
  `scope` if the account token carries one; parse `OAuthTokenResponse` and update the envelope's
  `rt` (and `hid` if returned). Watch for the same 401/429/token-revoked states handled by
  `RefreshAuthenticator` / `RevokedAuthenticator`.
- Initial login uses grant type `PASSWORD` (username/password), with 2FA via
  `oauth/v2/verify` + `/oauth/v2/verify_otp`. The manifest already declares `sms`, `email`, `totp`
  as supported 2FA methods.
- `RingConfig.oauth_url` already defaults to `https://oauth.ring.com` — no schema change needed
  for Stage B beyond the optional feature flags in §10.3.

### 10.6 Streaming wiring details

- Do **not** replace the go2rtc `ring:` source preemptively: on this branch it is the mechanism
  that yields true H.264 live video and the snapshot fallback exists for H.265 devices. The
  RE-derived WebRTC path is valuable when the bridge is unavailable, for E2EE sessions, or for
  playback control — introduce it behind a flag with a documented fallback chain:
  `native WebRTC → ring: go2rtc source → snapshot polling`.
- If adding a SignalR client, use the `X-Sig-API-Version: 4.1`, `X-Sig-Auth-Type`,
  `X-Sig-Client-ID`, `X-App-Correlation-ID` headers and the same OAuth bearer token
  (`SignalingAuthProviderImpl` uses `RingAuth.getAccessToken()`).
- E2EE: `OrchestratorCloudMediaResponse.isE2ee` is the cheap signal to skip cloud media download;
  Cerulean/CKMS key delivery is a distinct subsystem and should remain out of scope for the first
  integration increment.

### 10.7 Tests and quality gates

- **Preserve pinned behaviour** in `tests/test_native_ring_ingestion.py`: `test_token_roundtrip`,
  `test_build_stream_source_uses_full_encoded_token`, snapshot/clip writers,
  `test_discover_and_test_connection_use_real_device_list`,
  `test_poll_events_maps_real_history_fields`, `test_poll_events_filters_by_since`,
  `test_ingest_event_requires_real_payload`, `test_ingest_event_builds_camera_event_from_payload`,
  `test_parse_datetime_handles_epoch_millis`.
- **Add** (offline, no credentials/network): EVM response → payload mapping; OAuth request/response
  shape and `pnc` preservation with a fake `httpx.AsyncClient`; `AztBoundingBox` → `BoundingBox`
  mapping with frozen proto fixtures; SignalR message serialize/deserialize fixtures.
- **Quality commands used on this branch** (from the phase plans):
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
  `uv run pytest -q tests/test_native_ring_ingestion.py tests/test_native_event_poller.py tests/test_native_stream_manager.py tests/test_web_api.py`, then full `uv run pytest -q`.

### 10.8 Risks / open questions before implementation

1. Exact `evm/v3/history/events` request body, pagination, and time-field format (`startTime` is a
   string in the model; whether ISO-8601 or epoch varies by endpoint needs runtime confirmation).
2. Whether the legacy `dings`/`clients_api` endpoints used by `ring.py` still work for all account
   types, or should be retired in favour of EVM.
3. Exact `X-Sig-Auth-Type` value and token scope expected by the signaling endpoint.
4. Protobuf field numbers/framing for `RenderingControlMessages`, and the precise conditions under
   which the camera emits `AztBoundingBox`.
5. Whether a WebRTC media track can be handed to the embedded go2rtc without a new native
   dependency; if not, the bridge remains the practical live path.
6. E2EE key delivery (Cerulean) is not implemented by either Lookout or these notes.
7. The 2FA/token flows were observed statically only — validate with a throwaway account before
   relying on them for authentication, and keep token material out of logs/DOM per #64 AC5.

### 10.9 Recommended sequencing

1. **Stage A** (EVM history + event mapping) as its own CodeLoop issue on top of `phase/10-fixes`;
   it is additive, testable offline, and immediately improves detection metadata/thumbnails.
2. **Fold Stage B into issue #64** so there is one auth flow and one set of route changes.
3. **Stage C** as a separate follow-up issue with a runtime capture task first, because it is the
   only stage whose wire details remain medium-confidence.

---

## 11. Push notifications (FCM / ADM) — focused pass (added 2026-11-19)

**Confidence:** high for class/method structure, field names, and version strings; **medium** for
version-to-format mapping and channel selection semantics (no real push payload was captured).
Source: `dump_push_*.txt` in `/tmp/opencode/ring/` (targeted disassembly of classes3.dex / classes8.dex).

### 11.1 Two distinct push systems

Do not conflate these:

| System | Transport | Where documented |
|---|---|---|
| In-app event push | SignalR WebSocket, `push_event` / `push_subscribe` messages | §6 |
| OS push notifications | Firebase Cloud Messaging (FCM) and Amazon Device Messaging (ADM) | this section |

The OS path is `PushNotificationSDK` → `PushServiceConsumer` → `PushNotificationParser` →
`PushNotificationManagerImpl` / `RingNotificationMapper`. `PushTransport` is an enum with exactly
`FCM` and `ADM`. `PushNotificationParser` lazily constructs an `FcmPushParser`
(kotlinx.serialization `JsonElement` / `JsonObject` → flat `LinkedHashMap`) and an `AdmPushParser`
(`Map<String,String>` in, `String` lookups out). Both feeds converge on a `PushData` model.

### 11.2 Payload shapes and versioning

`PushData` fields: `version`, `androidConfig` (`AndroidConfig`), `analyticsData` (`AnalyticsData`),
`imgData` (`ImgData`), `video` (`VideoData`), `navigation` (`Navigation`), `data` (`ExtraData`).

Two representations are parsed (both observed in the same parser class):

- **Flat / namespaced keys** — `version`, `android_config:title|body|tag|channel|category|group_key|expandable|expandable_text|notification_count|ui_style`,
  `analytics:server_id|server_correlation_id|subcategory|minicategory|sent_at|triggered_at|referring_item_type|referring_item_id`,
  `img:url|snapshot_url|snapshot_uuid`, `video:animation_url|animation_base_url`,
  `navigation:deeplink|external|web`, `data:` + `id`; keys are split on `":"`.
- **Nested objects** — top-level `version`, `android_config`, `analytics`, `img`, `video`, `data`,
  `navigation` (the typed-model path). Missing/invalid values produce explicit errors:
  `Missing version value`, `Failed to parse version`, `Missing analytics value`,
  `Failed to parse analytics`, `Missing android_config value`, `Failed to parse android_config`.

`PayloadVersion` enum: `TWO`, `TWO_PW_SUPPORT`, `TWO_PHASE_2`, `TWO_PHASE_3`, `TWO_PHASE_4`,
`TWO_PHASE_5`, with string values `2.0.0`, `2.0.1`, `2.1.0`, `2.2.0`, `2.3.0`, `2.4.0`.
The precise version→parser mapping was not reconstructed.

Typed sub-models:

| Model | Fields |
|---|---|
| `AndroidConfig` | `title`, `body`, `category`, `tag`, `notificationChannel`, `groupKey`, `expandable`, `expandableText`, `notificationCount`, `uiStyle` |
| `AnalyticsData` | `serverId`, `serverCorrelationId`, `subcategory`, `miniCategory`, `sentAt`, `triggeredAt`, `referringItemType`, `referringItemId` |
| `ImgData` | `url`, `snapshotUrl`, `snapshotUuid` |
| `VideoData` | `animationUrl`, `animationBaseUrl` |
| `Navigation` | `deeplink`, `external`, `web` |
| `UiStyle` | enum `SILENT`, `FULLSCREEN` (wire strings `silent`, `fullscreen`) |
| `ExtraData`, `DevicePayload`, `LocationPayload` | generic `Map` payloads (`id`, `kind`, `name`, `product_name`, `url`, `url_title`, `zid`, `device`, `location`) |

### 11.3 Notification channels

`RingNotificationChannel` / its `Id` type has ~24 channel variants: `Alarm`, `AlarmCritical`,
`Call`, `Community`, `DailyHighlights`, `DeviceStatus`, `DeviceStatusCritical`, `Ding`,
`FirstPartyApps`, `Geofence`, `KeyDelivery`, `MailboxSensor`, `MemorableMoments`, `Miscellaneous`,
`Motion`, `Other`, `SearchParty`, `SearchPartyCritical`, `SensorAlert`, `SharedUserAccess`,
`SmartLighting`, `Unlock`, `VehicleRoutine`, `VehicleSecurity`, `Watchwright`.
Each channel carries `channelId`, `importance`, `sound`, `vibrate`, `channelNameRes`,
`channelDescriptionRes`. `NotificationChannelsManager` creates channels via Android's
`NotificationManager`, logs `Failed to create channel with ID …`, falls back to a default channel,
and tracks a `channels_migration_flag` in `SharedPreferences`. `PN category disabled: ` is logged
when a category is off; `android_config:channel` selects the target channel. Observed channel-id
strings include `RING_ALARM_NOTIFICATION_CHANNEL` and `RING_ALARM_CRITICAL_NOTIFICATION_CHANNEL`.
Tap/dismiss plumbing: `PushNotificationManagerImpl.handleTapActions`, `TapTrackedPendingIntentFactory`,
`DeleteIntentProvider`, `NotificationClearBroadcastReceiverV2`; `android.settings.CHANNEL_NOTIFICATION_SETTINGS`
is used to deep-link into channel settings.

### 11.4 Token registration (not a Ring endpoint)

`/auth/tiv/push_notification?token=` is constructed inside **Amazon Identity** SDK classes
`com.amazon.identity.auth.device.q7` and `.x7` (Amazon "Panda" backend), not in Ring's own API
interfaces. Requests use `GET`/`POST`, headers `x-amzn-identity-auth-domain`,
`x-amzn-identity-retry-attempt`, `Cookie`, `Accept`, `Content-Type: application/json`, and have
explicit 401/backoff handling. `PushServiceConsumer.onTokenUpdated` reacts to FCM token rotation.
Practical consequence for Lookout: the FCM registration token is device/app-scoped and is **not**
something a self-hosted integration can reuse; notification consumption should be via EVM polling
(§6 / #77) rather than FCM.

### 11.5 Media and UI behaviour

`img:snapshot_url` / `img:url` and `video:animation_url` feed
`PushNotificationBuilder.loadMediaIfNeeded()` — notification thumbnails/animations are downloaded
and attached to the system notification. `android_config:ui_style` controls `SILENT` vs `FULLSCREEN`.
Analytics events (`AppPushNotification*Event`) track received/tapped/dismissed/processing results;
these are telemetry, not protocol surface.

### 11.6 Lookout integration notes

- A dedicated parser issue is filed as **#85** (`Ring push notifications — FCM/ADM payload parser + channel/category mapping + token-registration notes`). It is research + typed-fixture mapping: **do not add an FCM SDK dependency** to Lookout.
- Useful mapping when a captured payload exists: `img:snapshot_url` → `CameraEvent.snapshot_uri`, `video:animation_url` → a media attachment, `analytics:server_id` → dedup/reference id, `android_config:category` → `EventCategory` hint. All values stay in `CameraEvent.metadata` until independently confirmed.
- Channels/categories are UI concerns; Lookout should map them to its existing notification/egress configuration (ntfy/Home Assistant), not recreate Android channels.
- Keep the EVM polling path (#77) as the authoritative event source; FCM notes are for parity/debugging only.

### 11.7 Caveats / open questions

1. No real FCM/ADM payload was captured; exact `data:` extra fields and their relation to EVM `eventId` are unverified.
2. Version→parser mapping (flat vs nested) is inferred from two parser methods, not from a captured sample.
3. How `android_config:category` maps to the ~24 channel ids needs an on-device capture.
4. Amazon Identity token registration details are SDK-level; reproducing them is neither needed nor desirable for Lookout.
5. No code from the app was copied — only names, paths, fields, and observed literals.

---

## Appendix A — Artifact index (`/tmp/opencode/ring/`)

| File | Contents |
|---|---|
| `ring.apkm`, `base.apk`, `split_config.arm64_v8a.apk`, `info.json` | Source APK material |
| `.venv/` | Fresh androguard 4.1.4 environment |
| `lib.py`, `classlist.py`, `strings.py`, `endpoints.py`, `annots.py`, `dumpall.py`, `dump2.py`, `dumpmethod.py`, `findstr.py`, `findclassstr.py` | Analysis scripts |
| `all_classes.txt` (184k lines), `all_strings.txt` (~1M), `strings_clean.txt` (~950k) | Raw inventories |
| `all_endpoints.txt` (919) | All Retrofit HTTP endpoint annotations, by class |
| `dump_auth_*.txt`, `dump_signaling_*.txt`, `dump_playback_*.txt`, `dump_event_models.txt`, `dump_rendering_control.txt`, `dump_snapshot_api.txt`, `dump_datachannel.txt` | Targeted class disassemblies |
| `dump_push_notifications.txt`, `dump_push_core.txt`, `dump_push_models.txt`, `dump_push_channels_token.txt`, `dump_push_channel_ids.txt`, `dump_push_fcm_adm.txt` | Focused FCM/ADM push-notification pass (§11) |

## Appendix B — Confidence legend

- **High** — literal strings/annotations/method signatures directly observed in disassembly.
- **Medium** — inferred from class structure and adjacent literals; exact wire format or semantics not fully resolved.
- **Low** (not used above) — speculation.
