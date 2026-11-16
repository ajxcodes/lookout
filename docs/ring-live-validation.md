# Ring Live View Validation Runbook

## Root cause summary

1. **Signaling proxied to the wrong go2rtc**: WebRTC signaling (`/api/ws`, `/api/webrtc`) went to the default go2rtc, while Ring streams live in the native-stream go2rtc instance.
2. **No lazy stream registration**: Ring streams were not registered in go2rtc until something else asked for them, so signaling hit an unknown stream.
3. **Stale cached stream**: a previously registered stream with an expired Ring source/token was reused instead of being refreshed.

The fix resolves the correct go2rtc target per camera, registers the stream lazily before proxying, and refreshes stale registrations.

## Build and deploy

```bash
TAG=lookout:ring-live-$(date +%Y%m%d%H%M%S)
podman build -t "$TAG" .
podman stop lookout && podman rm lookout
# Re-use the same data volume so config and tokens are preserved
podman run -d --name lookout -v lookout-data:/data -p 8000:8000 "$TAG"
```

## Config fields

- Ring camera entry: `type: ring`, `device_id` (Ring device id), plus the Ring refresh token in the Ring integration settings.
- Native stream go2rtc: `native_streams.api_url` (and its WebRTC/RTSP ports) must point at the instance Lookout manages.
- Never put tokens in logs or the PR; diagnostics redacts them.

## Browser checks (DevTools console on the live page)

```js
const pc = [...document.querySelectorAll('video')][0];
// Receiver track (if you have the RTCPeerConnection handle)
pc.getReceivers?.()[0]?.track;           // readyState should be "live"
const v = document.querySelector('video');
v.readyState;                            // expect 4 (HAVE_ENOUGH_DATA)
const t0 = v.currentTime; setTimeout(() => console.log(v.currentTime - t0), 2000); // > 0
```

## Diagnostics endpoint

```bash
curl -s http://localhost:8000/api/cameras/<camera_id>/live/diagnostics | jq
```

Fields: `stream_registered`, `source_scheme` (scheme only, e.g. `ring`), `source` (redacted full source), `codecs` (video only), `h264`, `go2rtc_target`. Expect `stream_registered: true` and `h264: true`.

## go2rtc logs

```bash
podman logs --tail 200 -f lookout 2>&1 | grep -i -E 'go2rtc|ring|webrtc'
```

## Reconnect check

1. Open live view and confirm video plays.
2. Close the tab (or toggle the network), wait ~30s, reopen.
3. Confirm video resumes, `currentTime` advances, and diagnostics still shows `stream_registered: true`.

## Ring vs Wyze comparison

| Aspect | Ring | Wyze |
| --- | --- | --- |
| go2rtc instance | Native-stream go2rtc | Existing/bridge go2rtc |
| Registration | Lazy, on first signaling | Pre-configured |
| Source | `ring:?device_id=...&refresh_token=...` (redacted) | RTSP/bridge URL |
| Codec check | `h264` in diagnostics | `h264` in diagnostics |
| Result | ☐ | ☐ |

## Snapshot fallback check

Make the live stream unavailable (e.g. stop the Ring source) and confirm the UI falls back to a snapshot image instead of a blank player.

## PR description template

```
## Summary
Fix Ring live view: route signaling to the correct go2rtc, register streams lazily, refresh stale streams.

## Validation
- [ ] Built image <tag>, redeployed keeping data volume
- [ ] video.readyState == 4, currentTime advancing
- [ ] diagnostics: stream_registered/h264 true, no secrets exposed
- [ ] Reconnect OK
- [ ] Snapshot fallback OK
- [ ] Wyze unaffected
```
