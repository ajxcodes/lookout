# Reverse-engineering protocol notes (durable copies)

These notes are **protocol facts only** — hostnames, endpoint paths, message/field names, JSON/protobuf shapes, and confidence levels. No implementation code, credentials, account data, tokens, or captured request bodies are included. They exist to support interoperability research for this personal, self-hosted project.

## Canonical paths

| Vendor | File | Size |
|---|---|---|
| Ring | `docs/reverse-engineering/ring-protocol-notes.md` | 516 lines |
| Wyze | `docs/reverse-engineering/wyze-protocol-notes.md` | 431 lines |

Use these paths instead of the old `/tmp/opencode/...` and gitignored `artifacts/reverse-engineering/...` locations referenced in issues #67–#86.

## Provenance

### Ring

- **Original:** generated 2026-11-19 by static analysis of Ring app `com.ringapp` v3.117.0 (APKM arm64-v8a, Android 10+) with androguard 4.1.4. No runtime capture and no credentials were used; wire-level details carry explicit confidence markers.
- **Original path:** `/tmp/opencode/ring/ring-protocol-notes.md` (deleted when the temp directory was cleared).
- **Recovery:** reconstructed from the OpenCode session **"Ring APK reverse engineering"** (`ses_e0b5a6643ffeETZEgvZh0XOA70`). The original `write` tool call and all nine subsequent `edit` tool calls were replayed in sequence from the session message log; the result was verified against the section citations used in issues #74–#86 (§3 auth/API, §4 live view, §5 recordings, §6 events, §7 motion tagging, §8 E2EE, §10 Lookout integration, §11 FCM/ADM push).
- **Recovered:** 2026-11-26.

### Wyze

- **Original:** generated 2026-11-19 from the official Wyze app v3.25.1.1037 reverse-engineering pass; cited by issues #67–#73 and #85.
- **Old path:** `artifacts/reverse-engineering/wyze-protocol-notes.md` — preserved verbatim here, because `artifacts/` is gitignored and does not survive cleanup.
- **Recovered:** 2026-11-26.

## Caveats

- The auxiliary artifacts named in the notes (`all_endpoints.txt`, `dump_*.txt`, captured payloads) were disposable temp files and are **not** included. Where the notes cite them, the cited facts are summarised in the notes themselves.
- Several phase-11 issues require **on-device capture before implementation** (e.g. #68 dynamic signature, #74 token issuance, #78 protobuf frames). These notes do not substitute for that capture; they define what must be confirmed.
- Before implementing, re-check that the cited app version still matches current firmware/app releases.
