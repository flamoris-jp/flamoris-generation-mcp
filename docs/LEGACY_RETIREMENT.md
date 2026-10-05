# Custom ComfyWorkFlow subsystem retirement

Authority: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and
[Generation #67](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/67).
The user authorized source deletion, retained internal connections and review/merge;
live operations and deletion of persisted data are outside this change.

| Removed source / surface | Retained contract |
| --- | --- |
| Registry, definition versions, v2 binding/profile and example definitions | Original schema-1 Image templates and generic `WorkflowStore` |
| v3 composition/compiler, qualification and Image v3 provider lowering | Schema-4 Speech, schema-5 Music and schema-6 transcription recipes |
| Verification/attestation and Runtime evidence/delegation/cleanup modules | One ordinary job authority, provenance, uncertainty and existing retention ledgers |
| `workflows.register`, `workflows.verify`, `workflows.v3.*` | `workflows.list/build/save`, jobs, assets and managed input/upload APIs |

The matching Hub static custom tools/v3 example and Studio custom/v3 discovery,
selection and dispatch are retired in their owners. Runtime deletes its Generation
media-lowering bridge while preserving native ExecuteFlow and generic embedding.
Controller is not implemented and receives none of this deleted subsystem.

## Data and uncertainty

The cleanup never deletes saved definitions/recipes, assets, immutable inputs,
historical evidence or active journals. Schema-2/3 saved IDs remain discoverable,
but build/get/save/submit reject custom execution explicitly. The old optional
build definition/readiness pins reject; an unknown template cannot select a builtin.
Completed old-recipe archives remain available through the retained read,
retrieval and deletion APIs. Caller authorization remains at the owning ingress;
an upstream identifier is not an ownership grant. These archives are not
executable graphs and receive no fabricated readiness.

Startup recognizes old schema-2/3, definition, verification or Runtime-delegation
active debt without importing the removed modules. Status reports unknown with
`retired_feature_requires_reconciliation`; the busy reservation and original
`active.json` stay intact across repeated restarts. The new package never polls,
resubmits, cancels or clears that opaque work. Result/cancel rejects with a fixed
reconciliation message. Normal builtin/native recovery and scoped exclusion remain.

Before an operational upgrade, stop admission and drain/reconcile using the previous
matched version and its existing authority. Back up and retain journals, input leases,
outputs and evidence. Do not clear a journal, drop storage or enable a fresh authority
merely to make startup succeed. A missing provider record is insufficient proof of
release. Operational rollout/rollback requires separate authorization and acceptance.

## Evidence and historical reference

`tests/test_legacy_retirement.py` exercises removed tools, explicit rejection,
unchanged saved data, opaque active debt/no replay, baseline restart and old completed
archive retrieval. Retained tests cover providers, recipes, jobs, provenance,
input/upload confinement, retention and bounded transfers. CI builds the installed
wheel and a non-root container without real providers. These do not qualify live GPU
or provider behavior.

Obsolete registration, qualification, v3, multimodal-composition and Runtime-bridge
runbooks were removed from the current document tree on 2026-10-05. They described
removed modules, tools and flags and must not act as operational instructions.
Git history preserves their original contracts and evidence; it is not an instruction
to recreate the subsystem. The historical documents below are pinned to the reviewed
pre-audit commit, rather than following `main`.

- [GENERATION_HUB_DESIGN.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/GENERATION_HUB_DESIGN.md)
- [WORKFLOW_SYSTEM_DESIGN.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/WORKFLOW_SYSTEM_DESIGN.md)
- [WORKFLOW_IMPLEMENTATION_HANDOFF.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/WORKFLOW_IMPLEMENTATION_HANDOFF.md)
- [WORKFLOW_VERIFICATION.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/WORKFLOW_VERIFICATION.md)
- [WORKFLOW_QUALIFICATION.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/WORKFLOW_QUALIFICATION.md)
- [WORKFLOW_V3_FOUNDATION.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/WORKFLOW_V3_FOUNDATION.md)
- [IMAGE_V3_EXECUTION.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/IMAGE_V3_EXECUTION.md)
- [RUNTIME_DELEGATION.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/RUNTIME_DELEGATION.md)
- [MULTIMODAL_WORKFLOWS.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/c3f8eee64283aab3e148c20ec737bc8002edb66a/docs/MULTIMODAL_WORKFLOWS.md)
