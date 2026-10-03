# SheetSage2 local transcription provider

The opt-in Python profile connects the independently installed
[SheetSage2 model and custom code](https://huggingface.co/m-a-p/SheetSage2) to
ordinary Generation workflows, jobs, reservations and assets. It does not install
dependencies, download weights, start a service or switch GPU runtimes. Normal CI
uses a real CPU subprocess fixture, without Torch, a GPU or model weights.

The supported entrypoint has SHA256
`20e6b23c910bfdecf012a8ee8d45efcb31cb6cb21351da5921d95fb877e70de6`.
This fingerprint establishes its argument contract, not an entire model snapshot
or a successful deployment smoke. Other local custom-code/configuration files
must be pinned through a complete operator-reviewed SHA256 manifest. In
particular, current upstream notation code must not silently replace the reviewed
installed code. The September 2026 upstream notation updates and the original
installation are independently identified snapshots.

## Configuration

Set `FLAMORIS_SHEETSAGE2_CONFIG` to a private JSON file accessible to the singleton
Generation process. Its fields are:

| Field | Meaning |
| --- | --- |
| `python` | Absolute existing interpreter in the installed Python environment |
| `source_root` | Absolute local directory containing `infer.py`, model weights and custom code |
| `source_revision` | Operator-recorded snapshot/revision identity; bounded non-secret label |
| `source_hashes` | Complete mapping of top-level Python/JSON filenames to lowercase SHA256 |
| `timeout_seconds` | Owned execution deadline, default 600, allowed 1–1800 seconds |
| `log_max_bytes` | Combined discarded stdout/stderr budget, default 65536, maximum 1 MiB |
| `output_max_bytes` | Per-execution output budget, default 32 MiB, maximum 64 MiB |

`source_hashes` includes all top-level `.py` and `.json` files in the chosen
snapshot, including its configuration and any benchmark metadata. The required
minimum names are exported as `REQUIRED_SOURCE_FILES` in the adapter. Relative
subpaths, symlinks, unpinned top-level code/configuration, malformed hashes,
missing/changed files and an incompatible `infer.py` fingerprint fail local
availability before subprocess execution. The interpreter may be the installed
virtual environment's ordinary executable symlink. The model file itself must
be a nonempty regular local `model.safetensors`; this shallow check does not
qualify its tensors or dependent model cache.

Generate the manifest from an **already reviewed** installation and record the
revision associated with it. Hashing whatever happens to be installed is not a
review or a real-runtime attestation. No production path, credential or deployment
configuration is included in this repository.

The profile always selects CPU, FP32, the default preset, no rendering and no
optional tensor exporters. Its fixed argument shape is:

```text
<configured-python> -B <configured-source>/infer.py
  <owned-staging>/input.wav --output <owned-staging>/outputs
  --model <configured-source> --device cpu --dtype fp32 --preset default
  --max-seconds <validated-seconds> --local-files-only
  [--melody-only]
```

This is an explanatory shape, not a command to paste. Paths come exclusively
from private operator configuration and fresh provider-owned staging. Callers
cannot select an executable, model path, code revision, output directory, device,
arbitrary prompt tasks or raw CLI options.

Both Hugging Face and Transformers are forced offline for the entire child
environment, including nested MERT-v2 loading. Existing dependent resources must
already be available in the selected local cache. Server/Hub tokens, passwords,
proxy settings and `PYTHONPATH` are not inherited. GPU visibility is disabled and
CPU thread settings are bounded. These are library execution controls, not an OS
network sandbox for arbitrary third-party code; operator code review remains
required because the upstream loader uses `trust_remote_code=True`.

## Input lifecycle

`music-transcribe` is schema 6, operation `music.transcribe`, provider `sheetsage2`.
It accepts only:

```json
{
  "audio": "0123456789abcdef0123456789abcdef",
  "max_seconds": 30.0,
  "melody_only": false
}
```

`audio` is an existing managed input ID, not an asset ID, URL or filesystem path.
Its WAV is acquired through the existing shared reservation and input lease.
The adapter copies the bounded verified reader into a private `input.wav`, checks
RIFF/chunk/sample-format framing and accepts at most 64 MiB and 600 seconds of
source audio. `max_seconds` selects the first 1–120 seconds, default 30; the
upstream default decoder applies the limit to decoding and the returned waveform.
The profile deliberately does not promise a complete long-song transcription.

The original managed-input lease ends after a complete copy. Later deletion or
expiry of that original cannot redirect the independent provider snapshot. The
accepted job records the original input ID, source asset ID, MIME, size and
SHA256. Staging remains private to the owned execution and is never rebound to a
caller path. Audio is not exposed through provider filesystem paths.

## Output manifest and partial score failure

The adapter never publishes arbitrary files found in an output directory.

| Asset | Kind / MIME | Assigned role |
| --- | --- | --- |
| `transcription.mid` | `midi` / `audio/midi` | `midi`, required |
| `events.json` | `metadata` / `application/json` | `events`, required |
| Sanitized `result.json` | `metadata` / `application/json` | `summary`, required |
| `score.abc` | `score` / `text/vnd.abc` | `score`, optional |
| `melody.mid`, `melody_vocal.mid`, `melody_instrumental.mid`, `chords.mid` | `midi` / `audio/midi` | `parts`, fixed indices 0–3, optional |
| `annotations.json` | `metadata` / `application/json` | `annotations`, optional |

The annotation asset folds only the declared key/chord/structure, beat/downbeat,
rhythm and melody LAB files into JSON. Raw token dumps, playback/rendering files,
internal notation files and undeclared provider files are not published.

A zero exit status is insufficient. Publication requires a structurally valid,
nonempty-note primary MIDI; bounded finite JSON; bounded summary counts with a
matching event count and duration; and a declared musical event namespace. MIDI track framing, variable
integers, running status, channel data, fixed metadata and end-of-track markers
are checked. ABC uses a bounded UTF-8 score envelope, not a full music-theory or
notation correctness proof. Optional declared MIDI/ABC assets are checked before
publication. Symbolic interpretation accuracy still requires listening and
real-model acceptance.

The upstream default invocation can save MIDI/events but omit ABC and still exit
successfully. That outcome completes with `transcription.warnings` containing
`abc_unavailable` and no score asset. A requested `melody_only` score failure is
an execution failure, matching the reviewed entrypoint. Raw `abc_error`, provider
logs and diagnostic strings are replaced with bounded warning codes. Public
summary JSON contains only selected reproducibility fields, counts and warnings,
never input locators or provider tracebacks.

Each published output receives an immutable SHA256. Materialization checks that
digest again and rejects output replacement. Output access uses private directory
descriptors and rejects symlinks, special files and hard links. The declared
asset roles use the existing stable Hub asset identities and chunk transfers.

## Cancellation, recovery and storage

Each accepted invocation owns a fresh process group. Cancellation signals only
that group, waits for settlement and escalates from TERM to KILL when necessary.
Timeout, combined-log overflow and bounded output-directory overflow use the
same settlement path. Unknown ownership or uncertain spawn acknowledgement
retains an unknown fence and never retries generation. A late spawn still belongs
to its original task and is terminated once acknowledged. A new adapter process
does not recover permission to signal an old execution merely from its ID.

Per-session and staging directory counts are capped at 128. Staged inputs and
provider originals are retained conservatively; this profile does not add an
automatic deletion schedule or make the ComfyUI retention command applicable to
native staging. Operators must settle all work and preserve already materialized
Hub archives before applying a separately reviewed staging-maintenance policy.
Never clean an unknown execution's directory to clear an execution reservation.

`system.health` reports `configured-local-resources` and `runtime_verified: false`.
An unavailable optional environment does not make the Generation process
unhealthy. Production/Studio qualification remains separate: perform real-model
output, cancellation, restart, transfer and source/model receipt checks before
declaring this provider complete under issue #31.

SheetSage2 and its MERT-v2 parent have independent model/code licensing. The
upstream model card declares CC BY-NC 4.0; this adapter does not redistribute
upstream code, weights or generated media and does not change their terms.
