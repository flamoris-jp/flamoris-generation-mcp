# Automatic Workflow verification

`workflows.verify(workflow_id, definition_version, definition_digest, parameters)`
admits one bounded Image-v1 smoke using the ordinary JobStore. Poll its returned
job ID through `jobs.status` / `jobs.result`. The background observer also finalizes
without requiring a client to keep polling. Registration remains static validation;
only a separately persisted successful current attempt confers ready. Verification
allows one image, effective dimensions <=512, steps <=30, and a 300-second deadline.
Failure, cancellation, timeout, uncertain submit or output decoding failure leaves
the admitted attempt unavailable. A timeout is not proof that provider work stopped.

Production recipes pin version/digest and `require_ready=true`. Discovery, build,
admission and the pre-POST staging boundary check current evidence. Builtins retain
their compatibility policy; schema-v1 definitions require an Image metadata upgrade.
Checkpoint selection must match the content identity tested by the attestation.
Ready descriptors constrain the checkpoint enum to that tested model. The deadline
covers provider admission, background observation and output materialization;
an expired observation retains the ordinary active reservation for reconciliation.

## Runtime evidence authority contract

Readiness defaults off. `FLAMORIS_RUNTIME_EVIDENCE_FILE` points to a private record
published and continuously refreshed by a **trusted runtime mutation authority**.
This is not a manually authored verification receipt or a per-Workflow approval.
The authority implementation/deployment is a runtime prerequisite: absent that
authority, do not fabricate the record from object_info, model names or stat data.
Generation does not itself control ComfyUI mutations.

The publisher maintains a stable regular `<record>.lock` inode. Before every
core/dependency/config/node/model mutation or provider restart, it acquires that
file's exclusive flock, withdraws evidence, advances a never-reused provider epoch,
performs the mutation, measures content, atomically publishes evidence and releases
the lock. Generation takes nonblocking shared locks for readiness checks and across
staging through provider POST. The publisher must encompass **all** mutations and
out-of-band writers; if it cannot, production readiness cannot be established.
Keep record/lock paths and their parents writable only by that trusted service.

Record schema v1:

- `schema_version`: 1
- `continuity`: `exclusive-mutation-lock-v1`
- `provider_url`: exact configured provider URL, with trailing slash removed
- `provider_epoch`: unique 16..128-character alphanumeric/underscore/hyphen ID
- `expires_at`: Unix time, future and at most 300 seconds from each read
- `manifest`: measured SHA-256 identities for `core`, `dependencies`, `config`;
  `nodes` maps installed class names to `interface` and `implementation` hashes;
  `models` maps kind/name identities to actual model content hashes.

Records are <=1 MiB; malformed, expired, unavailable or locked evidence fails
closed. A changed epoch or canonical manifest fingerprint invalidates old ready,
including same-name content replacement. Internal manifest information stays out of
discovery. Generation restart only preserves completed exact attestations with
current evidence; interrupted admitted attempts cannot resume into ready.

`FLAMORIS_MANAGED_INPUT_READY=true` is the independent infrastructure gate. Enable
it only after bounded staging/decode, the installed img2img export and provider
upload retention have real operational evidence. It never makes a Definition ready.
The packaged reference graph is a candidate fixture, not a verified installed graph.

## Coordinated rollout

Pause ingress, drain and reconcile existing provider work, update the Generation
singleton and exact deployed Hub catalog in the same maintenance window, restart
Hub, verify full schema/annotation parity on a fresh connection, then deploy Studio
and reopen. Mixed schemas block the entire Generation connection, including health
and status. Roll back a compatible Studio/DB, Generation/catalog pair and persistence
together while preserving newer data. Follow WORKFLOW_SYSTEM_DESIGN.md; no automatic
replay, merge or deployment is performed by implementation tests.

Real installed graph/node inspection, trusted runtime mutation authority, retention
evidence and successful automatic live verification remain operational acceptance.


Public discovery is bounded to 128 entries and 256 KiB of serialized JSON,
including legacy descriptor aliases and recipe IDs. Catalog overflow returns a
bounded availability error. Ready checkpoint defaults and enums both use the
measured model without altering the canonical Definition or digest. Fixed
Image dimensions cannot have editable bindings, including roleless aliases;
materialized production graphs recheck effective dimension bounds before POST.


Image profile revision 2 rejects every node outside the declared SaveImage's
dependency graph, including disconnected custom output nodes. The complete
submitted graph is therefore covered by topology and runtime node evidence.
Remove unused nodes from exported definitions before registration or startup;
unsupported persisted definitions fail validation. Revision-1 attestations do
not confer readiness after this upgrade; run automatic verification again.
Managed reference decoding rejects any dimension above 4096 before upload,
while retaining the 16-megapixel aggregate bound and 512-pixel smoke-output bound.

Scalar `number` enums compare numeric values across JSON integer and float
representations (for example, `4` and `4.0`). Boolean and string values remain
invalid numbers; `integer` parameters retain strict integer validation.
