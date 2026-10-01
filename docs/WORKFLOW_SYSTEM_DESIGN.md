# Workflow discovery and managed img2img design

Status: proposed implementation contract; design-only. Reviewed 2026-10-01. Tracks
Generation #19/#42 and Studio #21/#30. No implementation, deployment, runtime
registration, GPU execution, or automatic merge is included in this PR.

## 1. Evidence and scope

Source baselines:

| Repository | Reviewed main commit |
| --- | --- |
| Generation MCP | cd5d6011bc0e35c274e8a9d7c1780cdd42100b53 |
| Studio | e89e35e380627c9d4b14d0f59db5acf3cae47745 |
| MCP Hub | fcbe0243c2e54d77e7ea44dc073c883e90d38cb4 |

Read-only live discovery confirmed a registered `janku-basic-image` v1, the checkpoint
named in #19, and an available ComfyUI provider. This verifies discovery, not the
installed node schemas, the raw registered graph, Clip Skip, img2img execution, or
production smoke. Public docs do not retain private deployment details, upstream
job/input IDs, or server inventories.

The attached historical setup notes are background, not current runtime truth. Current
infrastructure observations come from Server Manager; current Generation
catalog/availability comes from its own MCP tools.

### Current implementation

| Area | Evidence in current code | Remaining work |
| --- | --- | --- |
| Trusted registry | `WorkflowDefinition`, `ParameterSpec`, strict schema, bounded graph, model validation | Image semantics and more precise scalar bindings |
| Registration | `WorkflowRegistry.register`, atomic publish/fsync/rollback; immediate activation | Preserve restart-free publish; add separate readiness attestation |
| Discovery | `WorkflowStore.list` returns templates/definitions; metadata excludes graph/node/input | Uniform Image descriptors; Studio consumption |
| Versioning | Latest definition per ID; monotonically increasing versions; saved recipes pin version | Explicit build version precondition; document stale handling |
| Managed inputs | Immutable generated-asset snapshots, confinement, expiry, stage lease | Studio ownership mapping and input preview |
| ComfyUI input execution | Provider uploads image, resolves declared LoadImage binding, stores provenance | Real smoke; decoding bounds and upload retention policy |
| Studio discovery | Gateway reads capability template IDs and models, not workflows.list | Definition descriptors and selection |
| Studio submit | Chooses fixed text-to-image(-lora) from LoRA presence | Selected descriptor/version and parameter mapping |
| Studio Image UX | Styles, preferences, full basic parameters, automatic seed, Use settings exist | Workflow/reference-aware restoration and controls |
| Reference UI | Static unavailable notice | Authorized existing-Asset picker, thumbnail, replace/remove |

Generation #19 already has the registry foundation, runtime registration,
parameter/default/model validation, saved version-pinned recipes, provider execution,
tests, and an example packaged definition. It is not fully accepted: the example is a
fixture, not a validated production JANKU export; the live basic v1 registration does
not establish the intended full JANKU configuration; Studio discovery/selection and the
recorded production smoke remain incomplete. Keep #19 open until these acceptance items
are evidenced.

`docs/MANAGED_INPUTS.md` still describes a pre-adapter rollout. README and current
provider code already support reviewed LoadImage managed-image staging. Update that
documentation during implementation instead of treating the stale paragraph as absence
of the adapter.

## 2. Responsibility and compatibility decisions

- ComfyUI owns runtime execution and its model/node environment.
- Generation owns trusted definitions, validation, recipes, jobs, provider
  adapters, managed inputs, generated binary assets, and runtime readiness attestations.
- Studio owns authenticated user authorization, opaque catalog/input handles,
  presentation, preferences/Styles, and request snapshots.
- Hub owns routing and its explicit tool catalog; it does not inspect graphs.

Do not introduce a Studio workflow registry, second job state machine, direct browser
MCP transport, or direct Studio-to-ComfyUI connection.

Retain the two built-in templates, including ordered optional LoRA behavior. Do not
force a dynamic ordered LoRA chain into this first scalar-only declarative schema.
Existing clients and recipes continue working. New workflows use definitions; both paths
expose the same Image descriptor vocabulary.

External-client asset import is separate: Hub #25, Generation #41, Studio #35. This
project enables Studio-originated img2img from Studio-owned assets. It does not make a
previously ChatGPT-created asset owned or visible in Studio. There is no dependency on
that identity project for this vertical slice.

## 3. Definition and discovery contract

Keep accepting definition schema v1. Add schema v2 for explicit Image metadata and
parameter semantic roles. A v1 registration must continue to load/build/save/ submit
unchanged; do not silently rewrite it.

V2 adds:

- `image`: an Image-specific descriptor, not a universal UI schema.
- `parameters.<key>.role`: optional bounded semantic role.
- `parameters.<key>.multiple_of`: positive integer only, on integer parameters
  only. Reject booleans, numeric strings, non-integer values and nonpositive
  divisors. Generation and Studio use integer remainder (`value % divisor == 0`)
  and validate defaults/enums too. Width/height use 8. Decimal steps for number
  parameters are outside v2; a future contract must avoid float tolerance.

The Image descriptor has `profile: "image-v1"`, `mode: "txt2img" | "img2img"`, and one
explicit dimension policy:

- `{mode: "parameters"}`: width/height roles control output size;
- `{mode: "fixed", width, height}`: display fixed size, omit size inputs.

An img2img descriptor also declares `reference_semantics: "initial_image"` and
`resize_policy: "center-crop-resize"` for the initial reviewed workflow. Do not label
this as character identity, style conditioning, IP-Adapter, ControlNet, or an inpaint
mask.

Roles supported by the dedicated Image editor are `checkpoint`, `positive_prompt`,
`negative_prompt`, `width`, `height`, `seed`, `steps`, `cfg`, `sampler`, `scheduler`,
`denoise`, and `initial_image`. Roles are unique within a definition and have
type/constraint checks; `initial_image` requires a required managed_input with explicit
media_types. Required Image roles are checkpoint and positive_prompt; img2img
additionally requires initial_image and denoise. Parameter-size mode requires both
dimensions. Builtin descriptors additionally describe their existing ordered LoRA
contract; external definitions do not claim it. The graph-free builtin parameter entry
uses type `ordered_loras`, role `loras`, max_items 16, model_kind `lora`, and the
existing item fields name, strength_model and strength_clip (each strength -20..20,
default 1). The no-LoRA builtin disallows nonempty lists; the LoRA builtin requires at
least one entry. This is a dedicated builtin descriptor extension, not a new external
ParameterSpec type.

Parameter keys remain arbitrary declared public keys. A key named `source` with role
initial_image is valid. Studio maps roles, never workflow IDs, model names, graph node
IDs, or parameter spelling. Never infer missing roles.

Shape-only metadata is insufficient to prove semantics: the current validator accepts a
disconnected LoadImage. The ComfyUI v2 Image-profile validator must verify the supported
dataflow for its claimed mode/resize policy, existing link targets, acyclic
dependencies, and that initial_image reaches the declared output through VAEEncode and
KSampler.latent_image. This check belongs in the provider-specific validation layer.
Reject a semantic claim that does not match the graph. This is a reviewed Image profile,
not a generic graph editor.

Example proposed graph-free descriptor:

```json
{
  "id": "janku-reference-image",
  "version": 1,
  "kind": "definition",
  "metadata_schema_version": 2,
  "provider_id": "comfyui",
  "capability_id": "image.generate",
  "readiness": {
    "state": "validated",
    "definition_version": 1,
    "definition_digest": "sha256:<canonical-definition-digest>",
    "reason": "verification_required"
  },
  "image": {
    "profile": "image-v1",
    "mode": "img2img",
    "reference_semantics": "initial_image",
    "dimensions": {"mode": "parameters"},
    "resize_policy": "center-crop-resize"
  },
  "parameters": {
    "source": {
      "type": "managed_input",
      "role": "initial_image",
      "required": true,
      "media_types": ["image/png", "image/jpeg", "image/webp"]
    },
    "width": {
      "type": "integer",
      "role": "width",
      "required": false,
      "default": 512,
      "minimum": 64,
      "maximum": 4096,
      "multiple_of": 8
    }
  }
}
```

This excerpt intentionally omits other required roles and is not a registrable
definition.

### workflows.list evolution

Preserve `templates`, `definitions`, `built_workflows`, and
`saved_workflows`. Enrich template/definition entries additively with the
descriptor above, name/description, and all supported public constraints.
Builtin version is a descriptor contract version, distinct from external
definition_version. Generation owns builtin metadata; Studio must not recreate
its defaults/ranges.

Define descriptor kind as `builtin | definition`, with metadata_schema_version 2
for new descriptors. Public metadata may have at most 128 workflow entries,
64 parameters per entry, and a 256 KiB total serialized discovery payload;
individual strings/enums keep the existing definition bounds. Return a bounded
availability failure on catalog overflow, never an unbounded browser response.

### Infrastructure readiness and workflow readiness

These are independent gates:

| Gate | Authority and meaning |
| --- | --- |
| Infrastructure readiness | Generation operator configuration: managed-input/provider adapter is production usable, with bounded decoder/staging and upload retention evidence |
| Workflow readiness | Generation verification authority: this exact workflow ID/version/canonical digest passed bounded real-runtime verification |

Retain top-level `managed_input_support` shaped as
`{ready: boolean, media_types: ["image/png", "image/jpeg", "image/webp"]}`.
Its ready field means infrastructure readiness only. Proposed operator setting
`FLAMORIS_COMFYUI_MANAGED_INPUTS_READY=0|1` defaults to 0; establish it after
adapter/profile smoke and a safe retention policy. This is a one-time deployment
gate, not an Approve action on each new definition. Registration cannot set it.

Each definition descriptor publishes Generation-computed `readiness` with
`state: registered | validated | ready`, `definition_version`,
`definition_digest`, and a bounded safe reason when unavailable. Ready entries
also expose a bounded verification timestamp/profile revision, without prompts,
input IDs, runtime filenames or private diagnostics. The sample above is
graph-free discovery output, never a field accepted in a Definition document.
Missing/mismatched/malformed readiness fails closed. Registered/validated entries
may appear disabled for diagnostics, but never in Studio production selection.
Studio cannot infer readiness from ID, JANKU/model name, graph, a successful
registration, or the global flag. Normal definition use requires exact ready
identity; Reference Image additionally requires infrastructure readiness,
supported Image semantics, provider availability and Studio ownership APIs.

Builtin txt2img/ordered-LoRA compatibility remains available. Generation emits
ready builtin descriptors with basis `builtin_compatibility` and the bundled
descriptor revision; this explicitly preserves existing behavior and is not a
claim that an external Definition was runtime verified. External registrations
cannot claim builtin kind/basis or replace reserved builtin IDs. New external
definitions, including txt2img, require the attestation described below.

### Automatic readiness attestation

Separate the conceptual states:

| State | Evidence | Studio production use |
| --- | --- | --- |
| registered | Trusted Definition durably published | Unavailable |
| validated | Schema, bindings, bounds and static Image graph semantics pass | Unavailable |
| ready | Exact identity has a persisted successful real-runtime attestation | Available subject to other gates |

Keep current register validation-before-publication: malformed definitions are
rejected and the last valid publication remains intact. Thus a successful
`workflows.register(definition)` normally returns validated directly; registered
is the conceptual durable-publication stage, not a reason to publish unsafe
graphs. Register performs bounded static work only, with no generation wait.
Preserve atomic/fsync/rollback and immediate activation for trusted build/save/
verify, without image rebuild, file release, Git deployment or service restart.
Immediate activation does not imply Studio production availability.

Proposed separate trusted tool:
`workflows.verify(workflow_id, definition_version, definition_digest, parameters)`.
Here workflow_id identifies a registered Definition, not the recipe UUID
returned by workflows.build. Require the exact current identity and normal
declared parameter validation. Parameters contain declared scalar/model values
and existing managed-input IDs only; no local upload, path, URL or raw graph.
Caller authority is the existing trusted client group, not a browser API.

Verify performs bounded installed-provider/node compatibility preflight and
build, then admits one ordinary JobStore job through the normal submit path.
Return its job_id and verification-pending metadata after bounded admission;
do not keep an MCP call open until generation completes. Extend existing
jobs.status/jobs.result with bounded verification outcome/attestation metadata;
do not introduce another executable job state machine or GPU lock. An internal
bounded observer/finalizer uses the same JobStore status/result/asset APIs and
persists readiness automatically after the evidence is complete. No human
Approve, mandatory visual review, or per-version operator flag change occurs.
ChatGPT, Work, CI with an authorized runtime, or an operations tool can run
register -> verify -> poll -> discovery without human intervention. Offline CI
uses fake providers and never claims real production attestation.

Use reviewed bounded smoke parameters (initial JANKU img2img profile: one image, at most
512 x 512 and 30 steps), bounded provider/transfer calls and a configurable
verification deadline capped at 300 seconds. Reject incompatible or out-of-budget
smoke requests before admission. No unbounded queue/rejection/retry loop. Busy
returns the existing safe busy outcome; explicit retry after resource release
is allowed. Re-verification also shares single-active-job authority, including
managed-input staging leases. Never submit directly to ComfyUI. Deadline/cancel
uses normal scoped job cancellation and terminal-state confirmation: expiration
of the verification observer does not free an uncertain active provider job.
Ambiguous submission retains normal submission_unknown/recovery semantics,
never gets an attestation, and is never automatically resubmitted.

For JANKU img2img, successful evidence requires all of:

- installed provider/model/node interface compatibility (including the reviewed
  Clip Skip/resize nodes), not just catalog presence;
- Definition/schema/binding validation plus static reference dataflow validation
  through crop/resize -> VAEEncode -> KSampler.latent_image -> declared output;
- existing generated-asset managed snapshot staging/decoding/upload under lease;
- normal build and submit, terminal successful completion, and bounded retrieval
  of the declared output via normal assets/transfer APIs;
- verified output MIME/decoding/size matching declared output/dimension policy,
  with private evidence retained internally and safe summary metadata published.

Human perception that an image looks plausible is not a readiness condition.
Static semantics and runtime evidence together establish that the source path
is connected and executed; a disconnected LoadImage can never pass validation.

Attestation identity is `(workflow_id, definition_version, definition_digest)`.
Generation alone computes the digest: SHA-256 over UTF-8 compact JSON of the
validated, normalized full Definition (sorted object keys, array order retained,
no NaN/Infinity; canonicalization revision `generation-json-v1`). Include graph,
bindings, outputs, parameters, defaults and semantic metadata. The same
Generation canonicalizer is used at registration, load, build and attestation;
clients relay the digest, never derive it from discovery metadata or raw file
formatting. A canonicalizer revision change invalidates old attestations.

Definition documents cannot carry readiness, attestation, builtin basis or a
production_ready self-claim; reject such fields. Trusted authoring does not
authorize writing a runtime verification result. Store bounded service-owned
attestations separately from definition files, with identity, verification
profile revision, provider compatibility evidence, completion time and safe
output evidence. Persist atomically before publishing ready. Integrity/path/
schema failures or missing evidence leave the entry unavailable. Restrict writes
to the verification service; no client-supplied success receipt is accepted.

Persist a service-generated verification attempt ID before admission, separate
from executable job state. Serialize finalization with register/reverify and
production admission; compare both exact Definition identity and current attempt
ID before publishing success. A superseded attempt for the same identity cannot
restore ready after a newer attempt failed. Attestation/evidence retention is
bounded by the discovery entry limit and existing bounded output storage; reject
overflow, never accumulate unbounded history. No new resource reservation is
introduced by these metadata/persistence guards.

An updated version OR changed canonical digest invalidates eligibility
immediately. Compare identity again atomically at finalization: an old pending
job may complete, but cannot mark a replacement ready. Discovery and production
admission recheck the match; failure to persist cannot expose ready. Failed,
cancelled, timed-out or ambiguous verification retains registered/validated
state, a safe failure reason, and permits explicit reverify. Starting a reverify
clears current readiness before the attempt; failed reverify cannot retain an
old success as current ready. Provider/profile incompatibility or evidence
revocation also makes the entry unavailable. A successful persisted attestation
survives restart only if its identity/profile/evidence still match; an interrupted
pending verification never becomes ready from a fresh empty in-memory JobStore.
Respect current singleton deployment/recovery rules, not multi-process locking.

Legacy v1 definitions without Image metadata remain visible by name/version but
are marked unavailable for the dedicated Image editor with an actionable
metadata-upgrade reason. Upgrade the existing basic registration by registering
a higher version with Image metadata after review, rather than guessing its
roles or resetting its ID. Builtin generation remains available throughout.

Studio accepts known roles, plus a small bounded Advanced section for additional
scalar/enumerated public parameters; no universal schema-driven editor.
Unsupported required parameter types/profile versions disable that descriptor,
without disabling compatible workflows. Defaults, ranges, enums and model
selectors come from validated descriptors. Unknown unsupported fields must not
be silently dropped.

### Studio seed domain

A seed role uses an integer spec. Studio's automatic assignment and Randomize
seed must satisfy the advertised constraints, not only JavaScript precision.
Define D as all integers in 0..Number.MAX_SAFE_INTEGER satisfying the seed
minimum, maximum, typed enum (if present), and multiple_of (if present).
Fractional bounds narrow D using ceil/floor. Booleans, numeric strings and
non-integer enum values cannot become integer seeds through coercion.

Sample D cryptographically without an unbounded rejection loop: filter the
bounded enum when present; otherwise sample an index in the exact integer
progression with step equal to positive integer multiple_of (or 1 if absent).
Use integer quotient/remainder and checked bounds in Generation and Studio;
no rational-step conversion, rounding or floating tolerance. An empty domain or
unsupported constraint representation disables the Studio option with
unsupported_parameter; do not fall back to an invalid default.

Generation remains authoritative for parameter validation. Studio revalidates
automatic/explicit values before build and snapshots the resolved concrete seed
before submit. Explicit zero remains zero and is rejected if outside D; it is
never a random sentinel. A missing seed role means no seed parameter is sent.
Preserve existing builtin behavior; narrower definition constraints must not
inherit the builtin full-range randomizer.

Picker availability depends on supported Image metadata, exact Workflow
attestation, provider/infrastructure readiness and Studio's ownership API, not on a pre-existing input. A valid owned
input is an additional Generate prerequisite. Missing/expired/revoked references
must still permit authorized selection/replacement once those services are ready.

### Build/version behavior

Add optional `definition_version` to workflows.build. For definitions, if
provided it must equal the currently installed version, checked before recipe
creation. Omission preserves latest-version behavior for existing MCP clients.
Builtin calls must omit it. Add optional `definition_digest` as the exact
canonical digest precondition (paired with definition_version when present),
and `require_ready: boolean = false`. Existing trusted clients retain latest/
candidate build behavior by omitting them. Studio always sends version, digest
and require_ready=true for definitions. Build pins the canonical identity and
readiness requirement into the recipe, rejects nonready/mismatched evidence,
and jobs.submit rechecks the same identity/readiness at admission before any
provider await, serialized with publication/revocation. For img2img production
admission also rechecks infrastructure readiness, so a stale Studio discovery
cannot bypass a disabled adapter gate. Candidate verification uses require_ready=false through the
same normal job authority; this trusted testing path is not exposed to browsers.
An attestation revoked after build must fail production admission. Already
accepted jobs retain captured execution/output identity despite later changes.

Retain latest-only storage: <id>.json and monotonic replacement. Updating v1 to
v2 makes old v1 recipes fail closed. Do not implement history/archive retrieval
or silently rebuild against v2 in this scope.

Studio snapshots the chosen version, canonical digest and normalized parameters. If discovery or
build races an update, return workflow_changed, refresh discovery, preserve
the draft, and require a new explicit Generate action. If update occurs after
build but before submit, the pinned recipe must fail before provider submission.
For an already accepted execution, capture its definition/output metadata
before the non-idempotent submit await; an update must not change output-node
resolution or cause a post-submit version lookup failure. Test this race.

Registering/updating a definition remains immediately active for trusted verification
and durable, while Studio production selection waits for automatic attestation, without
container rebuild, Git release, manual definition-file release, or restart.
The one-time deployment of the new schema/tool signature is separate.

## 4. Precise scalar binding validation

Local reproduction against the reviewed validator accepted the fixture but
rejected EmptyLatentImage width and height with the file/asset error.
FILE_INPUT searches both the input name and the entire node class name.

Keep fail-closed file handling. Add exact audited scalar exceptions for
(EmptyLatentImage, width/height) and (ImageScale, width/height), restricted to
integer specs, 64..4096, multiple_of 8, and compatible defaults/literals.
All actual file selectors still require managed_input, exclusively
LoadImage.image. A string/enum/model binding cannot use a scalar exception.
Do not remove the class/input heuristic globally or allow arbitrary *Image*
numeric parameters. Add negative tests for file/image/path selectors and
misdeclared scalar types.

## 5. First production reference workflow

Chosen semantics: one generated image is the initial image for img2img.

Reviewed conceptual flow:

- CheckpointLoaderSimple supplies model/CLIP/VAE.
- CLIPSetLastLayer with the reviewed Clip Skip configuration feeds both text
  encoders; this configuration stays internal to the definition.
- LoadImage -> ImageScale -> VAEEncode supplies KSampler.latent_image.
- KSampler -> VAEDecode -> the single declared SaveImage produces the result.

Use explicit output width/height, center crop to the target aspect ratio, and a
fixed reviewed interpolation method (initial candidate: lanczos). The UI
explains the crop. No automatic size changes or hidden ignored size parameters.

Proposed first public parameters: checkpoint, positive_prompt, negative_prompt,
reference_image (role initial_image), width, height, seed, steps, cfg, sampler,
scheduler, denoise. No multiple images, uploads, LoRA list, or batch count.
Reference Image v1 is Studio-owned existing generated Asset -> managed input
snapshot -> JANKU img2img only. This slice must not close Studio #30: its local
file chooser/drag-drop requirement remains a follow-up for local upload ->
authorized managed input. Do not render an unimplemented upload button.

Use a bounded denoise range 0..1, initially 0.5; explain that higher values
change more of the source and 1 can largely lose it. Do not call denoise an
identity-preservation guarantee. Proposed JANKU defaults are steps 25, CFG 4,
Euler/normal, 512 square; final defaults and Clip Skip (-2 is a candidate)
must match the actual reviewed ComfyUI export and smoke.

Official ComfyUI source confirms the relevant node interfaces:
[nodes.py](https://github.com/Comfy-Org/ComfyUI/blob/master/nodes.py).
The official [img2img example](https://github.com/comfyanonymous/ComfyUI_examples/tree/master/img2img)
explains VAE latent initialization with partial denoise.
These sources justify the design; they do not prove the deployed node version.
Before producing a production JSON definition, inspect the installed object_info
or exported workflow and record required node/API availability and successful
execution. Do not invent a production-verified export from these docs.

## 6. Managed-input execution and lifecycle

Reuse ManagedInputs, AssetTransfers, JobStore reservation and the ComfyUI
upload adapter. Do not introduce a second input store or GPU reservation.
Validate reference existence/expiry/media before submission, then revalidate
under the provider stage lease to close deletion/expiry races.

The first workflow accepts exactly one PNG/JPEG/WebP generated-asset snapshot.
Existing bounds remain: 64 MiB/input, 128 records, 512 MiB snapshot storage,
24h expiry, bounded reader chunks and copy/staging deadlines.

The adapter currently accumulates bounded bytes and uploads before submit.
Add bounded image validation before upload: MIME/signature agreement, decodable
single-frame image, each dimension <=4096 and total pixels <=4096 squared.
Use a bounded maintained image decoder (Pillow with a bounded dependency range
is compatible with Studio's current stack), treat decompression warnings as
errors, and preserve bytes rather than trusting header-only identification.
Reject malformed/animated/oversized content before non-idempotent submission.
Keep the work off the event loop and limit decode concurrency to the existing
single reserved generation; do not turn this into speculative media analysis.

After upload, ComfyUI reads its own copy, not the snapshot reader. The current
lease covers reading/upload/submission, then releases. Tests must distinguish
snapshot-lease release from provider-file retention.

Current code has no provider-input deletion/cleanup API. Do not claim automatic
cleanup of uploaded ComfyUI files. Production rollout requires an explicit
runtime/operator retention policy for service-generated upload files, including
failed/ambiguous submit orphans. Never delete original source assets or arbitrary
runtime inputs. If a bounded safe retention policy cannot be evidenced, leave
managed-input production readiness off and report the operational blocker.
Automatic provider-filesystem deletion is outside this change.

Never retry jobs.submit after an ambiguous timeout. Preserve submission_unknown
and existing reservation handling. Deleting a reference cannot delete an
already staged provider copy or cancel a running execution.

## 7. Persistence, authoring and recovery

Definition and invocation recipe storage remain distinct. Back up the configured
definition directory and recipe/output metadata through existing deployment
operations, together with separate service-owned attestation records; do not invent host paths. A definition registration publishes the
new file before activating it; preserve atomic failure behavior and validation.
Keep one service instance, as current process reservation and registry locks
are not multi-process coordination.

Author/review in ComfyUI -> export API graph -> add explicit allowed parameter
bindings/Image metadata -> validate with offline tests -> trusted runtime
registration/static validation -> automatic verify/normal job -> persisted exact
identity attestation -> ready discovery -> Studio production selection.
Source-controlled examples are reusable definitions with no model weights or
private media. They must not become a second live registry.

Recovery: restore validated current definition files, restart through the
documented deployment process, then validate exact attestation identity/evidence
and discovery. Missing/incompatible attestation requires reverify, never inferred readiness. Restore source inputs
through their existing metadata/binaries if needed. Expired inputs remain
expired; recipe restoration does not revive input TTL or obsolete definitions.
Rollback requires a reviewed higher version containing the previous graph, not
a decreasing-version runtime registration.

## 8. Implementation boundaries and checks

Generation files: workflow_registry.py (v2/scalar validation/metadata),
workflows.py (builtin descriptors/version precondition), server.py (tool signature),
providers/comfyui.py and comfyui.py (bounded input/race handling),
jobs.py (verification observer/admission through existing authority), separate bounded
attestation persistence, config.py (infrastructure readiness/verification bounds), example_definitions, pyproject.toml if decoder
dependency is added, README/docs/MANAGED_INPUTS, and focused tests.

Hub #26 must cover optional workflows.build definition_version/definition_digest/
require_ready arguments, the new workflows.verify tool and its exact generated
schema/annotations, plus safe register/list descriptions distinguishing activation
from readiness. Existing jobs.status/result route additive verification metadata.
Its existing registration and input tools already route opaque objects. Match
schemas to the implemented Generation signature, never invent catalog defaults.
No graph inspection, attestation authority or identity propagation belongs here.

Tests must cover v1/builtin compatibility, metadata without raw graphs, v2 roles
and mode/dataflow checks, unknown required profiles/types, exact scalar exceptions
and malicious selectors, multiple_of/default constraints, model validation,
runtime register/persistence/rollback, validated-not-ready discovery, automatic
attestation persistence/restart failure, version/digest invalidation, self-claim
rejection, finalization/update/revocation races, failed/retried/busy/timed-out/
ambiguous verification sharing the single reservation, integer-only multiple_of
and invalid divisors, stale discovery/build/recipe behavior,
definition update during accepted submit, PNG/JPEG/WebP, malformed/animated/
pixel-bound/expired/revoked inputs, upload failures and ambiguous submissions,
declared-output scoping, and stage-lease release.

Normal CI uses fake providers and small assets. Run the repository's current
ruff check/format, pytest, python -m build, installed-wheel and Docker smoke
checks for implementation. This design-only PR uses documentation/link/source
consistency checks; it does not report product tests as run.

See [implementation handoff](WORKFLOW_IMPLEMENTATION_HANDOFF.md) and
[Studio companion design](https://github.com/flamoris-jp/flamoris-studio/pull/37)
for commit order, APIs, ownership, and acceptance.
