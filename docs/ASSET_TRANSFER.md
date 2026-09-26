# Bounded asset delivery (Issue #27, Phase 2)

This contract adds MCP tools, not an HTTP file route. Generation serves one
trusted client group. Studio must authorize the source job on **every** prepare,
read and delete request; an asset ID or digest is not an ownership credential.
Hub must explicitly register the new schemas and retain caller authentication.

## Protocol

1. `assets.prepare(asset_id)` materializes exactly one selected output and returns
   metadata plus `sha256`, `size_bytes`, `chunk_bytes` and `transfer_version: 1`.
2. `assets.read(asset_id, sha256, offset, length)` returns bounded base64 bytes,
   `chunk_sha256`, `next_offset` and `eof`. Length is 1..256 KiB; offset is an
   integer within the object. The client verifies each chunk and the final digest.
3. Retry the same offset after transport failure. No server-side cursor or session
   is created. Iterate `assets.list` to retrieve multiple outputs independently.

The existing native-image `assets.get` and its 64 MiB limit remain unchanged.
Non-image media use the chunk contract. Adding media extensions here does not
register new production capabilities or interpret new ComfyUI node outputs.

## Ownership, bounds and failure

JobStore owns asset identity, tombstones and the per-job lock. The transfer helper
uses that same lock for prepare/read; deletion waits for an active operation and
then invalidates subsequent reads. No open file survives between requests.
The provider adapter supplies at most 256 KiB per yield. No whole-file fallback.
One prepare is admitted per process (busy fails immediately), with a 300 second
overall deadline including lock waiting and a 30 second provider-read deadline.
Default per-asset maximum is 1 GiB, managed output budget 8 GiB. Configuration can
lower these or raise them to 4 GiB / 64 GiB. A bounded directory-fd scan includes
existing output files and partial files; exceeding the scan bound fails closed.
Legacy retrieval keeps its existing budget semantics; operators should use the
new route for large outputs and retain ordinary output retention policy.

Stream into an exclusive no-follow temporary file under the pinned job directory;
count actual bytes, hash incrementally, fsync, and atomically rename. Failure or
cancellation removes the partial file. Crash leftovers remain charged to the disk
budget and are removed on the next prepare for that job (at most 64 entries).
This is a single-process/single-instance contract, not a distributed disk quota.
Only the service may write its managed output root.

An integrity receipt records the digest and file identity (device, inode, size,
mtime and ctime). Read checks identity before/after bounded I/O and returns a
chunk digest. Replacement or mutation fails closed. A fresh prepare can re-hash
an existing file; a caller's old digest cannot silently select new content.
No provider locator is persisted or exposed by this protocol. After restart,
materialized files can be prepared/read; unmaterialized metadata remains visible
but cannot be fetched without the lost provider mapping. Restart resumes by
preparing again and retrying offsets with the same digest, never re-submitting a
job. There is no expiring transfer token and no cancellation resource to leak.

Hub/Studio adoption is separately reviewed; no public route, topology, upload,
user ACL database, runtime activation or new generation authority is introduced.
