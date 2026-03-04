## Summary
- 

## Why
- 

## Scope
- [ ] Single RFC/slice scope
- [ ] No unrelated refactors mixed in

## Validation (local)
- [ ] `make lint`
- [ ] `make typecheck`
- [ ] `make test-unit`
- [ ] Additional targeted checks for changed area

## CI Expectations
- [ ] Fast PR gates are green
- [ ] Heavy gates run in scheduled/manual tier where applicable

## Governance/Docs
- [ ] RFC/docs updated where behavior or standards changed
- [ ] API/OpenAPI/vocabulary updates included if contract changed

## Post-Merge Hygiene
- [ ] Delete remote feature branch
- [ ] Delete local feature branch
- [ ] Sync local main with origin/main (`local = remote = main`)