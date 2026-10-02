# Multimodal media Workflows and pinned composition

Status: proposed contract design, 2026-10-02; no v3 implementation in this change. Cross-repository decisions: [FLAMORIS AI #15](https://github.com/flamoris-jp/flamoris-ai/issues/15). Existing `WORKFLOW_SYSTEM_DESIGN.md`, `WORKFLOW_VERIFICATION.md`, `ASSET_TRANSFER.md` and current code remain the implemented contracts.

## Current constraints and compatibility

At main `b2033d57723e90535c74ded5e9ce57fb7bf999dc`, registered definitions and ParameterSpec are Image/ComfyUI-specific, CapabilityRegistry permits one provider per capability, and WorkflowStore retains the active latest definition. The provider-neutral output record already has kind/MIME, and bounded transfer is generic; that does not register Music/Speech/Video providers. Native-image assets.get remains compatible.

Add definition schema v3 through a new validated discriminated contract; do not reinterpret definition v1/v2, saved recipe schemas, or Image-v1 semantics. A v3 wrapper for a legacy definition must pin the original content/profile and use the same validator. Unsupported v3 features fail explicitly on old servers. New saved-recipe and descriptor revisions are independently versioned and must be coordinated with Hub and Studio before deployment.

## Definitions and profiles

V3 has common identity, capability, public ports, parameters, output declarations and resource ceilings. Exactly one execution kind is selected:

- `provider`: one explicit registered provider plus provider-specific validated artifact/bindings;
- `composition`: pinned includes plus public-port bindings, no top-level fake provider ID.

Profiles define real semantics, not UI layout. Proposed profile families are image-generate, image-decompose, music-generate, music-transcribe, speech-generate, video-generate and video-motion-reference; their versioned concrete validators are separate deliveries. Component-only profiles declare typed public contracts but need not be selectable Studio operations. A ready registry entry cannot advertise a profile the provider/editor does not support.

Image roles such as checkpoint, positive_prompt and initial_image retain current meaning. Music roles may include style, lyrics and symbolic plan, but supported fields must follow the actual adapter contract. For example, a YuE2 ABC input is not automatically an arbitrary-MIDI rendering service. Do not promise a role solely because an operation note or model name suggests it.

Parameters remain strict bounded scalar/enum or explicit structured profile types. Public input/output ports additionally carry type/schema revision, semantic role, media kind, MIME allowlist, required/optional and cardinality. Structured plans such as ABC and analysis JSON have format/version/size validation. No arbitrary JSON becomes executable code.

## Capability discovery

Capability id denotes a creative operation, distinct from an execution implementation. Proposed metadata adds category, operation, execution mode and a bounded list of compatible workflow identities/profiles. Workflow descriptors expose exact id/version/digest, profile, supported roles, output declarations and readiness reason without provider graphs/locators.

Generalize the current one-provider mapping so music.generate or image.generate may have several explicit Workflow implementations. Submission follows the built manifest's providers; never choose a random healthy provider or latest workflow. A graph-free aggregate capability is available when at least one compatible production-ready Workflow is currently usable; per-Workflow readiness/availability remains visible. Preserve legacy fields only when their meaning is honest; ambiguous legacy single-provider lookup returns a documented compatibility error rather than silently selecting one. A descriptor protocol revision is required for this semantic change.

Initial operation ids: image.generate, image.decompose, music.generate, music.transcribe, speech.generate, video.generate, video.motion_reference. Do not add unsupported operations to production discovery. Process health, provider health, static support, infrastructure readiness and exact Workflow readiness remain separate.

## Include grammar and immutable versions

Illustrative proposed composition fragment (not a complete registrable definition):

```json
{
  "schema_version": 3,
  "id": "example-song",
  "version": 1,
  "capability_id": "music.generate",
  "execution": {"kind": "composition"},
  "profile": {"id": "music-generate-v1"},
  "includes": [
    {"alias": "compose", "workflow_id": "example-compose", "version": 3, "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    {"alias": "render", "workflow_id": "example-render", "version": 2, "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
  ],
  "bindings": [
    {"from": "inputs.prompt", "to": "compose.inputs.prompt"},
    {"from": "compose.outputs.score", "to": "render.inputs.score"},
    {"from": "render.outputs.audio", "to": "outputs.audio"}
  ]
}
```

References use finite public-port paths only. They do not interpolate strings, environment variables, URLs or scripts. Parent inputs may feed child inputs; a child output may feed another child's input or a parent output. Literal/default inputs need declared types and bounds. An explicit binding supplies the port value; absent an explicit binding, a declared child default is used only where the child contract permits omission. Reject overlapping aliases or hidden parameter overrides; validate the resolved value against both port constraints and the qualified child domain. Each required child input and required parent output has exactly one declared source (or a permitted child-input default); multiple sources require an explicit approved merge component. Parent inputs/parameters must have one unambiguous public naming/mapping contract; shadowing a child port or overriding a child parameter outside that contract rejects. Optional outputs cannot feed required inputs without an explicit typed policy. Validate media MIME intersections, profile semantics, cardinality and format revisions. Conversion is an explicit registered component with its own effects and budget, never hidden reference behavior. Static schema compatibility does not prove a concrete intermediate value: before dependent dispatch, validate the actual upstream output, required presence/cardinality, downstream bounds/qualified domain and current handle ownership/expiry. Absent, malformed, out-of-domain or no-longer-authorized values stop that dispatch; no implicit default, stale value or conversion is substituted.

Includes are namespace-local trusted registry lookups, never file paths or downloads. Reject duplicate aliases, missing or administratively disabled/revoked dependency records, invalid hashes, bindings to internal nodes, both include cycles and binding-derived dependency cycles. A retained historical child remains resolvable and executable under its own current validation/evidence even when a newer version owns the active alias. Moving the active alias alone neither revokes a child nor invalidates a pinned parent. The same child version may appear under multiple aliases; every expansion occurrence counts toward limits. DAG sharing must not collapse two intended executions into one.

Retain immutable `(id, version, digest)` records in a bounded durable version store. The active alias is separate from historical identity. Registering the same identity with different content fails; same exact content may be an explicit idempotent no-op. Publishing a new version does not edit parents. Administrative disable/revocation is separate from normal active-version update and is checked at admission/handoff. Garbage collection may remove only unreferenced records outside active parents, saved/built plans, attestations and in-flight jobs; if bounded storage fills, reject publication rather than evict a dependency. Atomic publication/restart indexing and dependency reference accounting need tests. The existing latest-only registry is not sufficient for this contract.

## Compile path and bounds

Registration performs bounded resolution/static validation. Build resolves the immutable closure again under a consistent registry snapshot, validates concrete parameters and emits a media execution manifest: closure identities/digests, qualified parameter/model domain, typed bindings, occurrence aliases, provider artifacts, budgets, profile/compiler/adapter revisions and separate deterministic structural/invocation digests. No latest lookup or include resolution occurs after admission. Plan caching is not authorization.

Keep structural qualification identity separate from concrete invocation identity. The structural plan digest pins the complete topology, exact closure, typed bindings, constants, qualified model identities, declared resource ceilings and profile/compiler/adapter revisions. A caller's tighter effective limits belong to the bound invocation and can only tighten those ceilings. Only explicitly declared input slots may vary under a reviewed profile's qualification predicate; changes to topology, executable constants or model content never hide in an input slot. The bound invocation digest additionally pins normalized effective parameters/defaults, immutable input content identities and the concrete built artifacts for that request. An attestation records both its structural plan digest and the smoke invocation digest plus the explicit qualified-domain predicate. Production must match the structural identity and satisfy that predicate; it need not reuse the smoke seed/prompt, and the smoke invocation digest must not masquerade as a parameter-independent readiness key. A broad domain requires profile acceptance evidence, not inference from a single smoke. Unknown predicates fail closed; every concrete intermediate is rechecked before handoff.

Definition, closure, structural plan and bound invocation hashes are separate named domains with versioned canonical encodings. V3 canonicalizes schema-validated values to UTF-8 compact JSON with sorted object keys and ordered arrays; reject duplicate decoded keys, unknown fields, nonfinite numbers and excessive JSON/structured-value nesting before recursive resolution. Fix scalar normalization in the schema so equivalent representations cannot vary between registration/build. Definition identity covers every execution-affecting artifact constant and logical reference. Observations/readiness records are separate objects, and private transport locators/credentials stay outside portable Definitions; excluding metadata must never omit executable content from the digest. Retain the existing v1/v2 digest algorithm unchanged. Do not pass a Generation digest as a Runtime IR digest: Runtime keeps its own canonical export rules. The bridge records both identities and the pinned lowering revision.

Proposed initial hard ceilings (lower deployment/profile limits win): root plus eight include edges in a path; 64 total include occurrences; 256 expanded steps; 1024 binding/dependency edges; 64 public ports/parameters per definition; 256 KiB per definition and 2 MiB resolved closure/manifest; 128 final assets. Bound traversal/expansion before allocation; use checked counts, and reject oversized defaults/structured inputs. Existing stricter Image/discovery limits remain unchanged. These are finite proposal ceilings, not permission to raise every current limit.

Every executed provider node must be reachable in the admitted plan and covered by validation/evidence. Reject disconnected hidden outputs/effects. Validate aggregate storage, input leases, output cardinality, time and resource bounds, not just each child separately. Caller limits may only tighten service limits.

F2 first supports includes that lower safely into one registered provider artifact under one ordinary JobStore reservation. Do not blindly concatenate ComfyUI graphs: apply deterministic alias/node rewriting, registered adapter port translation, explicit output indices and whole-expanded-artifact validation. Unsupported public-port combinations reject. Different providers or model-adjacent work remain unavailable until the Runtime bridge is reviewed and accepted.

## Cross-provider Runtime bridge

Generation owns the public media job, result/output publication and media qualification. For an admitted composed media job, a reviewed bridge lowers the media manifest into registered Runtime IR; Runtime validates/compiles its own plan and owns Runtime Run/Job/Continuation scheduling. Record the media-job/Runtime-run mapping and each provider operation provenance. Generation derives presentation from Runtime observation without allowing either side to overwrite the other's state machine.

Do not hold a Generation reservation and recursively submit a new public Generation job through Runtime. The bridge must separate external root admission from internal registered provider-operation dispatch under the root's scoped grant. Internal provider operations retain Generation asset/input ownership and runtime-fingerprint checks; they are not arbitrary private URLs or unrestricted tools. A Runtime-originated independent media call may still use the ordinary external Generation boundary. Reject recursive media-job ownership and lock acquisition.

The bridge needs explicit single-user/scoped-principal deployment rules before shared Studio use, resource arbitration with GPU Node Manager, bounded dispatch, deadlines, cancellation, observation retention and restart reconciliation. No distributed lock or durable execution recovery is inferred from shared files. Runtime capability ids/IR schemas must come from current Runtime implementation at implementation time, not these conceptual examples.

## Attestation of the entire composition

Preserve automatic verification through the ordinary Generation admission/resource authority. State progression distinguishes registration/static validity, verification attempt and current ready predicate. A component's internal verified contract is separate from exposure as a standalone creative Workflow.

Parent readiness requires:

1. Immutable root and complete dependency closure pass current validators; no revoked dependency.
2. Every leaf has compatible current real-runtime qualification for its used input/model/profile domain.
3. Actual expanded composition smoke succeeds, materializes and validates required intermediate/final outputs, and records observed bindings/provenance.
4. Attestation pins root id/version/digest, canonical dependency closure digest, structural media-plan digest, smoke invocation digest and qualified-domain predicate, profile/compiler/adapter/evidence revisions, admitted attempt identity and runtime evidence for every executing provider/capability.
5. Current evidence continuity and independent infrastructure gates hold for that exact set.

A virtual parent has no fake single runtime fingerprint: use a canonical vector of involved runtime/capability evidence. Current exclusive-mutation-lock Image evidence remains required; do not replace it with model names, stat data, health checks or user-authored receipts. Qualifying other providers requires equivalent reviewed mutation/continuity guarantees or ready remains unavailable.

Registration/build/admission and every provider dispatch/resume must check exact pins and current scope/budget/availability. Lock required evidence in a deterministic order at each handoff, revalidate the closure vector, and retain applicable guards through input staging/provider acceptance. Changes during a multi-stage execution abort further dispatch and leave unknown in-flight work owned for reconciliation. A completed artifact may retain its provenance but cannot certify a changed runtime. Evidence expiry, epoch/content replacement, changed child/profile/compiler and incomplete verification cannot confer ready.

Qualification and current dispatch availability remain separate. This first static-composition phase requires all used evidence to be concurrently current and does not auto-start providers. A composition requiring mutually exclusive GPU runtimes remains unavailable under that rule. The future Runtime bridge must separately establish a bounded stage-activation/qualification contract with GPU Node Manager, including re-verification after any epoch change; historical evidence alone cannot make an offline stage dispatchable.

Smoke budgets are operation-specific: Image retains its current one-image/512-size/30-step/300-second bounds. Music/Video need separately reviewed finite duration/frame/step/byte/deadline limits; some supported installed runtimes may exceed current deadlines and remain disabled until qualified. Verification may not invent a smaller invalid topology to claim that a full production composition was exercised. Attestation declares its qualified domain; arbitrary parameter/model combinations outside it require new verification. No manual approval or global managed-input-ready flag substitutes for exact attestation.

## Output contract and media-neutral inputs

Each output declares stable port/role, media kind, MIME allowlist, min/max cardinality and semantic metadata schema. Provider adapters map actual output manifests to declarations. Reject required missing outputs, duplicate role identities, invalid format/cardinality or undeclared public outputs. Optional diagnostics are separately bounded. Do not infer role from filename/extensions, and never forward arbitrary provider paths.

Example role sets, subject to adapter evidence:

| Operation | Roles / kinds |
| --- | --- |
| Music generate | primary audio; optional score/ABC, MIDI and bounded composition data |
| Music transcribe | transcription MIDI; melody/chords MIDI; score; structured analysis |
| Speech generate | primary audio; optional validated timing data |
| Video generate/reference | primary video; optional bounded frame sequence/timing data |
| Image decompose | primary PSD/document; preview; bounded layer images and manifest |

Extend durable asset manifests with role and typed safe metadata, preserving readers for old entries and tombstones. Legacy role absence means legacy/unclassified, not a fabricated primary output. Role assignment comes from validated provider declarations. Unknown formats remain bounded downloadable data, never inline executable content. Outputs can be cataloged without downloading all binaries.

Use assets.prepare/read for every supported media; preserve offsets, per-chunk/final integrity and actual-byte ceilings. Do not raise assets.get into a general giant-base64 response. Current unmaterialized provider mappings are lost on restart; a new provider must not claim restoration unless it establishes that contract. Scoped deletion never implies deletion of provider originals.

Managed input records are already generic snapshots for PNG/JPEG/WebP/WAV, but executable ParameterSpec currently permits managed images only. Generalize through reviewed profile-specific roles such as initial_image, source_audio and reference_voice. Keep explicit MIME/decode/bytes/pixels/duration/channels limits, snapshot immutability, expiry and scoped staging leases. Supporting MP3 requires a reviewed decoder/transcode contract; MIME metadata alone does not enable it. Provider-native staging stays within adapters. Explicit per-job intermediate snapshots may use approved Generation-owned references; they cannot reuse Studio authorization from an unrelated asset. Preserve current quotas until a reviewed provider profile justifies changes. Local upload/URL ingress is a separate contract.

## Failures, loops and observation

Default composed execution requires all declared required stages to succeed. Failures stop dependent dispatch, request targeted cancellation where supported and retain resource/input ownership for active or uncertain operations. Cancelling is not rollback; partial public results have an explicit incomplete status and cannot appear as complete required output sets. Cleanup of intermediate assets is bounded and reference-aware; retained cleanup debt remains accounted. A transport timeout never proves stop or allows blind replay. Restart recovers archives/definitions/evidence under existing rules, not in-flight jobs into fabricated success.

Static includes are acyclic. Later bounded loops/branches are Runtime control contracts, not recursive includes. The proposed melody-first composition can create a motif, then iterate over a finite section plan with max_sections/max_iterations, aggregate deadline/assets/resources and explicit typed state; unbounded while/until-AI-is-happy is excluded. Conditional and dynamic AI-selected parts need separate reviewed pins/effects/authorization before execution.

Progress is a bounded structured stage path (for example composition/melody), state, monotonic sequence and safe timing. Generation translates authorized Runtime/provider events; it never parses log strings for authority. Studio may render a tree and animate characters. Missing progress remains unknown; percentage is omitted unless measurable. Late/replayed events cannot regress terminal state or trigger execution.

## Implementation and acceptance

Foundation -> immutable include store and one-provider lowering -> actual Music providers under #25/#31 -> Speech -> Video -> Decompose -> explicit Runtime bridge. Existing provider issue acceptance remains authoritative; this design does not close live gates.

Required focused offline cases: v1/v2 regressions; mixed legacy/v3 descriptors; two implementations of one capability; strict ports/MIME/cardinality/defaults and absent or out-of-domain concrete intermediate values; pinned child after active-alias update versus administrative revocation; smoke/production seed variation within a qualified domain versus changed structural/model identity; cross-language canonicalization fixtures; repeated-alias expansion and cycles; registry atomicity/restart/pinning/revocation/GC; whole-plan budgets/hidden nodes; evidence expiry/change before dispatch; missing/invalid required outputs; immutable inputs and cross-scope handles; cancel/timeout/unknown submission with no replay; stale Runtime pins and recursive-reservation rejection. Normal CI requires no GPU, weights or paid API. Real-runtime adapter/composition verification is recorded separately with pinned revisions and safe provenance.

## Tracking

Owning follow-up: [#45](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/45). Cross-repository acceptance stays coordinated by [FLAMORIS AI #15](https://github.com/flamoris-jp/flamoris-ai/issues/15).
