# Irodori native speech

Generation supports an explicitly configured `speech.generate` implementation using
the `speech-no-reference` native recipe (schema 4). It uses the existing shared
JobStore reservation and generic workflow/job/asset tools. It is disabled by default.
This profile supports Japanese text and an optional voice-design caption, with one
audio output and no reference audio, embeddings, LoRA, arbitrary model selection,
shell, public filesystem paths or provider-local GPU/service activation.

## Source contract and configuration

The reviewed upstream is [Aratako/Irodori-TTS at
89f9d8fbd4d51ea019867ee1197725ede1df13c5](https://github.com/Aratako/Irodori-TTS/tree/89f9d8fbd4d51ea019867ee1197725ede1df13c5).
The adapter verifies the pinned Git blob identities of `infer.py` and all 19 Python
modules in `irodori_tts` before advertising local-resource availability and before
submission. A missing or changed source/model/codec/tokenizer resource makes the
provider unavailable; this does not change Generation process liveness or Image
availability. This check does not load weights, prove their contents/revision, run
GPU inference or establish quality/readiness.

Set `FLAMORIS_IRODORI_CONFIG` to an operator-owned JSON file (maximum 16 KiB):

```json
{
  "python": "/srv/irodori/.venv/bin/python",
  "source_root": "/srv/irodori/source",
  "checkpoint": "/srv/irodori/model/model.safetensors",
  "codec": "/srv/irodori/codec/weights.pth",
  "source_revision": "89f9d8fbd4d51ea019867ee1197725ede1df13c5",
  "model_revision": "operator-verified-immutable-model-revision",
  "model_device": "cuda",
  "codec_device": "cuda",
  "timeout_seconds": 300,
  "log_max_bytes": 65536
}
```

All resource paths are fixed absolute operator configuration. The Python interpreter
may be a virtual-environment symlink; source, configuration and output files must
be regular files. Checkpoint, codec and tokenizer files reject symlinks by default.
For an existing Hugging Face snapshot cache, set optional `resource_root` to the
absolute operator-owned cache root. Both the configured paths and their resolved
targets must remain inside that root, and targets must be nonempty regular files.
Escaping links, broken links and unrelated configured resources fail closed. Keep
this resource tree writable only by trusted operators: source and model checks do
not establish immutability against concurrent operator changes. The CLI retains
the snapshot checkpoint path so upstream can locate the adjacent tokenizer.
Keep the checkpoint's bundled `tokenizer`
directory beside it, and pre-install the selected PyTorch/backend and upstream
dependencies. The adapter invokes the configured interpreter directly with fixed
arguments, a local `--checkpoint`, local codec, `--no-ref`, one sequential candidate,
explicit duration/steps/seed and fixed tokenizer limits. It sets Hugging Face,
Transformers and datasets offline, disables user-site packages, and avoids existing
bytecode caches. Only standard runtime/cache/GPU environment variables are inherited;
server credentials and Hugging Face tokens are not passed to the child. Generation
never installs dependencies or downloads weights.

Upstream SilentCipher may use another Hugging Face snapshot. Pre-cache its resources
when watermarking is required. The upstream can continue without watermarking when
that backend is unavailable; this adapter does not attest watermark presence.
Text/caption also pass through upstream normalization and a 256-token conditioning
limit; the public character/byte bounds do not promise an untruncated tokenization.
The CLI carries text/caption in process arguments, so host process inspection and
host log access remain an operator access boundary.

## Public profile

Discovery identifies provider `irodori`, capability `speech.generate`, native profile
`irodori-no-reference-v1`, the exact source revision, `reference_audio: false` and
`candidates: 1`. Qualification is `configured-local-resources`; `runtime_verified`
is false and no v2/v3 readiness attestation is claimed. A Studio Speech editor needs
its own explicit opt-in, exact profile match and provider availability check.

| Parameter | Type | Bound | Default |
| --- | --- | --- | --- |
| `text` | String | Required, 1–512 characters, at most 2,048 UTF-8 bytes; nonblank | None |
| `caption` | String | 0–512 characters, at most 2,048 UTF-8 bytes | Empty |
| `seconds` | Number | Finite, 0.5–30 | 10 |
| `steps` | Integer | 1–80 | 40 |
| `seed` | Integer | 0–9,007,199,254,740,991, exact | 0 |

Control characters other than newline/tab and unknown parameters reject. Native
builds reject definition version/digest and `require_ready: true`; no ComfyUI graph
is fabricated. The build returns schema 4, scalar parameters, `prompt: null` and the
explicit Speech profile. Save/load and reservation recovery preserve this recipe.

## Execution and output settlement

Submission has a maximum five-second spawn-acknowledgement wait within the absolute
configured execution deadline. A lost acknowledgement/timeout/task cancellation
remains unknown with the shared reservation held; no submission is replayed. A late
acknowledgement is used only to clean up that original owned process. Normal work
has bounded stdout/stderr drains; bytes are discarded and never returned as
diagnostics. Exceeding the aggregate log budget stops the owned process group.

Cancellation/deadline cleanup targets only the spawned POSIX process group: TERM,
up to two seconds of settlement checks, then KILL and up to two more seconds. A
terminal result requires both observed process exit and absence of that group.
Unconfirmed settlement stays unknown. Restart never adopts a PID or treats an
existing staged WAV as proof of completion; the recovered JobStore reservation
remains held for operator reconciliation.

A successful output is validated before publication as a bounded RIFF/WAVE file:
mono PCM or finite float32 samples, 8–96 kHz, at most 31 seconds and 8 MiB. The
adapter assigns output port/role `audio`, role index 0, MIME `audio/wav`, and pins the
content digest before materialization. Changed files and symlinks reject. Generic
asset metadata, WAV transfer and existing players consume the result. Durations
may be shorter after upstream tail trimming; no exact spoken-content quality is
inferred from successful decoding.

Staging/session capacity is 128 executions and each accepted WAV is at most 8 MiB.
Capacity exhaustion rejects before spawn. Collection limits do not impose a hard
filesystem quota on the upstream CLI's writes; configure an operator staging-disk
quota for that physical bound. Staging cleanup is an operator task after completed
assets are retained and active/unknown reservations are reconciled; restarting does
not silently delete execution evidence.

## Acceptance and licensing

CPU-only CI uses an explicitly fake CLI and tiny WAV, including the complete shared
workflow/job/result/asset flow, scoped cancellation, timeout, lost acknowledgement,
bounded logs, changed output, source mismatch and restart fences. It uses no GPU,
model download or real Irodori model. Production acceptance still requires verified
deployed source/weights/codec/tokenizer identities, offline dependency availability,
one real inference/download/decode receipt, actual targeted cancellation/unknown
settlement observations and Studio/Hub ownership checks. Source completion alone
does not complete the real-provider checkboxes in #25.

Upstream code is MIT. The [v4.1-Small model
card](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small) declares MIT and additional
ethical restrictions including no nonconsensual impersonation or misleading
synthetic speech. Generated audio does not inherit this repository's code license.
No third-party implementation or model weights are vendored here.
