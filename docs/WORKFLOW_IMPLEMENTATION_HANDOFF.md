# Implementation Work handoff: Workflow + Studio img2img

Prepared 2026-10-01. Design phase only.
Recommended model: GPT-6.1 Sol; reasoning: High, because the contract crosses
Generation, Studio authorization, persistence, provider execution, and Hub schemas.
Local fixes after the contract is established can use Medium.

## Start here

Read the design changes in the Generation and Studio design PRs before coding:

- [Canonical Workflow design](WORKFLOW_SYSTEM_DESIGN.md)
- [Studio integration](https://github.com/flamoris-jp/flamoris-studio/pull/37)

If design PRs have not merged, read their branch docs explicitly. Do not assume
these files already exist in main. Recheck current main, AGENTS/README/
CONTRIBUTING/SECURITY, issue bodies, and open PRs. Compare changes after:

- Generation: cd5d6011bc0e35c274e8a9d7c1780cdd42100b53
- Studio: e89e35e380627c9d4b14d0f59db5acf3cae47745
- Hub: fcbe0243c2e54d77e7ea44dc073c883e90d38cb4

Use connected GitHub for repository/issue/PR operations. Do not conflate failed
local clone/authentication with repository access. Keep source backed by the
repositories and commit each meaningful unit; no large uncommitted implementation.

## Issue ownership

| Issue | Scope for this implementation |
| --- | --- |
| Generation #19 | Complete metadata/discovery/version precondition and production JANKU acceptance; foundation already exists |
| Generation #42 | One-image img2img definition, semantics, bounded input validation, smoke evidence |
| Studio #21 | Managed-input owner mapping/API/preview and cross-user authorization |
| Studio #36 | Descriptor discovery/selection/role mapping and version-aware snapshots |
| Studio #30 | Remaining reference/restore/UI integration; preserve implemented Styles/parameters/preferences/seeds |
| Hub #26 | Optional workflows.build definition_version catalog/signature parity |

Hub #25 / Generation #41 / Studio #35 are a separate external-client
identity/provenance/catalog-import project. Do not implement them here.
Local uploads, IP-Adapter, ControlNet, inpainting, multiple inputs, batch count,
general workflow designer, GPU runtime switching, and multi-provider framework
are out of scope.

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
   currently cannot upload a local file.
5. Managed inputs use existing immutable snapshots and bounded staging. Source
   deletion does not invalidate an existing snapshot; expiry/revocation does.
6. Preserve latest-only definition storage and monotonically increasing versions.
   Old recipes fail closed; new optional definition_version prevents stale builds.
   Accepted provider submissions use captured definition/output metadata.
7. runtime registration remains release/rebuild/restart-free. One-time code/Hub
   signature rollout and operator readiness promotion are separate.
8. Required provider readiness and runtime upload-file retention policy must be
   evidenced before enabling production reference UI. Do not claim provider
   uploads are automatically removed by current code.
9. Exact audited integer dimension binding exceptions fix the Image-name
   heuristic; real file selectors remain managed-only.
10. Explicitly owned input handles and snapshot v2 extend Use settings without
    exposing raw Generation input IDs. Picker availability does not require an
    existing valid input; Generate does. Support initial selection and authorized
    replacement after expiry/revocation/removal.
11. Automatic/Randomize seed samples the descriptor's legal integer domain
    intersected with 0..Number.MAX_SAFE_INTEGER, including ranges, typed enum and
    multiple_of. Use exact bounded sampling, revalidate before build, and snapshot
    the concrete seed before submit. Empty/unsupported domains disable the option;
    explicit zero is preserved and validated, not treated as automatic.

## Implementation order and commits

Use repository-specific branches and PRs; do not commit implementation on these
design branches.

| Order | Repository / meaningful commit |
| --- | --- |
| 1 | Generation: v2 metadata/roles + builtin descriptors + validation tests |
| 2 | Generation: precise dimensions/multiple_of + semantic graph validation |
| 3 | Generation: optional build version + accepted-submit version race fix |
| 4 | Generation: img2img definition/export fixture + bounded decode/staging tests |
| 5 | Generation: readiness config/docs + stale managed-input documentation correction |
| 6 | Hub: actual generated workflows.build catalog schema + parity tests |
| 7 | Studio: normalized descriptors/DTOs + discovery |
| 8 | Studio: managed-input owner table/migration/API/independent thumbnails |
| 9 | Studio: role-based submit + normalized snapshot v2 + stale/reference handling |
| 10 | Studio: dedicated Workflow selector + existing-Asset picker |
| 11 | Studio: result/Use settings restoration + component regressions/docs |

Add focused tests with each commit, rather than holding tests until the end.
Use existing architecture; extracting small contract helpers is fine, unrelated
refactors are not. Preserve commit checkpoints when runtime smoke is blocked.

## Required verification

Generation: existing ruff check/format, pytest, python -m build, installed-wheel
smoke, Docker smoke from CI. Focus on v1/builtin compatibility, v2 metadata
without graph leaks, role/dataflow validation, dimensions/file-selector
regressions, model/default/enum bounds, registration atomic failure/restart,
version races, decoded PNG/JPEG/WebP and malformed/animated/pixel-bound images,
input expiry/deletion, ambiguous upload/submit and stage lease behavior.

Studio: PostgreSQL-backed pytest + Alembic migration chain, npm ci,
npm run build, npm test, and Docker packaging/static frontend check from CI.
Test source and input cross-user denial BEFORE upstream calls, CSRF, expiry/
revocation/source deletion, DB failures and orphan compensation, metadata and
arbitrary public-key role mapping, version-aware snapshots, zero/auto seed,
32-bit maximum/nonzero minimum/enum/integral and fractional multiple_of seed
domains, singleton/empty domains, invalid explicit zero, missing seed role, and
normalized seed restoration. Preserve builtin/LoRA/Style/preferences regressions.
Real mocked component interactions must cover select/attach/remove/replace/
restore, initial selection with no input, reselection after expiry/revocation/
removal, empty Asset lists, and Generate blocked until a valid attachment.

Hub: current test/lint/format checks, present/absent version forwarding and exact
schema parity. Do not change lazy connection or automatic replay behavior.
Normal CI never needs a live GPU, model weights, private tunnel, or paid API.

## Real runtime smoke: after code/CI, separate from offline acceptance

Do not deploy services during this design phase. During implementation prepare
the smoke and report any operator action needed; inspect current deployment
docs and approved server state before issuing repository-specific commands.
Server Manager is authoritative for current infrastructure; Generation tools
are authoritative for its catalog. Use no historical file as live state.

Smoke preparation:

1. Record current Generation/Studio/Hub code revisions and operator rollout flags.
2. Verify installed ComfyUI required node schemas (object_info or actual exported
   API graph), model catalog, Clip Skip configuration, sampler/scheduler.
3. Export/review the txt2img and img2img graphs. Record exact definition ID/version
   and canonical content digest; keep private prompts/media/runtime IDs out of
   public GitHub evidence.
4. Register candidate definitions through the trusted runtime path, inspect
   discovery, save an invocation recipe, and test restart durability through an
   authorized operational window. Registering alone must not turn UI readiness on.
5. Start with an existing generated Asset owned by the smoke Studio user.
   If none exists, generate one through Studio; do not claim an arbitrary external
   Generation asset belongs to that user.

Execution matrix:

- Select the registered descriptor in Studio, create its owned immutable input,
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

Only after successful adapter/profile smoke and an explicit safe provider-upload
retention policy should an operator enable the production readiness flag.
Review/smoke new production graph versions before promoting them; no restart
is required just to runtime-register the definition.

Missing deployed node inspection, real img2img smoke, operator readiness and
provider-upload retention evidence are remaining real-environment checks, not
completed design-phase tests. Browser multi-user/UI smoke is also real-runtime
acceptance; Windows is not a requirement for these Python/web changes.

## PR and final report

Create reviewable PRs per repository, repair CI failures, then self-review the
whole flow including version/input races and authorization. Do not auto-merge;
final merges belong to the user. Do not close a parent issue from a design-only
or offline-only PR when its live acceptance is still outstanding.

Report implementation/commit/PR list, CI results, issue-by-issue done/residual,
live smoke evidence or exact blockers, deployment order (Generation + Hub
schema before Studio using new arguments), and the user's next concrete action.
