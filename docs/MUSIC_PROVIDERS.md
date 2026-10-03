# Native Music and transcription profiles

The existing workflow/job/provider/asset tools support two independent opt-in
profiles. Their adapters execute through the same single active `JobStore`
reservation as Image and Speech. No new job endpoint, runtime controller or
orchestration authority is introduced.

| Operator configuration | Template | Capability | Provider | Saved recipe |
| --- | --- | --- | --- | --- |
| `FLAMORIS_YUE2_CONFIG` | `music-generate` | `music.generate` | `yue2` | schema 5 |
| `FLAMORIS_SHEETSAGE2_CONFIG` | `music-transcribe` | `music.transcribe` | `sheetsage2` | schema 6 |

Both configurations are unset by default. An operator must select trusted local
resources and complete real-runtime qualification before enabling a production
consumer. Health checks describe resource/transport availability;
`runtime_verified: false` and descriptor `readiness.status: not-attested` remain
explicit. A configured provider never grants Image workflow attestation or
automatic runtime activation. Native recipes reject Image definition version,
digest and `require_ready` preconditions.

## Music generation

The YuE2 profile pins `ServeurpersoCom/yue2.cpp` revision
`decfe04c2ae2f8c73855832a56ddda0fce849407`. It submits one fixed full-CoT
single-track synthesis request to the verified `/synth` API and observes only the
returned execution ID through `/job`. Its bounded multipart result is normalized
to WAV audio, ABC score and JSON replay metadata. This is a synthesis profile;
there is no advertised `music.plan` operation or CLI execution shortcut.

Point `FLAMORIS_YUE2_CONFIG` to an operator-owned JSON file such as:

```json
{
  "url": "http://localhost:8080",
  "source_revision": "decfe04c2ae2f8c73855832a56ddda0fce849407",
  "model_revision": "operator-verified-model-identity",
  "timeout_seconds": 30,
  "execution_timeout_seconds": 1800
}
```

The configured source/model identities are declarations checked against this
adapter contract; HTTP health does not cryptographically attest a remote binary
or its loaded weights. Record those installed identities during qualification.
The URL cannot contain credentials, query or fragment. Redirects and environment
HTTP proxies are disabled. An unavailable health preflight rejects before
synthesis submission; a lost response after submission retains unknown capacity.

```json
{
  "template": "music-generate",
  "parameters": {
    "style": "gentle piano instrumental",
    "lyrics": "",
    "seconds": 30,
    "steps": 32,
    "seed": 1234,
    "lm_seed": 1234
  }
}
```

Pass that object to `workflows.build`, then pass the returned `workflow_id` to
`jobs.submit`. Style is required and bounded to 1,024 characters / 4,096 UTF-8
bytes. Lyrics are optional and bounded to 4,096 characters / 16,384 bytes.
Duration is 1–120 seconds, steps 1–64, and both seeds are integers from zero to
`2**53-1`. The profile fixes full CoT, one track and WAV16 output. Callers cannot
select a provider URL, filesystem path, raw graph, model or subprocess argument.

YuE2's scoped cancel request is an acknowledgement, not proof of physical
settlement. A cancel-requested/unknown job retains shared capacity until its
execution reports a definite terminal state. Missing history, timeout and lost
submission acknowledgements never trigger generation replay or automatic release.

## Audio transcription

The SheetSage2 profile pins the installed Python entrypoint SHA-256
`20e6b23c910bfdecf012a8ee8d45efcb31cb6cb21351da5921d95fb877e70de6`.
Its operator-owned local source/configuration manifest is also checked before
launching. The existing interpreter runs a fixed argv with CPU, FP32 and local
files only; offline Hugging Face flags are set. This profile uses the Python
implementation with MIDI and annotations, independently of native
`yue-transcribe` and its GGUF/ABC contract.

See [the SheetSage2 configuration and source-manifest contract](SHEETSAGE2.md)
for the required local files and their trusted hashes. The MCP package does not
install its interpreter, source tree, dependencies or model.

```json
{
  "template": "music-transcribe",
  "parameters": {
    "audio": "0123456789abcdef0123456789abcdef",
    "max_seconds": 30,
    "melody_only": false
  }
}
```

`audio` must be a published immutable input ID returned by
`inputs.create(asset_id)`, with actual WAV signature and `audio/wav` MIME. The
input source is an existing managed/generated asset. This adds no client upload,
remote URL ingestion or caller-provided local path. Studio must verify source
ownership and its own input-owner mapping before constructing a user job.

The input is leased while copying to a private provider-owned immutable WAV.
The source lease may then end because inference reads that separate copy.
Source deletion does not alter the provider copy or captured input/source IDs,
SHA-256, MIME and byte length. Provider staging remains confined and bounded;
uncertain process settlement retains its job reservation and staging fence.
Process-group cancellation targets only the adapter's own execution.

`max_seconds` bounds the processed initial segment to 1–120 seconds; it is not an
unbounded full-file transcription request. MIDI and parsed events/summary are
validated before declaring success. ABC can be unavailable while MIDI succeeds,
so exit code zero alone is insufficient. Optional ABC, separated MIDI parts and
normalized annotation JSON retain explicit output roles; files outside the fixed
output contract are never collected by a directory glob.

## Asset delivery and recovery

| Suffix | Media kind | MIME |
| --- | --- | --- |
| `.wav` | `audio` | `audio/wav` |
| `.mid` | `midi` | `audio/midi` |
| `.abc` | `score` | `text/vnd.abc` |
| `.json` | `metadata` | `application/json` |

Use `jobs.status` and `assets.list` to discover stable individual output IDs and
their adapter-assigned roles. Listing does not download binary media. Use
`assets.prepare` / `assets.read` for bounded verified delivery; ABC and MIDI use
that same route, not native MCP `ImageContent`. Input reproducibility and trusted
external client provenance survive completed materialization and active journal
recovery without exposing provider staging paths.

An ambiguous non-idempotent submission is never retried. A restart restores an
active native reservation as unknown, including a lost-acknowledgement window,
and refuses another provider job until reconciliation. Disabling a native profile
while its reservation exists fails startup rather than forgetting possible work.
Completed archives preserve stable local assets; they do not reconstruct process
ownership or authorize provider replay. Materialize outputs before a planned
restart and retain the existing single-instance deployment requirement.

## Acceptance still required

GPU-free fixtures verify validation, source/transport handling, shared exclusion,
unknown submission, scoped cancellation, role metadata and bounded delivery.
They do not prove installed model behavior. Each deployment must record actual
runtime/model/source identities, successful normalized artifacts and their byte
digests, cancellation and physical settlement, restart recovery, input staging and
upstream request-log handling. Keep the owning Music and managed-input acceptance
issues open until those observations are available. AnimeGen and SeeThrough still
require their separately verified ComfyUI graphs/output contracts and remain
unavailable through these native Music profiles.
