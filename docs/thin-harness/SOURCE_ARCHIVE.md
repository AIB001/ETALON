# Source response archive / 1

New `run_data` acquisitions retain source responses in the existing data snapshot,
without a second database. `source-archive/index.json` and content addressed
`source-archive/bodies/<sha256>.body` files enter the existing snapshot file inventory
and SHA-256 seal. Evidence files have no TTL or cache eviction path. The caller must
retain an admitted snapshot; the exploratory MolQuarry cache is a separate facility.

The index records sanitized request descriptors, per-attempt HTTP status and allowed
headers, retrieval time, declared source version when available, partial streams,
HTTP errors, retries, and cache observations. Authorization/cookie headers, URL
userinfo, credential query values, and private request/reply bodies are excluded.
Public scientific response bodies retain their original values and bytes.
The established public pagination fields `page_token` and `pageToken` also retain
their exact values, including in an initial non-first-page query. Authentication
fields such as `access_token`, `id_token`, `refresh_token`, and generic `token`
remain redacted; this exception does not allow arbitrary token-suffixed fields.

Each body declares its representation and digest scope. Normal transport streams
contain HTTP content-encoded body bytes, excluding HTTP framing and TLS. Buffered
httpx responses may expose only content-decoded bytes; this is explicit and never
presented as the unavailable encoded original. MolQuarry's existing
`response_sha256` remains the content-decoded parser-input digest, with a separate
link to the archived attempt and body digest. These hashes are not interchangeable.
Truncated/aborted captures identify their observed prefix and remain incomplete.

Normalized page files link to capture attempts through the index and unchanged
MolQuarry provenance hashes. Other workflow outputs are inventoried in the index;
their existing provenance carries the response links. A parsed JSON object is
never reserialized and called an original response.

New evidence runs continue to disable HTTP caching. If the boundary observes a
cache hit, it records either an already captured, matching original in this same
snapshot, or `original_unavailable`; it does not manufacture missing bytes. Old
snapshots remain valid under their original seal. `source_archive_status()` reports
them as `legacy_normalized_only`, without changing their manifest or identity.

Local acceptance uses httpx MockTransport, not scientific computation. Live
acceptance must use a new run id for a bounded public query, verify it with
`read_snapshot`, inspect the body and encoding metadata, compare the existing
decoded provenance digest with the decoded archived body, and confirm a copied
body's corruption is rejected. This new capture does not repair historical
acquisitions whose original responses are missing.
