# Bounded Workflow qualification observations

> Historical and superseded by [the implemented retirement](LEGACY_RETIREMENT.md) under AI #18. Custom graph registration, qualification, composition and Runtime delegation described below are removed or held; these are not current source/configuration or rollout instructions. Retained data and uncertainty fences remain protected.

The installed client observes the existing MCP authority; it is not a readiness
publisher or another Generation process. It does not replace the installed graph,
mutation-continuity, provider-upload retention and Studio acceptance requirements
in [WORKFLOW_VERIFICATION.md](WORKFLOW_VERIFICATION.md).

First complete the coordinated Generation/Hub catalog rollout and establish the
real runtime prerequisites. Obtain one current exact ID, version and canonical
digest from `workflows.list` (or `workflows.v3.list`). Prepare a private scalar JSON
parameter file using that definition's declared parameter names. Img2img uses an
existing authorized managed input; this command never creates an input, transfers
an arbitrary external asset to a Studio user or uploads a local file.

## Preflight, then one explicit smoke

Provide the selected endpoint and exact identity explicitly. The example names
are environment-neutral placeholders; use the real values obtained from discovery.

```sh
python -m flamoris_generation_mcp.qualification \
  --url "$GENERATION_MCP_URL" \
  --workflow-id "$WORKFLOW_ID" \
  --definition-version "$DEFINITION_VERSION" \
  --definition-digest "$DEFINITION_DIGEST" \
  --parameters private-smoke-parameters.json \
  --receipt private-preflight-observation.json
```

The default is read-only: process/provider availability, reservation busy state
and exact current definition identity are checked. `preflight_passed` does not
validate the executed graph's smoke budget, establish runtime continuity or prove
production readiness. Those remain ordinary `workflows.verify` checks.

For one smoke, repeat with explicit `--execute` and a **new** receipt filename.
Add `--tool-prefix generation.` when targeting Hub; empty prefix means direct
Generation. The command never guesses another namespace or falls back to another
endpoint. Add `--v3` only for the opt-in v3 catalog; it uses `workflows.v3.list`
and `workflows.v3.verify`, sharing the same ordinary job tools.

Authentication headers come only from an explicitly named environment variable:
`--headers-env PRIVATE_MCP_HEADERS`. Its value is a private JSON object such as
`{"Authorization":"Bearer YOUR_TOKEN"}`. Supply it through your normal secret
mechanism; never put credentials in the endpoint URL or command arguments. URL
userinfo, query and fragment are rejected. Protocol/routing header overrides,
header newlines and duplicate names are rejected. No credentials, parameters,
provider errors, URLs, filesystem paths or runtime manifests appear in receipts.

## Outcomes and recovery

`--execute` sends at most one verification admission request. It uses the MCP
session primitive without the SDK convenience method's header/input-required
retry behavior. Verification shares the service's normal JobStore reservation;
the client never directly submits to ComfyUI. On success it checks the same job,
attempt and exact definition identity, observes completed output evidence through
`jobs.result`, then rereads the exact current ready descriptor. Output bytes and
provider paths stay out of the receipt.

| Outcome | Meaning and next action |
| --- | --- |
| `preflight_passed` | Read-only prerequisites observed; no smoke admitted |
| `verified` | Normal verification and exact current ready descriptor observed; continue independent infrastructure and Studio acceptance |
| `definition_changed` | Latest discovery differs from the requested pin; inspect the new definition, never silently select latest |
| `provider_unavailable` / `generation_busy` | No smoke requested; restore provider availability or reconcile existing work through its owning authority |
| `submission_unresolved` | Admission may have occurred; inspect `system.health` and reconcile its active job before any explicit new attempt |
| `observation_timeout` / `interrupted` | Observation stopped; saved accepted job ID remains available for ordinary polling; do not infer provider work stopped |
| `verification_failed` / `current_readiness_unavailable` | No ready qualification claimed; inspect the job/runtime and explicitly reverify only after reconciliation |
| `configuration_unavailable` | Check URL, identity, private parameter/header configuration and that the receipt path does not already exist |
| Other unavailable outcomes | A bounded response, identity, transport or receipt operation failed; no readiness is granted |

The independent managed-input infrastructure flag is recorded as observed. A
successful img2img Workflow attestation does not enable that flag, demonstrate
provider-upload retention or test production admission. This command does not
build/submit a second production job or test Studio authorization/UI behavior.

Receipts are private observation files with mode `0600`, not service-owned
attestations. Existing files are refused before upstream calls. An accepted job
ID is saved before polling; updates are atomically replaced and synced. Do not
copy operational job/attempt identifiers to public issues. Stdout contains one
sanitized final JSON observation; use `--receipt` to preserve progress during a
client interruption. If admission failed before a job ID was received, the
service's durable active reservation remains the recovery authority.

The entire connection/observation is bounded by `--timeout` (default/max 330
seconds), each tool wait by 30 seconds, catalog by 128 entries, parameter file
by 256 KiB and network response/SSE event by 1 MiB. The client requests identity
encoding and rejects compressed responses before decompression. It does not
change the service's 300-second verification budget. Timeouts, malformed replies,
connection loss and input-required replies never trigger admission replay or
automatic job cancellation. Exit status is zero only for `preflight_passed` or
`verified`, one for unavailable/failed outcomes, and 130 for a client interrupt.
