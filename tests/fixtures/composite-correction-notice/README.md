# Retained correction-notice test captures

These immutable captures exercise Report's native v9 admission and projection against
the original and corrected v8 generations from the independent 8d chain. Each JSON
envelope stores the original filename, the SHA-256 of the original bytes, and those
bytes compressed with gzip and encoded as base64. The test loader verifies the hash
before parsing. Financial strings, nulls, reason codes, array order, and source
policies remain unchanged.

The nine snapshot, document, and acknowledgement captures total 785,313 original
bytes. Four additional request and job-status captures support test-only reconstruction
of the original semantic request. They do not establish runtime ledger authority.
The helper in `tests/unit/composite_notice_fixture.py` remains outside application
code. No capture is a database seed or permission to recreate historical records.

The path is O → O_rr → C → C_rr. Technical edges retain the exact Report lineage
events. The original Archive correction POST response remains an immutable fixture.
The economic edge in the r6 candidate uses a controlled eight-field relationship
record projected from that fixture, labelled as test evidence. It is not an
Archive197 API response and does not prove source API authorization. Each
generation's existing native v8 revision and custody derivation is tested separately.
The generated notice has a synthetic job, request, snapshot, and render identity.
It remains `PARTIAL` and `NOT_ATTESTED` with eight independent obligations.

## Generate a local review candidate

Working directory: the `lotus-report` checkout. Install the repository's constrained
development dependencies first. Choose an absolute output directory outside source.

PowerShell:

```powershell
python scripts/generate_correction_notice_evidence.py --output "$env:TEMP/lotus-report-notice-evidence"
python -m pytest tests/unit/test_composite_correction_notice.py -q
```

Bash:

```bash
python scripts/generate_correction_notice_evidence.py --output "${TMPDIR:-/tmp}/lotus-report-notice-evidence"
python -m pytest tests/unit/test_composite_correction_notice.py -q
```

The generator runs the native model, projection, revision, custody, full-package builder,
and existing producer preflight. It emits executable schema candidates, the full
synthetic package/capture, canonical hash preimages, expected digests, workbook projection,
and a byte-hash manifest. It never calls a source service or writes a runtime database.
Its budget proof explicitly distinguishes producer measurements from unexecuted writer
and live delivery checks. Registered Report capture, SQLite reopen, and source-off
replay are exercised with controlled source and renderer ports; they do not establish
Archive197 API delivery or receiver workbook behavior. Receiver review and runtime
qualification remain separate.

The candidate is not a schema freeze, live restore, registered replay, or release claim.
