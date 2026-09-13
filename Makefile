.PHONY: install lint typecheck monetary-float-guard domain-product-validate idea-evidence-intake-contract-gate idea-evidence-materialization-contract-gate openapi-gate migration-smoke migration-upgrade-smoke migration-apply complexity-gate source-size-gate dead-code-gate dependency-hygiene-gate dependency-constraints-gate constraints-refresh code-health-gates test test-unit test-integration test-e2e test-suite-coverage coverage-gate test-coverage security-audit check ci ci-local docker-build clean

TEST_SUITE ?= unit
TEST_PATH ?= tests/$(TEST_SUITE)
COVERAGE_INPUTS ?= .coverage.unit .coverage.integration .coverage.e2e
COVERAGE_FAIL_UNDER ?= 97

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]" -c constraints.txt
	python -m pip install pre-commit -c constraints.txt
	pre-commit install

lint:
	python -m ruff check .
	python -m ruff format --check .
	$(MAKE) monetary-float-guard
	$(MAKE) idea-evidence-intake-contract-gate
	$(MAKE) idea-evidence-materialization-contract-gate

monetary-float-guard:
	python scripts/check_monetary_float_usage.py

domain-product-validate:
	python scripts/validate_domain_data_product_contracts.py

idea-evidence-intake-contract-gate:
	python scripts/validate_idea_evidence_intake_contract.py

idea-evidence-materialization-contract-gate:
	python scripts/validate_idea_evidence_materialization_contract.py

typecheck:
	python -m mypy --config-file mypy.ini

openapi-gate:
	python scripts/openapi_quality_gate.py

migration-smoke:
	python scripts/migration_contract_check.py --mode ledger-schema
	$(MAKE) migration-upgrade-smoke

migration-upgrade-smoke:
	python scripts/report_schema_upgrade_check.py

migration-apply:
	python scripts/migration_contract_check.py --mode ledger-schema

test:
	$(MAKE) test-unit

test-unit:
	python -m pytest tests/unit

test-integration:
	python -m pytest tests/integration

test-e2e:
	python -m pytest tests/e2e

test-suite-coverage:
	COVERAGE_FILE=.coverage.$(TEST_SUITE) python -m pytest $(TEST_PATH) --cov=src/app --cov-report=

coverage-gate:
	python -m coverage combine $(COVERAGE_INPUTS)
	python -m coverage report --fail-under=$(COVERAGE_FAIL_UNDER)

test-coverage:
	$(MAKE) test-suite-coverage TEST_SUITE=unit TEST_PATH=tests/unit
	$(MAKE) test-suite-coverage TEST_SUITE=integration TEST_PATH=tests/integration
	$(MAKE) test-suite-coverage TEST_SUITE=e2e TEST_PATH=tests/e2e
	$(MAKE) coverage-gate

security-audit:
	# The closure is Linux-resolved. Audit it in the same Linux posture from
	# every host rather than letting a workstation resolve a different graph.
	# Build only audit inputs in a temporary workspace: setuptools writes package
	# metadata, while the committed source mount remains immutable. Do not copy
	# checkout metadata or local artifacts into the proof environment. Install the exact constrained
	# build backend before disabling build isolation, so the audit never executes
	# a freshly resolved backend. Installing the project with its declared dev
	# extra before scanning makes the audit refuse a direct or extra-derived
	# package that is missing from the recorded closure; pip-audit never receives
	# an incomplete pin set.
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/src:ro" python:3.12-slim bash -c "mkdir -p /tmp/report-audit/docs/standards && cp /src/pyproject.toml /src/constraints.txt /tmp/report-audit && cp -r /src/src /src/scripts /tmp/report-audit && cp /src/docs/standards/dependency-vulnerability-exceptions.json /tmp/report-audit/docs/standards && cd /tmp/report-audit && python -m pip install --quiet setuptools -c constraints.txt && python -m pip install --quiet --no-build-isolation '.[dev]' -c constraints.txt && python scripts/run_security_audit.py --repo-root /tmp/report-audit"

# Equality-banked code-health thresholds: each equals today's measurement exactly, so
# any regression fails and any improvement is banked by lowering the bound in the
# same commit (reporting_read_service.py at 4508 lines and CC 28 in
# package_builder's outcome-review builder are the current ceilings, not
# aspirations).
SOURCE_FILE_MAX_LINES ?= 4249
MAX_CYCLOMATIC_COMPLEXITY ?= 28
MAX_HIGH_COMPLEXITY_FUNCTIONS ?= 8

complexity-gate:
	python scripts/python_complexity_inventory.py --limit 20 --max-cc $(MAX_CYCLOMATIC_COMPLEXITY) --max-high-complexity $(MAX_HIGH_COMPLEXITY_FUNCTIONS)

source-size-gate:
	python scripts/source_size_gate.py --max-lines=$(SOURCE_FILE_MAX_LINES)

dead-code-gate:
	python scripts/dead_code_gate.py

dependency-hygiene-gate:
	python -m deptry .

# The committed constraints.txt is the reproducibility statement: the exact
# closure CI builds and type-checks against (report#345). Enforced on Linux
# (the lane platform); elsewhere the gate states not-evaluable, because pip's
# environment markers make one exact closure platform-specific.
dependency-constraints-gate:
	python scripts/check_dependency_constraints.py

# Refresh runs in the lane image so the recorded closure stays Linux-resolved;
# rerun `make security-audit` against the refreshed closure in the SAME slice.
constraints-refresh:
	docker run --rm -v "$(CURDIR):/src:ro" -w /tmp python:3.12-slim bash -c "mkdir /tmp/build-src && cp /src/pyproject.toml /tmp/build-src && cp -r /src/src /tmp/build-src && cd /tmp/build-src && pip install --quiet --upgrade pip setuptools && pip install --quiet --no-build-isolation -e '.[dev]' && pip install --quiet pre-commit && { pip freeze --exclude-editable; python -c 'from importlib.metadata import version; print(\"setuptools==\" + version(\"setuptools\"))'; } | sort -fu" > constraints.txt
	@echo "Closure refreshed from the lane image; now run 'make security-audit' in the same slice."

code-health-gates: complexity-gate source-size-gate dead-code-gate dependency-hygiene-gate dependency-constraints-gate

check: lint typecheck code-health-gates openapi-gate monetary-float-guard domain-product-validate idea-evidence-intake-contract-gate idea-evidence-materialization-contract-gate test

# Direct `make ci` documents a caller-owned isolated database (README, repository
# context); mark it so the integration-test session trusts the given URL instead of
# provisioning a nested database or demanding CREATEDB (issue #179).
#
# Each suite runs exactly once, through test-coverage. Listing test-integration and
# test-e2e here as well ran them a second time against the same database, so the
# later session inherited the earlier one's committed rows and failed on capacity it
# had itself consumed (issue #335). test-unit was already reached only through
# test-coverage, and the merge gate runs one test-suite-coverage job per suite
# against its own database. test-integration and test-e2e remain as targets for
# running a suite directly.
ci: export REPORT_JOB_LEDGER_DATABASE_IS_ISOLATED = true
ci: lint typecheck code-health-gates openapi-gate monetary-float-guard domain-product-validate idea-evidence-intake-contract-gate idea-evidence-materialization-contract-gate migration-smoke test-coverage security-audit

ci-local:
	python scripts/run_isolated_ci.py

docker-build:
	docker build -t lotus-report:ci-test .

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.ruff_cache', '.mypy_cache']]; [pathlib.Path(p).unlink(missing_ok=True) for p in ['.coverage', '.coverage.unit', '.coverage.integration', '.coverage.e2e']]"
