# Managed inputs (Issues #30 and #64)

After [custom ComfyWorkFlow retirement](LEGACY_RETIREMENT.md), immutable input/upload APIs, leases and retention remain. Image builtin recipes do not consume managed references; references to custom Image binding or verification below describe historical use. Native transcription keeps its separately reviewed input contract. Existing input snapshots and active debt are not deleted or replayed.

An existing generated asset can be copied into an immutable managed input.
Trusted clients can also upload bounded local images using the protocol below.
URLs, host paths and provider filenames are not accepted.
`inputs.create(asset_id)` returns an opaque input ID, source asset ID, SHA-256,
media type, size, creation time and expiry. `inputs.get(input_id)` reads metadata;
`inputs.delete(input_id)` removes a snapshot, refusing while leased to an adapter.
IDs are process-independent and never interpreted as filesystem paths.

## Trust and media

Generation serves a trusted client group, not individual Studio users. Studio
must check source ownership before create, persist its own owner/input mapping,
and authorize get/delete/workflow input use. IDs and hashes grant no user access.
Do not forward a caller's arbitrary Generation input ID through Studio. Hub must
register exact schemas after review. Uploaded originals share the same input
authority, quota, directory confinement and adapter leases; they are not output Assets.

The initial allowed types are PNG, JPEG, WebP and WAV. Check the actual file
signature against catalog MIME/kind; this is format identification, not a decoder
or malware scan. Providers must validate/decode media and enforce their own
pixel/duration limits before use. No other type is accepted by inference yet.

## Local image upload protocol

The trusted client first commits a private random canonical 32-hex UUID and its
owner/quota mapping, then calls `inputs.upload.begin(upload_id, mime_type,
size_bytes, sha256)`. Only PNG/JPEG/WebP, 1 byte–8 MiB and a lowercase SHA-256 are allowed.
The returned offset is the durable committed cursor. Send ordered chunks through
`inputs.upload.write(upload_id, offset, data_base64, chunk_sha256)` with at most
256 KiB decoded bytes. Exact repeated committed chunks succeed without appending;
conflicting content, out-of-order offsets and changed files fail closed.
`inputs.upload.finish(upload_id)` verifies total size/hash and fully decodes one
matching single-frame image, at most 4096 pixels per dimension / 16 Mi pixels,
before atomically publishing metadata. Repeating begin/finish never changes content
or extends expiry. Published metadata has `source_kind=upload`,
`source_asset_id=null`, and the ordinary input identity/digest/expiry fields.

Pending sessions expire ten minutes after begin, including across restart, and
reserve the full declared payload in the shared 128-record / 512 MiB input quota.
Finish starts the ordinary 24-hour input lifetime. Unpublished sessions cannot be
read/staged. `inputs.delete` explicitly aborts a pending upload or deletes a final
input, refusing an active validation/adapter lease. A crash after content append
but before the cursor record is committed rejects further use: abort or let it
expire rather than silently treating uncommitted bytes as valid. Errors from the
chunk handler do not echo private base64. Generation never selects user filenames.
Studio bounds its complete body/transfer and persists owner/storage charges before
any RPC; raw input IDs grant no access. No upload automatically submits a job.

Upload preparation is independent of GPU and Workflow readiness. Actual inference
still requires the exact qualified img2img definition, managed-input infrastructure
readiness and provider-side retention protections. Upload acceptance alone does
not authorize a production reference-image Workflow.

## Limits and lifecycle

Hard bounds: 64 MiB per input, 128 records, 512 MiB retained bytes, one create at a
time (busy immediately), 24 hours retention, 4 inputs / 128 MiB per staging lease.
Copy deadline 300 seconds; all reads reuse #27's <=256 KiB transfer primitive.
The source MIME/kind is checked before provider streaming; a private prepare limit
stops materialization at the smaller of the 64 MiB input bound and remaining input
storage. Oversized or unsupported sources leave no published output or input file.
Input/source hashes and byte counts must agree before publication. Metadata is
published last; incomplete directories are not visible. Cancellation removes
partials. Create prunes expired/unpublished records with a bounded directory scan;
expired get/use fails immediately. Delete is idempotent. Service owns input root.
Input storage defaults under output storage (`managed-inputs`); its independent
512 MiB budget is additional to the output budget and survives Docker restarts.

Source deletion after publication does not change a snapshot. Deletion during
copy aborts safely. Each snapshot remains immutable, with a separately recorded
source ID and digest for provenance. Expiry blocks **new** leases; an active
lease can finish within its deadline. Leases are process-local and disappear on
restart. All I/O is directory-fd confined, rejects symlinks/hardlinks and checks
file identity and digest. Staging does not follow caller paths or names.

## Provider adapter boundary and rollout

`ManagedInputs.stage(job_id, references, allowed_types)` is an internal adapter
context. It requires the existing JobStore reservation to belong to job_id,
validates exact declared parameter names and MIME allowlists, and exposes only
bounded readers plus immutable metadata. An adapter owns mapping these readers
to its runtime's input locations, cleanup and retention across ambiguous submits.
No second job/GPU authority is created. Runtime lifecycle remains external.

The ComfyUI adapter stages bounded decoded PNG/JPEG/WebP images, resolves only
LoadImage.image, and uses ordinary JobStore staging leases. With
`FLAMORIS_COMFYUI_INPUT_ROOT` configured, copies are written exclusively into
`flamoris-inputs/` under the exact shared ComfyUI input root; the adapter never
calls `/upload/image` on this path. Random service-selected filenames avoid
provider renaming, overwrites and HTTP-upload ambiguity. When unset, the existing
HTTP upload path remains for schema-1 development; its unmanaged provider copies
do not satisfy production image-v1 input readiness.

## Shared ComfyUI copies (Issue #62)

The provider's private artifact ledger reserves full payload bytes and a file
slot **before** writing, then commits the exact file identity after durable copy.
Defaults are 128 reservations / 512 MiB, configurable only downward. These charges
are independent of the managed-input snapshot budget. The one-MiB ledger and a
single atomic-write temporary are additional metadata space; an orphan temporary
stops admission until operator reconciliation, preventing repeated accumulation.
The root, namespace and stable-lock device/inode identities are also committed
under Generation's independent `OUTPUT_DIR/comfyui-input-authority/root.json`.
This state must remain outside the provider input root, including resolved path
aliases. Replacing an input mount/namespace/lock cannot silently reset the storage
budget after restart. An existing namespace's missing lock is never recreated.
Incomplete or missing protected copies report infrastructure readiness false.
The ledger carries no job state, does not choose execution/cancellation, and never
replays a submission: JobStore remains the execution authority.

After a successful generation acknowledgement, the adapter durably binds copies
to that execution ID. Queued/running/unknown/cancel-requested copies remain charged
and protected across restart. Only valid terminal ComfyUI history for the bound
execution, or a definite pre-admission rejection, grants release. Expiring or
deleting a managed snapshot has no effect on these provider copies. Losing an
acknowledgement or crashing between acceptance and binding keeps the copies
protected even if unrelated queue/history entries appear terminal. Recovering an
owned execution uses the same release path without restoring output authority.

Released copies are deleted with bounded directory-fd-confined work. Root and
namespace paths reject symlinks; files/ledger/lock must be single-link regular
files. Before POST, copied bytes are rehashed and identities rechecked. Cleanup
journals a random quarantine name before rename, verifies identity again there,
and unlinks only the matching candidate. Crashes during cleanup retain a released
receipt and retry safely on a later terminal observation or new staging operation.
Changed, hardlinked, symlinked, unrecorded or incomplete unidentified files are
retained and block admission rather than silently deleted. Cleanup faults do not
change a known provider execution outcome or free an unconfirmed storage charge.

The namespace and stable `.lock` inode are owned by Generation's effective UID;
group/world write access on the namespace is rejected. A provisioned shared group
may read images through directory `2750` / file `0640` modes (subject to umask);
the ledger/lock stay `0600`. Verify the actual native provider UID can read the
copies before rollout. Other writers must not modify these entries. Do not delete
the ledger/lock, change mounted filesystem identity or manually remove protected
copies while a submission might still be active. A
storage-identity mismatch requires restoring the actual old mount/namespace/lock
or an explicit operator migration after reconciling all charges; do not erase the
Generation-side authority record to reset storage accounting. Provision the parent
input root rather than precreating an empty namespace without its stable lock.
A charged receipt without a committed file identity, a lost execution binding,
or changed/quarantined entries requires operator reconciliation with JobStore and
provider execution evidence. Restart, queue absence and old mtime are insufficient
evidence. Readiness must remain disabled during unresolved reconciliation.

Production rollout requires a shared-path/UID/mount receipt, legacy HTTP-copy
inventory, actual reference copy/terminal release/storage-bound verification,
independent trusted runtime evidence and exact automatic Workflow attestation;
see [verification](WORKFLOW_VERIFICATION.md). Passing offline lifecycle tests is
not an installed-runtime receipt. Other provider bindings remain unsupported.
