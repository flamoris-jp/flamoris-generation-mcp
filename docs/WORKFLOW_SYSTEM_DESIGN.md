# Workflow discovery and managed img2img design

Status: proposed implementation contract; design-only. Reviewed 2026-10-01.
Tracks Generation #19/#42 and Studio #21/#30. No implementation, deployment,
runtime registration, GPU execution, or automatic merge is included in this PR.

## 1. Evidence and scope

Source baselines:

| Repository | Reviewed main commit |
| --- | --- |
| Generation MCP | cd5d6011bc0e35c274e8a9d7c1780cdd42100b53 |
| Studio | e89e35e380627c9d4b14d0f59db5acf3cae47745 |
| MCP Hub | fcbe0243c2e54d77e7ea44dc073c883e90d38cb4 |

Read-only live discovery confirmed a registered `janku-basic-image` v1,
the checkpoint named in #19, and an available ComfyUI provider. This verifies
discovery, not the installed node schemas, the raw registered graph, Clip Skip,
img2img execution, or production smoke. Public docs do not retain private
deployment details, upstream job/input IDs, or server inventories.

The attached historical setup notes are background, not current runtime truth.
Current infrastructure observations come from Server Manager; current
Generation catalog/availability comes from its own MCP tools.

### Current implementation

| Area | Evidence in current code | Remaining work |
| --- | --- | --- |
| Trusted registry | `WorkflowDefinition`, `ParameterSpec`, strict schema, bounded graph, model validation | Image semantics and more precise scalar bindings |
| Registration | `WorkflowRegistry.register`, atomic publish/fsync/rollback; immediate activation | Preserve behavior; metadata schema upgrade |
| Discovery | `WorkflowStore.list` returns templates/definitions; metadata excludes graph/node/input | Uniform Image descriptors; Studio consumption |
| Versioning | Latest definition per ID; monotonically increasing versions; saved recipes pin version | Explicit build version precondition; document stale handling |
| Managed inputs | Immutable generated-asset snapshots, confinement, expiry, stage lease | Studio ownership mapping and input preview |
| ComfyUI input execution | Provider uploads image, resolves declared LoadImage binding, stores provenance | Real smoke; decoding bounds and upload retention policy |
| Studio discovery | Gateway reads capability template IDs and models, not workflows.list | Definition descriptors and selection |
| Studio submit | Chooses fixed text-to-image(-lora) from LoRA presence | Selected descriptor/version and parameter mapping |
| Studio Image UX | Styles, preferences, full basic parameters, automatic seed, Use settings exist | Workflow/reference-aware restoration and controls |
| Reference UI | Static unavailable notice | Authorized existing-Asset picker, thumbnail, replace/remove |

Generation #19 already has the registry foundation, runtime registration,
parameter/default/model validation, saved version-pinned recipes, provider
execution, tests, and an example packaged definition. It is not fully accepted:
the example is a fixture, not a validated production JANKU export; the live
basic v1 registration does not establish the intended full JANKU configuration;
Studio discovery/selection and the recorded production smoke remain incomplete.
Keep #19 open until these acceptance items are evidenced.

`docs/MANAGED_INPUTS.md` still describes a pre-adapter rollout. README and
current provider code already support reviewed LoadImage managed-image staging.
Update that documentation during implementation instead of treating the stale
paragraph as absence of the adapter.

## 2. Responsibility and compatibility decisions

- ComfyUI owns runtime execution and its model/node environment.
- Generation owns trusted definitions, validation, recipes, jobs, provider
  adapters, managed inputs, and generated binary assets.
- Studio owns authenticated user authorization, opaque catalog/input handles,
  presentation, preferences/Styles, and request snapshots.
- Hub owns routing and its explicit tool catalog; it does not inspect graphs.

Do not introduce a Studio workflow registry, second job state machine, direct
browser MCP transport, or direct Studio-to-ComfyUI connection.

Retain the two built-in templates, including ordered optional LoRA behavior.
Do not force a dynamic ordered LoRA chain into this first scalar-only declarative
schema. Existing clients and recipes continue working. New workflows use
definitions; both paths expose the same Image descriptor vocabulary.

External-client asset import is separate: Hub #25, Generation #41, Studio #35.
This project enables Studio-originated img2img from Studio-owned assets. It
does not make a previously ChatGPT-created asset owned or visible in Studio.
There is no dependency on that identity project for this vertical slice.

## 3. Definition and discovery contract

Keep accepting definition schema v1. Add schema v2 for explicit Image metadata
and parameter semantic roles. A v1 registration must continue to load/build/save/
submit unchanged; do not silently rewrite it.

V2 adds:

- `image`: an Image-specific descriptor, not a universal UI schema.
- `parameters.<key>.role`: optional bounded semantic role.
- `parameters.<key>.multiple_of`: positive, finite numeric validation, used for
  integral dimensions. Validate defaults/enums against it too.

The Image descriptor has `profile: "image-v1"`,
`mode: "txt2img" | "img2img"`, and one explicit dimension policy:

- `{mode: "parameters"}`: width/height roles control output size;
- `{mode: "fixed", width, height}`: display fixed size, omit size inputs.

An img2img descriptor also declares
`reference_semantics: "initial_image"` and
`resize_policy: "center-crop-resize"` for the initial reviewed workflow.
Do not label this as character identity, style conditioning, IP-Adapter,
ControlNet, or an inpaint mask.

Roles supported by the dedicated Image editor are `checkpoint`,
`positive_prompt`, `negative_prompt`, `width`, `height`, `seed`, `steps`,
`cfg`, `sampler`, `scheduler`, `denoise`, and `initial_image`.
Roles are unique within a definition and have type/constraint checks;
`initial_image` requires a required managed_input with explicit media_types.
Required Image roles are checkpoint and positive_prompt; img2img additionally
requires initial_image and denoise. Parameter-size mode requires both dimensions.
Builtin descriptors additionally describe their existing ordered LoRA contract;
external definitions do not claim it. The graph-free builtin parameter entry
uses type `ordered_loras`, role `loras`, max_items 16, model_kind `lora`,
and the existing item fields name, strength_model and strength_clip (each
strength -20..20, default 1). The no-LoRA builtin disallows nonempty lists; the
LoRA builtin requires at least one entry. This is a dedicated builtin descriptor
extension, not a new external ParameterSpec type.

Parameter keys remain arbitrary declared public keys. A key named `source`
with role initial_image is valid. Studio maps roles, never workflow IDs, model
names, graph node IDs, or parameter spelling. Never infer missing roles.

Shape-only metadata is insufficient to prove semantics: the current validator
accepts a disconnected LoadImage. The ComfyUI v2 Image-profile validator must
verify the supported dataflow for its claimed mode/resize policy, existing
link targets, acyclic dependencies, and that initial_image reaches the declared
output through VAEEncode and KSampler.latent_image. This check belongs in the
provider-specific validation layer. Reject a semantic claim that does not match
the graph. This is a reviewed Image profile, not a generic graph editor.

Example proposed graph-free descriptor:

```json
{
  "id": "janku-reference-image",
  "version": 1,
  "kind": "definition",
  "metadata_schema_version": 2,
  "provider_id": "comfyui",
  "capability_id": "image.generate",
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

Add top-level `managed_input_support` shaped as
`{ready: boolean, media_types: ["image/png", "image/jpeg", "image/webp"]}`.
Presence of inputs CRUD is not readiness. The new operator setting
`FLAMORIS_COMFYUI_MANAGED_INPUTS_READY=0|1` defaults to 0 and is set to 1
only after reviewed adapter/profile smoke and retention evidence.
Trusted registration cannot set that operator flag. Direct trusted-client smoke
may exercise a candidate while Studio keeps it unavailable.

Provider availability and operator readiness remain separate from declarative
schema validity. Studio requires all of its own authorization, the descriptor,
provider availability, and rollout readiness before enabling the picker.
A trusted operator must review/smoke each production graph change; readiness
does not certify arbitrary custom nodes or arbitrary admin registrations.

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
bounded enum when present; otherwise sample an index in the exact valid integer
progression within the bounds. For a fractional multiple_of p/q in reduced
positive rational form, legal integer seeds are multiples of p; derive this
with exact arithmetic, not floating-point tolerance or rounding. For example,
multiple_of 2.5 permits integer seeds 0, 5, 10, ... within the declared bounds.
An empty domain or unsupported constraint representation disables the Studio
option with unsupported_parameter; do not fall back to an invalid default.

Generation validates multiple_of using the same exact numeric interpretation;
Studio must not introduce a different rounding/tolerance rule.
Generation remains authoritative for parameter validation. Studio revalidates
automatic/explicit values before build and snapshots the resolved concrete seed
before submit. Explicit zero remains zero and is rejected if outside D; it is
never a random sentinel. A missing seed role means no seed parameter is sent.
Preserve existing builtin behavior; narrower definition constraints must not
inherit the builtin full-range randomizer.

Picker availability depends on supported Image metadata, provider/operator
readiness and Studio's ownership API, not on a pre-existing input. A valid owned
input is an additional Generate prerequisite. Missing/expired/revoked references
must still permit authorized selection/replacement once those services are ready.

### Build/version behavior

Add optional `definition_version` to workflows.build. For definitions, if
provided it must equal the currently installed version, checked before recipe
creation. Omission preserves latest-version behavior for existing MCP clients.
Builtin calls must omit it. Studio always sends it for definitions.

Retain latest-only storage: <id>.json and monotonic replacement. Updating v1 to
v2 makes old v1 recipes fail closed. Do not implement history/archive retrieval
or silently rebuild against v2 in this scope.

Studio snapshots the chosen version and normalized parameters. If discovery or
build races an update, return workflow_changed, refresh discovery, preserve
the draft, and require a new explicit Generate action. If update occurs after
build but before submit, the pinned recipe must fail before provider submission.
For an already accepted execution, capture its definition/output metadata
before the non-idempotent submit await; an update must not change output-node
resolution or cause a post-submit version lookup failure. Test this race.

Registering/updating a definition remains immediately active and durable without
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
operations; do not invent host paths. A definition registration publishes the
new file before activating it; preserve atomic failure behavior and validation.
Keep one service instance, as current process reservation and registry locks
are not multi-process coordination.

Author/review in ComfyUI -> export API graph -> add explicit allowed parameter
bindings/Image metadata -> validate with offline tests -> trusted runtime
registration -> discovery -> real smoke -> production promotion.
Source-controlled examples are reusable definitions with no model weights or
private media. They must not become a second live registry.

Recovery: restore validated current definition files, restart through the
documented deployment process, then verify discovery. Restore source inputs
through their existing metadata/binaries if needed. Expired inputs remain
expired; recipe restoration does not revive input TTL or obsolete definitions.
Rollback requires a reviewed higher version containing the previous graph, not
a decreasing-version runtime registration.

## 8. Implementation boundaries and checks

Generation files: workflow_registry.py (v2/scalar validation/metadata),
workflows.py (builtin descriptors/version precondition), server.py (tool signature),
providers/comfyui.py and comfyui.py (bounded input/race handling),
config.py (rollout readiness), example_definitions, pyproject.toml if decoder
dependency is added, README/docs/MANAGED_INPUTS, and focused tests.

Hub needs only an optional argument addition in
config/mcps/_generation.example.yaml and schema parity tests for workflows.build.
Its existing registration and input tools already route opaque objects.
No identity propagation changes here.

Tests must cover v1/builtin compatibility, metadata without raw graphs, v2 roles
and mode/dataflow checks, unknown required profiles/types, exact scalar exceptions
and malicious selectors, multiple_of/default constraints, model validation,
runtime register/persistence/rollback, stale discovery/build/recipe behavior,
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
