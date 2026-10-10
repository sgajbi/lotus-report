# Composite authority changes while rendering

An outstanding composite render belongs to its captured snapshot. Changing the
current supplier's membership, method or source cut after capture cannot replace
that package or its financial facts. A new order with the original selection
refuses the changed response; an explicitly selected new version gets a distinct
capture. Report does not recalculate either version.

## Registered control

`tests/unit/composite_reporting/test_authority_change_during_render.py` exercises
the registered order, worker, SQLite snapshot and shipped `RenderClient`. An event
holds render submission after the job reaches `rendering`. The test mutates the
current source object **in place**, reads the original snapshot while the render
is outstanding, then releases it. Both the actual transport argument and retained
record must still equal the original capture. There is one source call before
release; only the subsequent old/new selection orders perform new source reads.

Six cases cover membership, method and source-cut changes for the normal and
large-money fixtures. The stale order's source returns HTTP 200 but fails pinned
admission; no financial revision or source response is retained as successful data.
The new selection completes a separate JSON capture. Transport is controlled and
the unit receiver returns 503; these tests do not claim workbook or Archive completion.

Run from the `lotus-report` checkout, in its existing development environment:

```powershell
python -m pytest tests/unit/composite_reporting/test_authority_change_during_render.py -q
```

```bash
python -m pytest tests/unit/composite_reporting/test_authority_change_during_render.py -q
```

## Case-to-artifact evidence

The bounded native proof passes the released packages to existing Render
`e8035036ef6ab0a590cba88da7d0b73f5ad323b2`'s `CompositeWorkbookRenderService`.
The existing external raw OOXML reader independently checks every cell, policy,
source pointer, retained dataset and identity. It imports no Report or Render
implementation to derive expected cells. Substitution of the changed current
source is refused. Each artifact has 154 canonical cells, 62 policies and 17 sheets.
Native producer `cd66fb/0` and targeted tests `24c548/0` succeeded; this proof is
registered Report/SQLite plus actual native writer, with controlled source and
render transport. It is separate from the accepted [R27 actual-source lifecycle](https://github.com/sgajbi/lotus-report/issues/417#issuecomment-6101603776),
not a new TCP/Archive campaign.

Retained bundle: `report417-render-authority-boundary-20261011-r2`; exact package,
registered-boundary record, diagnostic, workbook and independent-reader receipt
are retained for each case. SHA256 of original XLSX:
`a663a0f31f7a336193fb815414dc00e76abf9571172b77d5e683c5d3eb1cee6f`;
large-money/zero XLSX:
`e4223dd92836b5198a08a788528e14d8ba17722e93c7de04d5d52c46cb94f35a`.

| Requested scenario | Exact expected source / produced artifact | Successful independent oracle |
| --- | --- | --- |
| Authority changes after capture during rendering | Both packages preserve the original window membership, method, source-cut and response identity; current-source substitution refuses | Registered held event and snapshot equality; native reader compares all pinned data and identities; six old/new order controls |
| Large money / precision | Large-money case: `MonthlyReturns_1!G2` = `400000000000000000000000000.00 USD`; `Contribution_1!F2` = `100000000000000000000000000.00 USD`; canonical evidence preserves exact text | Large-money reader SHA256 `b03e55fe41c122a81bdd53eee8c8d904efd12a525536d8f598a7a7ce590e34ce`; no Excel numeric storage |
| Negative / zero / unavailable | `Contribution_1!E3` = `-2.00%`, `H3` = `-1.50 pp`; second-month source dispersion `0E-12` is AVAILABLE, first-month null dispersion remains unavailable; `Risk_1!C2` = `UNAVAILABLE: SOURCE_PRODUCT_NOT_CAPTURED` | Same full reader; measured zero and unknown/uncaptured values retain different canonical values and reasons |
| Identifiers / formula injection | `Contribution_1!B2` = `00000000000000000001`, `B3` = `=literal-identifier`, both literal text | Independent raw reader rejects formula cells/external relationships; existing retained partition-reader controls also refuse injected formulas (`d517da/0`) |
| Worked periods / fee / currency / method / legend | Monthly source +1%/+2%, displayed cumulative3.02%; NET_ACTUAL/USD; original method/pins remain in captured package | Both full readers; existing retained wrong-fee/wrong-legend controls in `report417-partition-reader-controls/command-receipt.json` |
| Long history / actual partitions | Existing R5 source-products workbook:72 months, Contribution partitions3, CellEvidence partitions25; original/corrected/re-render separately retained | `report417-r5-delivery-ledger.json`, reader `f03899/0`; new small workbooks do not replace or broaden this proof |

The large fixture scales supplied money literals equally while preserving supplied
weights and ratios; it introduces no investment-return calculation. Its second
month has equal member returns and a literal measured dispersion zero. Unknown
first-month dispersion and unavailable risk remain null with reasons. These are
controlled examples, not actual producer financial authority.

## Remaining original acceptance

Supplied since-inception composite evidence is still a Performance dependency;
the delivered portfolio SI product does not itself supply a composite selector.
An explicit closed-member history-to-workbook oracle also remains unlocated.
The retained Manage correction graph has intervals ending on its selected month's
end; the local three-month unit helper has an earlier ended interval. Neither is
an actual producer case proving a formerly included member remains visible after
closure and absence from a later month's observations. The required Manage
artifact is a pinned later-month source/read set with that member's earlier
included interval, its effective end/exclusion, expected cohort and observation
absence; Report can retain it through the existing history product. No invented
`CLOSED` status or replacement history engine is required.

RPT-01 is the first family; partial RPT-04/RPT-06 work does not deliver RPT-02–12.
This boundary proof adds no GIPS, PDF, enterprise-capacity, recipient or
institutional acceptance condition and does not close #417.
