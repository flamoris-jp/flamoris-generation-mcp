# FLAMORIS Generation MCP

External MCP facade for FLAMORIS generation capabilities. Part of [FLAMORIS AI](https://github.com/flamoris-jp/flamoris-ai).

## Current correction and as-built implementation

[AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Generation #67](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/67) define the corrected target: ChatGPT -> MCP Hub -> Generation MCP -> internal generation capability. Internal Studio/Agent/Runtime calls do not use MCP/Hub in the target architecture.

The current Python package still co-locates MCP transport with provider registries, generation recipes/jobs, inputs/references and assets. ComfyUI is the initial provider; opt-in Irodori, YuE2 and SheetSage2 paths have separately documented support and acceptance. This documentation does not migrate or remove that implementation.

**Intelligence cleanup comes first. Generation Controller remains unimplemented.** The existing MCP-side ComfyWorkFlow subsystem is for later removal, not transfer into Controller or automatic recreation there. Exact source/tools/callers/tests and retained data must be inventoried before a separately authorized deletion. Generation/reference-image development and rollout remain paused. Current work is documentation review/fixes and requested documentation merges only.

## Terminology

`ComfyWorkFlow` means a ComfyUI execution graph/API-format JSON and its declared bindings. `ExecuteFlow` means Runtime inference flow; `ExecutionPlan` remains Runtime's compiled representation. These are distinct. Non-ComfyUI generation requests/recipes are not automatically ComfyWorkFlow.

Existing literal names such as `workflows.*`, `workflow_id`, `WorkflowDefinition`, `WorkflowStore`, environment variables and WORKFLOW-named files remain unchanged until an explicit compatibility-reviewed implementation change. New architecture prose should not use bare Workflow. Historical design and current wire identifiers must retain their actual names rather than pretending a rename is implemented.

## Existing definition, recipe and job identities

| Concept | Current meaning |
| --- | --- |
| Registered definition identity | Trusted definition ID/version/digest and declared parameter/input bindings |
| Built recipe handle, returned as `workflow_id` | Result of `workflows.build`; used by `workflows.save` and `jobs.submit` |
| Generation `job_id` | One admitted generation/verification attempt with its own state and provider mapping |
| Asset/input identity | Managed output or immutable input reference, not a host path or proof of user authorization |

Do not call the built handle a registered definition ID. A registered definition can be selected as the build `template`, but submit consumes the resulting built recipe handle. Provider-specific graphs are inspectable exports, not caller-mutable submission authority. Versioned recipes retain their definition identity under the current contract.

For ComfyUI, the builder applies allowed values/reference bindings and constructs JSON; ComfyUI executes the nodes. JSON construction, submission and production qualification are separate operations. Agent, a generic scheduler and an AI Runtime bridge are not mandatory for simple JSON building.

## Existing tool families

The actual server schemas remain authoritative; this overview is not a newly versioned catalog.

| Family | Existing responsibility |
| --- | --- |
| `system.health`, `capabilities.list/get`, `models.list/get` | Process/provider status and configured metadata; discovery is not model readiness |
| `workflows.list/register/build/save` | Current trusted definitions, validated parameter binding and built/saved recipes |
| `workflows.verify` | Bounded automatic Image qualification through the normal job authority |
| `jobs.submit/status/result/cancel` | Admitted generation and truthful terminal/unknown results |
| `assets.list/get/delete`, `assets.prepare/read` | Metadata, selected bounded content retrieval and managed-copy deletion |
| `inputs.create/get/delete`, `inputs.upload.begin/write/finish` | Immutable managed references and bounded uploads |
| Opt-in `workflows.v3.*` | Separately cataloged Image v3 profile; not proof of all-media composition support |

Keep current exact argument/schema/annotation matching between MCP Hub and the upstream. The architecture correction does not change tools, wire IDs or catalog configuration. Current internal consumers are an audit baseline, not an instruction to keep using MCP internally.

## Existing state and safety guarantees

One active process owns ProviderRegistry, CapabilityRegistry, WorkflowStore and JobStore for its shared resource pool. Do not run parallel workers, overlapping replacements or a second stdio process against that pool. The submission reservation is process-owned with a durable conservative recovery journal, not a distributed/host-wide generation lock. Direct provider submissions bypass it. GPU Node Manager retains host-wide lifecycle control.

Provider availability is separate from process health. Optional providers do not load automatically, switch runtimes, download weights or grant fallback. Capability/model discovery and static definition validity do not prove installed compatibility or production readiness.

Keep registered/validated candidate state separate from exact automatic verification and computed ready status. Reference images require their own managed-input infrastructure and semantics in addition to per-definition qualification. Existing verification, evidence continuity and input protections remain applicable to retained paths; feature retirement must not create a bypass or a fabricated ready result.

Never retry an ambiguous accepted submission automatically or equate disconnect/cancel with stopped provider work. Retain uncertain reservation until justified reconciliation. Restart/upgrade must drain/reconcile and use one authority; missing queue/history alone does not prove release.

Metadata and binary materialization are separate. Completed manifest listing does not guarantee an unmaterialized provider output remains fetchable after restart. `assets.delete` removes the managed copy/catalog entry, not provider originals; original retention is a separate explicit operation. Keep individual asset selection, digest verification, bounded chunks and caller authorization.

Managed input snapshots are immutable and separate from source assets. Source deletion does not revoke a published snapshot automatically; expiry and in-use leases have their own contract. Accept no arbitrary caller URL, host path or provider filename. Preserve bounded decoding, pixel/byte/storage limits, safe staging, exact ownership and crash/uncertain-work accounting. Studio is still its users' authorization boundary; possession of a raw ID or signed provenance alone grants no access.

## Existing installation and configuration reference

Python 3.11+ and the relevant independently configured providers are required. The generation server's filesystem contract is Linux/POSIX. These commands describe current package entry points and are not deployment instructions for this paused correction:

```sh
python -m venv .venv
# Activate the environment before installing.
python -m pip install -e '.[dev]'
flamoris-generation-mcp
```

stdio is the default. Explicit HTTP example:

```sh
flamoris-generation-mcp --transport streamable-http --host 127.0.0.1 --port 8765 --mcp-path /mcp
```

The current HTTP surface has no application-level authentication. Keep SDK Host/Origin checks and an operator-controlled authenticated/network boundary. A proxy/tunnel is separately configured; the package does not manage it. Provider requests use configured trusted endpoints without redirects/environment proxies. No runtime operations are performed in this documentation pass.

Important current paths are **not interchangeable**:

| Variable | Default | Meaning |
| --- | --- | --- |
| `FLAMORIS_MODEL_ROOT` | `models` | Model metadata scan root |
| `FLAMORIS_WORKFLOW_DIR` | `.generation/workflows` | Saved parameter recipes, not registered definitions |
| `FLAMORIS_WORKFLOW_DEFINITION_DIR` | `.generation/definitions` | Trusted registered definitions, not the saved-recipe directory |
| `FLAMORIS_OUTPUT_DIR` | `.generation/outputs` | Managed outputs and state |
| `FLAMORIS_COMFYUI_URL` | `http://localhost:8188` | Independently configured ComfyUI endpoint |
| `FLAMORIS_WORKFLOW_V3_ENABLED` | `false` | Opt-in v3 catalog/profile flag |

Current Docker mappings use `/data/workflows` for recipes and `/data/definitions` for definitions. Do not rename variables or point one storage role at another because terminology changed.

The complete [pre-correction README reference](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/b3dab9b72af0dd2bf0b67361639ce3e31688d476/README.md) preserves detailed configuration, CLI examples, singleton/retention caveats and current entry-point behavior. It is an as-built reference, not permission to follow old expansion/rollout instructions during the hold.

## Current contracts and historical design

The following files retain current/historical literal names. Their as-built semantics and acceptance records remain useful, but old implementation/expansion directions do not override #18.

- [Automatic Image verification](docs/WORKFLOW_VERIFICATION.md) and [installed qualification reference](docs/WORKFLOW_QUALIFICATION.md)
- [Managed inputs](docs/MANAGED_INPUTS.md) and [bounded asset transfer](docs/ASSET_TRANSFER.md)
- [Native music providers](docs/MUSIC_PROVIDERS.md) and [Irodori profile](docs/IRODORI_PROVIDER.md)
- [External provenance](docs/EXTERNAL_PROVENANCE.md)
- [Image v3 execution](docs/IMAGE_V3_EXECUTION.md) and [historical v3 foundation](docs/WORKFLOW_V3_FOUNDATION.md)
- [Historical Generation Hub design](docs/GENERATION_HUB_DESIGN.md), [multimodal proposal](docs/MULTIMODAL_WORKFLOWS.md) and [existing Runtime delegation](docs/RUNTIME_DELEGATION.md)

Historical generic media composition must not be relabeled ComfyWorkFlow indiscriminately. Neither it nor Runtime delegation is a prerequisite for Intelligence cleanup or a simple ComfyUI JSON builder.

Normal CI uses fake providers and bounded fixtures without live GPU, weights or paid APIs. Later changes must validate applicable source/tests, lint/format and package behavior. Real generation, mounted storage, provider qualification, cutover and rollback require separate authorization/evidence. This PR changes only Markdown and runs no providers.

## 日本語

MCPは外部入口です。内部の生成制御は別境界ですが、Controllerはまだ実装しません。Intelligence整備を先行し、Generation MCPの既存ComfyWorkFlow実装は後の削除対象として整理します。移植や同じ仕組みの再作成は指示していません。

ComfyWorkFlowはComfyUI用JSON、ExecuteFlowは推論フロー、ExecutionPlanはRuntimeのコンパイル済み表現です。既存の`workflow_id`はbuild結果のrecipe handleで、登録definition IDとは別です。recipe保存先とdefinition保存先も区別し、名称変更だけで設定や実データを変更しません。

## License and support

Code/docs are [Apache-2.0](LICENSE) unless stated otherwise. Models, weights, datasets, provider assets and generated media may have separate terms. FLAMORIS is provided as-is without guaranteed individual support; repository documentation, Issues, tests and source are the primary self-support references. Generic non-AI foundations belong in [FLAMORIS Commons](https://github.com/flamoris-jp/flamoris-commons).
