# Trusted external client provenance

Generation records optional non-secret `{issuer, subject}` metadata. MCP Hub
authenticates an external credential and supplies this identity; Studio binds an
exact identity to an authenticated Studio account. Generation does not choose an
owner. Job, asset, workflow and managed input IDs remain identifiers, not access
grants. Existing trusted-group transport access must remain deployment-controlled.

## Internal signing configuration

Configure `FLAMORIS_PROVENANCE_SECRET` and `FLAMORIS_PROVENANCE_ISSUER` together.
The secret is an independent internal key, never an external client credential,
and must contain 32–512 bearer-credential characters. Use a randomly generated
high-entropy value. The expected issuer and opaque non-secret subject must match
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`. Keep these values separate from Studio user IDs.
Secret configuration is excluded from settings dumps and representations.

Hub inserts `_meta["flamoris.dev/external-provenance"]` into each upstream
`tools/call` request. Its exact fields are:

| Field | Contract |
| --- | --- |
| `version` | Integer `1` |
| `issuer` | Exact configured issuer |
| `subject` | Credential-bound opaque stable identity |
| `issued_at` | Integer UTC Unix seconds, within 30 seconds of receipt |
| `nonce` | Fresh 32-character lowercase hexadecimal UUID |
| `signature` | 64-character lowercase hexadecimal HMAC-SHA256 |

The HMAC key is the UTF-8 encoded internal secret. Signed bytes are the UTF-8
encoding of Python-equivalent
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`,
where `value` contains `version`, `issuer`, `subject`, `issued_at`, `nonce`,
`tool` (the original upstream tool name) and `arguments` (the original argument
object, before validation or coercion). The signature field is excluded. The
serialized signed object is bounded to 256 KiB. Neither caller-selected ownership
fields nor MCP session IDs participate as authority.

## Admission and replay protection

Middleware verifies the exact issuer, envelope, signature, raw tool and raw
arguments before any tool effects. Missing context preserves legacy anonymous
behavior. Claimed context that is unconfigured, partial, malformed, stale,
replayed or unverifiable fails with a bounded error; it cannot become anonymous.
Each accepted nonce is committed and fsynced in the existing private durable-record
store before dispatch. The journal holds at most 4096 live nonces, each for 61
seconds from acceptance, and retains the last wall-clock observation. It survives
process restart and refuses corrupt/unreadable records, clock rollback, uncertain
publication or capacity exhaustion. Expired valid entries can be removed only
after a valid clock observation. Live entries are never evicted to accept traffic.
An in-process authority fence requires reconciliation/restart after journal or
clock failure; anonymous calls do not consult this authority. No automatic replay
or anonymous fallback is supported.

The verified immutable provenance is scoped to the current server request task
and reset after dispatch. Cached HTTP sessions cannot bind a principal; sequential
and concurrent principals on the same session remain separate. Only admission
captures it: polling by another principal cannot overwrite a job's provenance.
The public tool argument schemas have no provenance or owner parameter.

## Durable job and asset metadata

Admission captures `external_provenance: {issuer, subject}` before provider awaits
and persists it in the active reservation journal. Recovery preserves the same
binding without claiming new provider/replay guarantees. Completed manifests,
`jobs.status/result`, `assets.list` and `assets.prepare` preserve it. Legacy absence
remains anonymous; malformed persisted provenance fails closed. Signing keys,
signatures, nonces and bearer credentials are not copied into job/asset manifests.

Archived `jobs.status` returns only a bounded completed catalog summary. It
validates completed identity, declared output roles, safe managed filenames and
deletion markers without reading media bytes or contacting providers. The summary
includes `archived: true`, `output_count` and optional provenance; it does not
restore job execution or expose manifest file paths.

Studio can import a completed external job by verifying the exact configured
issuer/subject mapping against its authenticated account, then checking that the
job and every selected asset have identical provenance and job identity. Studio
must authorize assets, inputs and downloads itself. Provenance metadata is not a
Generation access-control policy or a general ownership token.
