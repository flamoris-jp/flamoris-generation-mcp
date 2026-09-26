# Managed generated-asset inputs (Issue #30)

This first contract copies an existing generated asset into an immutable managed
input. Client uploads, URLs, host paths and provider filenames are not accepted.
`inputs.create(asset_id)` returns an opaque input ID, source asset ID, SHA-256,
media type, size, creation time and expiry. `inputs.get(input_id)` reads metadata;
`inputs.delete(input_id)` removes a snapshot, refusing while leased to an adapter.
IDs are process-independent and never interpreted as filesystem paths.

## Trust and media

Generation serves a trusted client group, not individual Studio users. Studio
must check source ownership before create, persist its own owner/input mapping,
and authorize get/delete/workflow input use. IDs and hashes grant no user access.
Do not forward a caller's arbitrary Generation input ID through Studio. Hub must
register exact schemas after review. Both integrations are separate issues.

The initial allowed types are PNG, JPEG, WebP and WAV. Check the actual file
signature against catalog MIME/kind; this is format identification, not a decoder
or malware scan. Providers must validate/decode media and enforce their own
pixel/duration limits before use. No other type is accepted by inference yet.

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

This PR does not claim a verified upload/staging API for ComfyUI, Irodori,
SheetSage2, AnimeGen or SeeThrough. Production workflow file bindings therefore
remain fail-closed. No production provider input capability is advertised.
The internal stage contract is exercised with fake adapters and small real media
fixtures. Provider-specific staging/submit integration must keep the lease for
all runtime reads, preserve returned provenance in job metadata, and pass the
separate real-runtime smoke before a trusted manifest can enable file bindings.
