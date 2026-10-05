# FLAMORIS Generation MCP

External MCP facade for FLAMORIS generation capabilities. Part of
[FLAMORIS AI](https://github.com/flamoris-jp/flamoris-ai).

## Implemented cleanup

[AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and
[Generation #67](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/67)
authorize deletion of the mistakenly added custom ComfyWorkFlow subsystem.
Registration/versioning, automatic graph qualification, v3 composition and
Generation-to-Runtime delegation were removed. Nothing is migrated or recreated
in Generation Controller. The retained domain is now extracted into its MCP-free package; the deleted subsystem is not copied.

Original bounded builtin Image recipes and the opt-in native Irodori, YuE2 and
SheetSage2 recipes remain in the pinned Controller package. This facade constructs
one Controller runtime and calls its ordinary typed contract. Studio calls the
authenticated non-MCP HTTP API hosted on the same listener. Both paths share jobs,
inputs, assets and one reservation; live cutover remains separately pending.

Read [retirement and retained data](docs/LEGACY_RETIREMENT.md) before any operational
upgrade. Source cleanup performs no deployment, provider call, DB/data deletion,
credential change or runtime switch.

## Retained external contracts

Controller #6 adds a separate bounded checkpoint profile through `comfy.register`
and `comfy.get`: immutable txt2img/img2img API-format graphs, managed initial
images and schema 7 recipes. This is static validation, with live readiness
unverified. Retired schema 2/3, v3 and custom-node graphs remain unavailable.
The external catalog now has 25 tools. See [reference registration](docs/COMFY_REGISTRATION.md).

| Tool family | Current responsibility |
| --- | --- |
| `system.health`, `capabilities.list/get`, `models.list/get` | Configured metadata and bounded status; not production qualification |
| `workflows.list/build/save` | Original builtin Image and enabled native parameter recipes |
| `jobs.submit/status/result/cancel` | One admitted job authority, durable reservations, truthful uncertainty |
| `assets.list/get/delete`, `assets.prepare/read` | Safe metadata, bounded content retrieval, managed-copy deletion |
| `inputs.create/get/delete`, `inputs.upload.begin/write/finish` | Immutable snapshots, bounded uploads and in-use/retention guards |

`workflows.register`, `workflows.verify` and every `workflows.v3.*` tool are
retired. `definition_digest` is supported for the new registered profile; old
definition-version/readiness arguments cannot enable retired qualification or
versioning and reject unsupported use explicitly. Saved schema-2/3 recipes remain on disk and discoverable but cannot
execute or be rewritten through save. No unknown custom template falls back to a
builtin recipe. Retained custom in-flight debt stays reserved as unknown and is
never replayed by the new package.

## Names and ownership

`ComfyWorkFlow` is ComfyUI graph/API-format JSON. ComfyUI executes its nodes; the
builtin builder constructs JSON. Runtime `ExecuteFlow` and compiled `ExecutionPlan`
are distinct and do not own graph building. Non-ComfyUI recipes are not graphs.
Existing literals such as `workflows.*`, `WorkflowStore`, `workflow_id` and
`FLAMORIS_WORKFLOW_DIR` remain compatible. A built `workflow_id` is a recipe handle,
not a definition ID. GPU Node Manager retains host-wide lifecycle authority.

One Controller authority handles all retained providers. A lifetime output-root
lock rejects another Controller process before construction/recovery; it does not
constrain old pre-lock binaries or coordinate different roots/hosts. Provider
absence, static validity and live qualification are different. No implicit model
download, GPU switch, arbitrary URL/path, raw graph submission or provider fallback.
Missing provider history does not prove uncertain work stopped. Output metadata,
binary materialization and provider-original retention remain separate operations.

## Install and configuration

Python 3.11+ and Linux/POSIX filesystem semantics are required. Development checks
need no GPU, weights or live provider:

```sh
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest
python -m build
```

The default entrypoint is stdio; explicit HTTP uses `flamoris-generation-mcp
--transport streamable-http`. HTTP still needs an operator-controlled authenticated
network boundary; Host/Origin checks do not grant user access. Studio independently
scopes every user job/input/asset reference.

The internal API is POST `/api/v1/generation/{operation}` on the same HTTP
listener. Configure a private `FLAMORIS_CONTROLLER_TOKEN` (32–512 printable ASCII
characters); unset means internal operations are unavailable. Studio sets the exact
API base as `STUDIO_GENERATION_ENDPOINT`, the same service secret as
`STUDIO_GENERATION_TOKEN`, and an empty `STUDIO_GENERATION_NAMESPACE`. This is
separate from external MCP/Hub ingress credentials and signed provenance. The
service credential grants a trusted backend the bounded operation surface; Studio
retains user authorization. No MCP fallback or automatic uncertain-submit retry.
See [Controller API](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/API.md).

`FLAMORIS_MODEL_ROOT`, `FLAMORIS_WORKFLOW_DIR`, `FLAMORIS_OUTPUT_DIR` and
`FLAMORIS_COMFYUI_URL` retain their existing meanings. Docker retains models,
recipes and outputs; obsolete definition/attestation mounts and environment defaults
were removed without deleting host data. Retired feature flags/configuration no
longer enable any source or tool. Exact installed schemas and Hub static catalogs
must match before a separately authorized rollout. Domain/provider tests now live
with Controller; facade and mixed ingress tests remain here. The retained
`python -m flamoris_generation_mcp.retention` command delegates to Controller.
Drain/reconcile and stop any previous owner before upgrading; preserve all active
journals and never delete the lifetime lock while a Controller is alive.

## Retained references

- [Managed inputs](docs/MANAGED_INPUTS.md), [asset transfers](docs/ASSET_TRANSFER.md)
  and [external provenance](docs/EXTERNAL_PROVENANCE.md)
- [Irodori](docs/IRODORI_PROVIDER.md), [Music](docs/MUSIC_PROVIDERS.md)
  and [SheetSage2](docs/SHEETSAGE2.md)
- [Removed source, historical contracts and reconciliation](docs/LEGACY_RETIREMENT.md)

Historical design documents are marked superseded. They preserve old evidence;
they do not authorize restoring the rejected subsystem. Normal tests/package/CI
are separate from live host/provider qualification and deployment acceptance.

## 日本語

誤って追加した独自ComfyWorkFlowの登録・検証・v3構成・Runtime橋渡しを削除しました。
元のImageテンプレートとnative provider recipe、入力・資産・未確定予約の保護は維持します。
削除済みの独自機能は再作成していません。残る生成処理はControllerへ移し、StudioのHTTP入口と外部MCP入口が同じ実行予約を使います。実機切替・保存データの削除は行っていません。
その後、別の限定profileとして標準checkpointのtxt2img／img2img登録と管理参照画像を実装し、mainへマージしました。外部toolは25件です。旧schema 2/3・v3や任意custom nodeは対象外で、実機生成は未確認です。

## License and support

Code/docs are [Apache-2.0](LICENSE) unless otherwise stated. Provider/model/data/media
terms remain separate. FLAMORIS is provided as-is without guaranteed individual support.
