# Opt-in Image v3 execution

> Historical and superseded by [the implemented retirement](LEGACY_RETIREMENT.md) under AI #18. Custom graph registration, qualification, composition and Runtime delegation described below are removed or held; these are not current source/configuration or rollout instructions. Retained data and uncertainty fences remain protected.

> **Architecture correction (2026-10-04):** use **ComfyWorkFlow** for ComfyUI execution definitions/graphs. Do not use bare `Workflow` as new architecture terminology. Existing `workflows.*`, `WorkflowDefinition`, filenames, schemas, and test identifiers below describe the current implementation and are not renamed by this documentation-only change. Generation Controller remains unimplemented. When Generation work is explicitly resumed, the embedded Generation MCP ComfyWorkFlow implementation is to be **removed from this MCP repository rather than migrated into Controller**. FLAMORIS AI is prioritizing Intelligence-boundary cleanup first. See [flamoris-ai#18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Generation MCP #67](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/67).


This delivery advances #45 with a usable MCP path for one reviewed Image leaf,
including nested pass-through compositions and renamed public ports. It does not
complete the multimodal umbrella: multiple native components, intermediate media,
cross-provider execution, Music, Speech and Video need separate reviewed profiles.

Set `FLAMORIS_WORKFLOW_V3_ENABLED=true` to add the five separately named tools
below. Defaults retain exactly the legacy tools, descriptors, recipes and Image
attestation namespace. Enable this catalog with the matching optional Generation
v3 Hub template; the default Hub catalog continues to match legacy tools. There
is no runtime activation, deployment, workflow registration or generation at startup.

| Tool | Contract |
| --- | --- |
| `workflows.v3.register` | Strict schema-3 definition; publishes a validated candidate with retained history |
| `workflows.v3.list` | Graph-free descriptor revision 3; current exact root qualification and measured model domain |
| `workflows.v3.build` | Exact definition id/version/digest and scalar parameters; production requires attestation by default |
| `workflows.v3.verify` | Exact whole-root smoke through the ordinary JobStore and runtime evidence guard |
| `workflows.v3.revoke` | Administrative exact-version revocation; dependent pinned compositions become unusable |

The existing `workflows.save`, `jobs.*`, `assets.*` tools consume the built opaque
workflow handle. A build with `require_ready=false` permits candidate inspection
and saving; ordinary `jobs.submit` rejects it. Verification alone admits an
unqualified candidate. There is no caller-supplied readiness receipt.

## Supported profile

`image-generate-v1` revision 1 uses ComfyUI adapter revision 1, compiler revision 2,
and the current audited txt2img Image-v1 topology. Exactly one leaf and one
`primary_image` output are required. Native `SaveImage` collection index zero is a
final published image collection, never an intermediate native graph edge.

Public scalar inputs have explicit Image role/schema mappings and finite bounds.
Required checkpoint and positive prompt remain required; optional inputs require
explicit defaults. Width/height are paired multiples of eight, 64..4096; steps
1..150, seed 0..2^53-1, cfg 0..100 and denoise 0..1. Actual declared input domains
may be narrower. Checkpoint discovery and ambiguity checks use the existing model
catalog. Sampler/scheduler and Clip Skip are definition-owned bounded constants.
One latent image, no hidden/disconnected execution and the existing node/JSON,
64 MiB output and media-transfer ceilings remain authoritative. No LoRA or managed
asset input is exposed by this profile. Effects must explicitly include external
and write; declared deadlines are exactly 300 seconds and output assets exactly one.

Each nested wrapper must pass public inputs one-to-one to the leaf and publish
its one image. Static compilation validates every include and parent contract;
the lowerer then validates the entire concrete Image graph again. Outputs carry
explicit public port, primary-image role and collection index through assets and
transfers. Required cardinality/MIME are checked on observed completion; downloaded
and streamed bytes also undergo bounded single-frame format/dimension validation
before a local artifact can commit. Stream errors discard the destination's partial
file. Malformed provider history remains uncertain, retaining the reservation.

## Identity and qualification

Definitions retain immutable id/version/digest independently of the active alias.
A parent or saved recipe never silently follows a newer child. Retained history
has conservative retention and no GC. Revocation is checked during build,
admission, immediately before POST and before attestation promotion. An already
accepted execution retains its captured output contract for reconciliation.

A recipe pins the root, closure digest, structural plan digest, compiler/adapter
revisions and concrete lowered-invocation digest. Allowed prompt/seed/scalar
changes preserve structural identity and change invocation identity. Restart
rebuilds and compares pins; mutated or incompatible recipes reject.

Separate durable v3 attestations pin the full structural identity, measured smoke
invocation digest, reviewed `image-v1-bounded-scalars` domain revision 1, existing
runtime fingerprint/epoch, checkpoint content digest and decoded smoke output.
The smoke uses at most 512x512 and 30 steps. The reviewed domain permits subsequent
validated scalar changes within the exact root bounds and measured checkpoint;
it does not qualify arbitrary profiles, graph constants or other models. Child
qualification alone cannot make a parent ready. Runtime evidence changes or
expiry remove readiness, including from discovery. Infrastructure gating for
managed inputs remains independent and is outside this txt2img delivery.

Verification and ordinary generation share one durable process-owned reservation.
Busy verification preserves a previous ready record; an admitted attempt supersedes
it. Interrupted verification cannot promote after restart. Unknown POST acceptance
retains the reservation and never replays. Do not disable v3 while a v3 reservation
needs reconciliation: startup intentionally refuses an incompatible active journal.

The production watcher requests targeted cancellation once after the 300-second
wall-clock deadline. Known terminal cancellation releases the reservation;
unconfirmed running/unknown work keeps it. An output first observed after expiry
cannot become successful. Previously completed results remain retrievable. After
restart, explicit polling reconciles the captured reservation; there is no implicit
resume or automatic resubmission and no fallback to global interruption.

## Exercise with an installed model

The portable examples are `examples/v3/image-leaf.json` and
`examples/v3/image-parent.json`. They contain no weights, generated media or host
configuration. Register the leaf first, then its pinned parent. If editing either
contract, increment its version and recompute the include's digest using
`workflow_v3.Definition.model_validate(value).digest`; never hand-edit a receipt.

1. Configure a real ComfyUI service, local model catalog and fresh mutation-locked
   runtime evidence as described in [WORKFLOW_VERIFICATION.md](WORKFLOW_VERIFICATION.md).
2. Enable the v3 catalog and its matching Hub template, then register the examples.
3. Call `workflows.v3.list` to obtain the parent's exact identity.
4. Call `workflows.v3.verify` with that identity and scalar parameters including
   an actually installed checkpoint and a short positive prompt. Poll the returned
   Job until its verification state is ready. Health alone is insufficient.
5. Build the exact parent with `require_ready=true`, save its returned handle if
   needed, submit with `jobs.submit`, and retrieve using existing job/asset tools.
6. Verify output dimensions, roles, runtime identity and cancellation behavior on
   the real installation before enabling a product route.

Normal tests use actual adapters over mocked HTTP, valid image decoding, real
durable stores and MCP protocol calls. CI does not require GPU/model installation.
Real runtime smoke and a Studio v3 product route are separate rollout gates.
