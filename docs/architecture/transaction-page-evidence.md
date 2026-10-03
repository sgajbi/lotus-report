# Transaction page evidence

Report consumes `lotus-core` `TransactionLedgerWindow:v1` as a source-owned ledger.
The consumer trust fields are defined in
`contracts/domain-data-products/lotus-report-consumers.v1.json`. Report does not
reconcile the portfolio or replace Core's quality policy with row-count completeness.
Core may mark paginated responses `PARTIAL` even when Report eventually fetches all
rows; that producer qualification remains partial. The all-healthy control verifies
aggregation only when every source page actually states complete evidence.

`transaction-page-evidence.v1` qualifies every consumed page. All pages must carry
the required trust fields. A missing or malformed field on one page remains an
explicit limitation even when another page supplies it. Quality is complete only
when every page states `COMPLETE`; reconciliation is complete only when every page
states `RECONCILED` or `COMPLETE`. Missing or unknown status remains unknown.
Multiple distinct adverse reconciliation states also yield unknown rather than
choosing whichever page arrived last.

In addition to the consumer trust fields, every page must carry a nonblank string
`portfolio_id`, as required by Core's `PaginatedTransactionResponse`. Optional
`reporting_currency` may be omitted or null for the raw ledger. A raw scope and a
currency-restated scope cannot be
combined as one coherent window, including when the first page's currency is null.

The stable window fields are product/version, tenant, portfolio, reporting currency,
as-of date, latest evidence timestamp, restatement version, source batch fingerprint,
snapshot identity and policy version. Conflicting supplied values produce
`transaction_window_source_identity_incoherent`. Core's reconstruction evidence is
bound to the complete unpaginated ledger scope. Generation time, operational
correlation and per-page content hashes may differ and do not establish revision
conflicts. Aggregate identity retains the first supplied value; an incoherent
aggregate never becomes ready.

`sourceProduct.page_evidence` records consumed-page ordinals, missing field names,
conflicting field names, quality/reconciliation states and source reason codes.
Conflict notes expose field names and page ordinals without copying conflicting raw
values. Page count remains bounded by the existing read budget. Reason codes are
sorted and deduplicated, limited to 64 per page and aggregate, and restricted to
128-character machine identifiers using letters, digits, underscore, dot, colon
and hyphen. Discarded malformed or excessive reason evidence produces an explicit
partial qualification. Row deduplication and truncation budgets retain their
existing behavior.

The transaction and income/activity sections share the same qualified read.
Durable capture persists that evidence; reopening and retained replay/rerender
reuse it without source recollection. The current Render package forwards the
qualification's code, severity and message in earnings-statement notes when
income/activity was requested. Full page evidence remains in the immutable input
snapshot, rather than being added to the Render contract.

Owning tests are `tests/unit/services/test_transaction_page_evidence.py` and
`tests/unit/services/test_transaction_page_retention.py`. The latter exercises the
native clients and capture recorder with an in-process HTTP transport and SQLite
stores. It proves source behavior and retained projection, not external Core wire,
default PostgreSQL runtime, financial engine execution or Archive custody.
