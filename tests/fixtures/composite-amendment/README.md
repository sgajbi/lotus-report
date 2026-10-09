# Two-month controlled source fixture

`two-month-controlled-source.json` and `two-month-request.json` are retained from
the Report #417 two-month ordinal inspection on 2026-10-10. Manage domain helpers
at `545269b3d356630680da7c692d90613a5c28f99c` generated an ordinary September root
and source correction, followed by an ordinary October root and source correction.
No dependent-month cascade is represented. These are controlled in-memory source
products, not native producer HTTP or database exports.

The registered Report API/worker inspection at
`d4e237028c0320cc0afa2991211152d6b1dba4d5` produced 34 Amendment rows per month,
starting `m0:a0` and `m1:a34`. Delivery tests rebuild the dataset from these source
products and derive response digests with the production function. They also
exercise actual worker package emission with a deliberately declining Render
boundary. No receiver delivery or financial calculation is implied.
