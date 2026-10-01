# Implementation Work handoff: Workflow + Studio img2img

Prepared 2026-10-01. Design phase only. Recommended model: GPT-6.1 Sol; reasoning: High,
because the contract crosses Generation, Studio authorization, persistence, provider
execution, and Hub schemas. Local fixes after the contract is established can use
Medium.

## Start here

Read the design changes in the Generation and Studio design PRs before coding:

- [Canonical Workflow design](WORKFLOW_SYSTEM_DESIGN.md)
- [Studio integration](https://github.com/flamoris-jp/flamoris-studio/pull/37)

If design PRs have not merged, read their branch docs explicitly. Do not assume these
files already exist in main. Recheck current main, AGENTS/README/ CONTRIBUTING/SECURITY,
issue bodies, and open PRs. Compare changes after:

- Generation: cd5d6011bc0e35c274e8a9d7c1780cdd42100b53
- Studio: e89e35e380627c9d4b14d0f59db5acf3cae47745
- Hub: fcbe0243c2e54d77e7ea44dc073c883e90d38cb4

Use connected GitHub for repository/issue/PR operations. Do not conflate failed local
clone/authentication with repository access. Keep source backed by the repositories and
commit each meaningful unit; no large uncommitted implementation.

## Issue ownership

| Issue | Scope for this implementation |
| --- | --- |
| Generation #19 | Complete metadata/discovery/exact-identity production admission and automatic attestation; registry foundation already exists |
| Generation #42 | One-image img2img definition, static semantics, bounded input validation and automated real-runtime verification |
| Studio #21 | Managed-input owner mapping/API/preview and cross-user authorization |
| Studio #36 | Ready-only descriptor selection/role mapping and version/digest-aware snapshots |
| Studio #30 | Existing-Asset reference/restore/UI slice only; keep open for local upload follow-up; preserve implemented features |
| Hub #26 | workflows.verify plus build version/digest/require_ready catalog/signature parity |

Hub #25 / Generation #41 / Studio #35 are a separate external-client
identity/provenance/catalog-import project. Do not implement them here. Local uploads,
IP-Adapter, ControlNet, inpainting, multiple inputs, batch count, general workflow
designer, GPU runtime switching, and multi-provider framework are out of scope.

## Locked design decisions

1. Generation is the definition/recipe/job/input/asset authority; Studio owns user
   authorization and UI/catalog mappings. Hub only forwards.
2. Preserve v1 definitions and builtin txt2img/ordered-LoRA behavior. Add v2 Image
   metadata/semantic roles. Studio never parses graph or guesses by names.
3. First reference semantic is initial-image img2img with explicit center crop/
   resize -> VAE latent initialization -> denoise. It is not character/style
   conditioning. Start from a verified installed-node export, not an invented
   production graph.
4. Input picker selects an existing owned Studio-generated Asset; input CRUD
   currently cannot upload a local file. Reference Image v1 is strictly
   Studio-owned existing generated Asset -> managed input snapshot -> JANKU
   img2img. No PC file picker, drag/drop upload or unimplemented upload button.
   Studio #30 must not close from this slice; local upload -> authorized managed
   input is explicit follow-up scope.
5. Managed inputs use existing immutable snapshots and bounded staging. Source
   deletion does not invalidate an existing snapshot; expiry/revocation does.
6. Preserve latest-only definition storage and monotonically increasing versions.
   Old recipes fail closed; optional definition_version/definition_digest prevent
   stale builds. Studio uses require_ready=true; Generation pins this policy and
   rechecks exact attestation at JobStore admission, before provider submission.
   Accepted provider submissions use captured definition/output metadata.
7. runtime registration remains release/rebuild/restart-free. One-time code/Hub
   signature rollout and infrastructure readiness are separate. Register does
   bounded static validation/publish only; it never waits for generation.
   Trusted candidates activate immediately for testing, not Studio selection.
8. Infrastructure readiness (adapter/provider/upload retention policy) AND exact
   Workflow readiness are necessary for Reference Image. Generation computes
   registered -> validated -> ready; a separate service-owned attestation binds
   workflow_id/definition_version/canonical definition digest. Definitions cannot
   self-declare production_ready. Version OR digest changes invalidate ready.
   Do not claim provider uploads are automatically removed by current code.
9. Exact audited integer dimension binding exceptions fix the Image-name
   heuristic; real file selectors remain managed-only.
10. Explicitly owned input handles and snapshot v2 extend Use settings without
    exposing raw Generation input IDs. Picker availability does not require an
    existing valid input; Generate does. Support initial selection and authorized
    replacement after expiry/revocation/removal.
11. Automatic/Randomize seed samples the descriptor's legal integer domain
    intersected with 0..Number.MAX_SAFE_INTEGER, including ranges, typed enum and
    positive integer multiple_of only. V2 applies it only to integer parameters;
    width/height use 8. Reject invalid divisors; number-parameter decimal steps
    need a future separate contract without float tolerance. Use integer modulo
    and bounded progression/enum sampling, revalidate before build, and snapshot
    the concrete seed before submit. Empty/unsupported domains disable the option;
    explicit zero is preserved and validated, not treated as automatic.

12. workflows.verify takes the registered ID/version/digest and declared bounded
    smoke parameters, builds and submits through ordinary JobStore authority,
    returns a job_id, and exposes outcome through jobs.status/result. Bound smoke
    to the canonical profile/deadline; no separate GPU reservation, direct provider
    submit, unbounded wait or automatic replay. An automatic finalizer verifies
    installed node/provider compatibility, static semantics, managed staging,
    successful completion and declared output retrieval/validation before atomic
    attestation persistence. Human Approve/visual checks are not readiness gates.
13. Reverify after failure is explicit and supported. Failure/timeout/cancel/busy/
    submission_unknown never produces ready or releases uncertain provider work.
    Exact identity is checked at finalization/discovery/build/submit; replacing a
    definition during smoke cannot inherit readiness. Guard finalization with the
    current service-generated attempt ID as well as Definition identity; an older
    attempt cannot publish ready over a newer failed reverify. Production img2img
    admission rechecks infrastructure readiness too. Persist separate bounded attestation
    evidence, revalidate after restart, and never revive pending verification from
    an empty process-local job reservation. Preserve singleton recovery rules.
    Current main releases the JobStore slot on provider-submit exceptions even
    when a transport failure may follow accepted POST. Reservation retention
    after ambiguous acceptance is REQUIRED hardening in the same normal submit
    authority before verify is enabled, not a feature already implemented. Retain
    the unknown-work reservation until reconciliation/explicit safe recovery;
    preserve Studio submission_unknown and no replay.

14. Image roles must bind the reviewed inputs in the effective declared-output
    dependency graph. Reject ignored/disconnected or wrong-semantic bindings;
    non-default dimensions/seed must reach the actual resize/latent/sampler nodes.
    Bound verify against the materialized executed graph, including fixed
    dimensions, sampler steps, batch/output count; caller smoke values alone
    cannot establish its budget. Generation owns these checks; Studio maps roles.
15. Generation signature and deployed Hub catalog must change in one coordinated
    maintenance window. Hub exact schema comparison blocks the whole Generation
    connection during a mismatch, even for legacy calls. Pause all submitters,
    drain/reconcile provider work, replace the Generation singleton, update/restart
    Hub with exact implemented schemas/annotations, validate parity and legacy/new
    calls, then deploy Studio and reopen. Rollback restores a compatible Studio,
    Generation/Hub pair and backed-up persistence together, preserving newer data.
    Never resume from an empty reservation or assume old code reads new schemas.
    Follow the canonical rollout/rollback section; do not guess host commands.

## Implementation order and commits

Use repository-specific branches and PRs; do not commit implementation on these design
branches.

| Order | Repository / meaningful commit |
| --- | --- |
| 1 | Generation: v2 metadata/roles + builtin descriptors + validation tests |
| 2 | Generation: integer-only dimensions/multiple_of + static reference graph validation |
| 3 | Generation: build version/digest/require_ready + admission/accepted-submit races + ambiguous-submit reservation hardening |
| 4 | Generation: img2img definition/export fixture + bounded decode/staging tests + stale managed-input docs |
| 5 | Generation: normal-job workflows.verify + separate durable attestation + infrastructure config/docs |
| 6 | Hub: generated verify/build schemas and annotations + forwarding/parity tests |
| 7 | Studio: normalized descriptors/DTOs + discovery |
| 8 | Studio: managed-input owner table/migration/API/independent thumbnails |
| 9 | Studio: role-based submit + normalized snapshot v2 + stale/reference handling |
| 10 | Studio: dedicated Workflow selector + existing-Asset picker |
| 11 | Studio: result/Use settings restoration + component regressions/docs |

Add focused tests with each commit, rather than holding tests until the end. Use
existing architecture; extracting small contract helpers is fine, unrelated refactors
are not. Preserve commit checkpoints when runtime smoke is blocked.

## Required verification

Generation: include unused/wrong role bindings, non-default effective dimensions/seed,
and materialized sampler/batch/size budget regressions. Existing ruff check/format,
pytest, python -m build, installed-wheel smoke,
Docker smoke from CI. Focus on v1/builtin compatibility, v2 metadata without graph
leaks, role/dataflow validation, dimensions/file-selector regressions, integer-only
divisor/default/enum bounds, registration atomic failure/ restart, self-declared-ready
rejection, missing/mismatched attestation exclusion, automated verify sharing JobStore
reservation, busy/deadline/cancellation/unknown submission, output retrieval/decoding
failure, attestation persistence failure, restart/profile mismatch, version/digest and
finalization/revocation races, version races, decoded PNG/JPEG/WebP and
malformed/animated/pixel-bound images, input expiry/deletion, ambiguous upload/submit
and stage lease behavior.

Studio: PostgreSQL-backed pytest + Alembic migration chain, npm ci, npm run build, npm
test, and Docker packaging/static frontend check from CI. Test source and input
cross-user denial BEFORE upstream calls, CSRF, expiry/ revocation/source deletion, DB
failures and orphan compensation, metadata and arbitrary public-key role mapping,
version-aware snapshots, zero/auto seed, 32-bit maximum/nonzero minimum/enum/positive
integer multiple_of seed domains, singleton/empty domains, invalid explicit zero,
missing seed role, and normalized seed restoration, invalid divisor rejection,
exact-ready selection/ production admission and both infrastructure/Workflow readiness
gates. Preserve builtin/LoRA/Style/preferences regressions. No unimplemented upload UI.
Real mocked component interactions must cover select/attach/remove/replace/ restore,
initial selection with no input, reselection after expiry/revocation/ removal, empty
Asset lists, and Generate blocked until a valid attachment.

Hub: mixed-version whole-connection rejection and paired schema rollback, plus current
test/lint/format checks, present/absent version/digest/require_ready
forwarding, workflows.verify routing and exact generated schema/annotation parity. Do
not change lazy connection or automatic replay behavior. Normal CI never needs a live
GPU, model weights, private tunnel, or paid API.

## Real runtime smoke: after code/CI, separate from offline acceptance

Do not deploy services during this design phase. During implementation prepare the smoke
and report any operator action needed; inspect current deployment docs and approved
server state before issuing repository-specific commands. Server Manager is
authoritative for current infrastructure; Generation tools are authoritative for its
catalog. Use no historical file as live state.

Smoke preparation:

Before deploying the supporting signature change, follow the canonical coordinated
maintenance rollout/rollback: pause all submission ingress, drain/reconcile while
the old pair matches, replace the singleton and deployed Hub catalog together,
then validate legacy/new parity before Studio/reopening. This is a design
requirement, not evidence that deployment or runtime smoke occurred.

1. Record current Generation/Studio/Hub code revisions and infrastructure rollout flags.
2. Verify installed ComfyUI required node schemas (object_info or actual exported
   API graph), model catalog, Clip Skip configuration, sampler/scheduler.
3. Export/review the txt2img and img2img graphs. Record exact definition ID/version
   and canonical content digest; keep private prompts/media/runtime IDs out of
   public GitHub evidence.
4. Register candidate definitions through the trusted runtime path, inspect
   discovery (validated/unavailable), and test restart durability through an
   authorized operational window. Registering alone never enables UI readiness.
5. Start with an existing generated Asset owned by the smoke Studio user.
   If none exists, generate one through Studio; do not claim an arbitrary external
   Generation asset belongs to that user.
6. Invoke workflows.verify with exact ID/version/canonical digest, an authorized
   managed-input snapshot and bounded declared smoke parameters. Poll the normal
   job; the automatic finalizer checks compatibility -> validation -> staging ->
   build -> submit -> successful completion -> declared output retrieval/validation
   and persists readiness. Re-read exact ready descriptor before Studio selection.
   No Approve button, manual per-version promotion, or human image judgement is
   required. ChatGPT/Work/runtime-enabled CI/operations tools can run this sequence.
   Report busy or failure and explicitly reverify; never silently replay submit.

Execution matrix:

- Select the exact automatically attested ready descriptor in Studio, create its owned immutable input,
  show thumbnail, build/submit, poll to completion, synchronize Asset, preview/
  download it, inspect snapshot and Use settings.
- With identical seed/prompt/dimensions/denoise, compare two visibly different
  authorized source images. Verify the actual submitted graph follows the source
  VAE latent path and inspect output differences. No brittle exact-pixel golden
  expectation across runtime versions; visual difference alone is insufficient
  without confirming the graph consumed the source.
- Check low/high denoise behavior and declared center crop/output size.
- Check supported PNG/JPEG/WebP, invalid input, expiry/revocation, no-reference
  rejection and independent source deletion behavior. The picker must permit the
  first attachment and authorized reselection after expiry/revocation/removal;
  Generate stays blocked until a valid reference is attached.
- With a second Studio user, reject create/get/delete/thumbnail/submit/restore of
  the first user's handles.
- Verify snapshot lease releases, provider-upload retention policy, safe failed/
  ambiguous submission behavior, and no removal of original source files.
- Confirm builtin txt2img/LoRA, existing Assets and snapshots still work.

Infrastructure readiness may be enabled once adapter/profile smoke and a safe
provider-upload retention policy are established. That flag never certifies a
Definition. Every production Definition version/digest needs automatic verify and
persisted matching attestation; updated identities immediately become unavailable until
verification succeeds. No per-definition human approval or restart is required. Test a
replaced/failed/retried candidate staying out of Studio production selection and a
self-declared-ready definition being rejected.

Missing deployed node inspection, successful automatic attestation, infrastructure
readiness and provider-upload retention evidence are remaining real-environment checks,
not completed design-phase tests. Browser multi-user/UI smoke is also real-runtime
acceptance; Windows is not a requirement for these Python/web changes.

## PR and final report

Create reviewable PRs per repository, repair CI failures, then self-review the whole
flow including version/input races and authorization. Do not auto-merge; final merges
belong to the user. Do not close a parent issue from a design-only or offline-only PR
when its live acceptance is still outstanding. Even successful existing-Asset img2img
acceptance does not close Studio #30: its PC file picker/ drag-drop local upload ->
authorized managed input remains follow-up scope.

Report implementation/commit/PR list, CI results, issue-by-issue done/residual, live
smoke evidence or exact blockers, deployment order (Generation + Hub schema before
Studio using new arguments), and the user's next concrete action.
