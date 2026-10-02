# Pinned Music provider contract research

Research slice for #31 and #25, inspected 2026-10-03. This records upstream source
contracts, not deployed-version equivalence or real-runtime acceptance. Music
capabilities/editors remain disabled until those independent entry gates pass.

## Upstream source identity

The reviewed candidate is [ServeurpersoCom/yue2.cpp at
11c1ecb084329200e22fcb286e252b847442ea5c](https://github.com/ServeurpersoCom/yue2.cpp/tree/11c1ecb084329200e22fcb286e252b847442ea5c).
Its executable/model naming corresponds to the recorded installation pattern;
that correspondence does not establish the deployed commit or binary identity.
Private deployment paths and host configuration are intentionally not reproduced.

| Source at the pinned commit | Contract authority |
| --- | --- |
| [HTTP server](https://github.com/ServeurpersoCom/yue2.cpp/blob/11c1ecb084329200e22fcb286e252b847442ea5c/tools/yue-server.cpp) | Routing, job retention, submission, status, results and cancellation |
| [Request type](https://github.com/ServeurpersoCom/yue2.cpp/blob/11c1ecb084329200e22fcb286e252b847442ea5c/src/request.h) and [parser](https://github.com/ServeurpersoCom/yue2.cpp/blob/11c1ecb084329200e22fcb286e252b847442ea5c/src/request.cpp) | Consumed fields, defaulting, serialization and replay identity |
| [Pipeline](https://github.com/ServeurpersoCom/yue2.cpp/blob/11c1ecb084329200e22fcb286e252b847442ea5c/src/pipeline.h) | Symbolic/semantic/audio stages and finite token budget |
| [Transcription CLI](https://github.com/ServeurpersoCom/yue2.cpp/blob/11c1ecb084329200e22fcb286e252b847442ea5c/tools/yue-transcribe.cpp) | Native SheetSage2 executable/argv/output contract |
| [Browser client](https://github.com/ServeurpersoCom/yue2.cpp/blob/11c1ecb084329200e22fcb286e252b847442ea5c/tools/webui/src/lib/api.ts) | Consumer cross-check; server source wins when comments differ |

## Verified HTTP shape

| Request | Observed source behavior | Generation adapter obligation |
| --- | --- | --- |
| `GET /health` | Liveness JSON only | Do not treat as model/profile/input qualification |
| `GET /props` | Version, model/VAE locators, rates, context and request defaults | Bound/validate internally; never publish raw filesystem locators |
| `POST /synth` | JSON request validated, random job ID allocated, worker enqueued; returns ID | Claim/journal ordinary reservation before handoff; lost response is unknown, never resubmit |
| `GET /job?id=...` | Status: running, done, failed or cancelled; missing/evicted ID is 404 | Preserve unknown ownership on missing ID; running includes queued work |
| `GET /job?id=...&result=1` | Result only when done; otherwise 404 | Use known status plus bounded result receipt; 404 alone proves neither stopped work nor readiness |
| `POST /job?id=...&cancel=1` | Sets per-job flag and returns current status | Requested cancellation is not settled cancellation/resource release |
| `POST /transcribe` | Optional multipart audio route, registered only with a configured transcriber | Never advertise from the executable/model name alone; managed-input snapshot only |

No planning-only endpoint exists in this reviewed HTTP source. `/synth` may generate
an ABC score as part of the full pipeline. An eventual `music.plan` operation needs
its own supported contract; it cannot be inferred from an ABC output or supplied
MIDI. ABC input is symbolic text, not an arbitrary MIDI rendering promise.

The server retains at most 32 completed-job records by evicting old non-running
entries; running records are not evicted by that loop. There is no durable restart
recovery, caller request-id deduplication or immutable content receipt at this HTTP
boundary. Generation must snapshot and validate outputs before source eviction.

## Request and result domain

The actual consumed request includes style, lyrics, optional ABC, `cot` mode,
duration, separate language/acoustic seeds, flow-matching steps, language/synthesis
batch counts, sampling parameters, semantic-token replay, guidance and audio format.
Mode accepts full/melody/off; formats are MP3 or WAV16/24/32. Initial qualification
should choose an explicit finite single-song/single-variation subset rather than
expose all upstream controls. Unknown fields and wrong types must reject in our
adapter even where the upstream parser silently keeps a default.

Duration is a semantic-token ceiling, not an exact output-duration guarantee. A
negative seed resolves randomly; actual resolved seeds must come from the returned
replay record. Batch output ordering is song-major. Exact integers outside the
Runtime binary64 domain require a reviewed string/handle encoding or rejection;
do not round seed identity through a generic number conversion.

Synthesis results are `multipart/mixed`: alternating replay-request JSON and encoded
audio for each track. The pinned server builds this even for one track. A stale
browser comment describing a single raw-audio response is not the server contract.
The adapter needs bounded byte/header/part/cardinality parsing, strict MIME and
actual audio decoding checks, exact JSON/audio pairing, required-output completeness
and independent immutable audio/score/replay metadata publication. Do not reuse the
browser's permissive whole-buffer splitter as production validation.

The server logs the full serialized synthesis request. Private draft integration
requires a reviewed upstream logging configuration/patch and retention policy;
Generation cannot promise prompt privacy by sanitizing only its own logs. The
upstream `/logs` surface must not be exposed as Studio diagnostics.

## SheetSage2 alternatives and cancellation

This revision supplies a native `yue-transcribe` executable with required model and
audio arguments, explicit output path and optional melody-only mode. It writes one
ABC file and exits nonzero on failure; there is no MIDI/JSON output manifest to
assume. This candidate is separate from the original Python/Hugging Face SheetSage2
entrypoint. Deployed executable/model identity must select one actual contract.

The optional HTTP transcription route takes WAV/MP3 audio and a melody-only field,
returns a job ID, then exposes JSON containing ABC. The transcription pipeline has
no per-job cancellation callback at this server call site. Setting a cancellation
flag does not prove the transcription stopped; a successful transcription may still
finish as done. The CLI alternative needs process-group termination, confirmed wait
and isolated staging/output roots before release. GPU activation stays with GPU Node
Manager; no provider-local systemd switching is introduced.

## Remaining entry evidence

Before provider code/public catalog activation, #31 still requires:

- Deployed upstream commit/binary and model revisions, compared to the pinned source.
- Sanitized real health/props and one request/status/result sequence per supported operation.
- Finite output/cardinality/input/deadline fixtures, including encoded audio and ABC semantics.
- Actual targeted cancellation/unknown-response/restart observations and physical settlement.
- Reviewed upstream prompt logging and data retention; configured transcriber/CLI selection.
- Owner-checked managed audio input (#30), output transfer/roles and explicit Hub parity.

Current read-only infrastructure observations establish neither these receipts nor
permission to fabricate them. No live generation, activation, deployment or provider
feature is performed by this research delivery; #31 and #25 stay open.
