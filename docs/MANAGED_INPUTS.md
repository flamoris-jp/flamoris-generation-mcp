# Managed inputs (Issues #30 and #64)

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
size_bytes, sha256)`. Only PNG/JPEG/WebP, 1–8 MiB and a lowercase SHA-256 are allowed.
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

The ComfyUI adapter uploads bounded decoded PNG/JPEG/WebP images, resolves only
LoadImage.image, and uses ordinary JobStore staging leases. Production img2img
requires both independent infrastructure readiness and exact automatic Workflow
attestation; see [verification](WORKFLOW_VERIFICATION.md). Provider upload retention
is an operational prerequisite, not automatic deletion claimed by this adapter.
Other provider bindings remain unsupported.
