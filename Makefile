.PHONY: install lint typecheck monetary-float-guard domain-product-validate idea-evidence-intake-contract-gate idea-evidence-materialization-contract-gate openapi-gate migration-smoke migration-apply test test-unit test-integration test-e2e test-coverage security-audit check ci ci-local docker-build clean

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"
	python -m pip install pre-commit
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

test-coverage:
	COVERAGE_FILE=.coverage.unit python -m pytest tests/unit --cov=src/app --cov-report=
	COVERAGE_FILE=.coverage.integration python -m pytest tests/integration --cov=src/app --cov-report=
	COVERAGE_FILE=.coverage.e2e python -m pytest tests/e2e --cov=src/app --cov-report=
	python -m coverage combine .coverage.unit .coverage.integration .coverage.e2e
	python -m coverage report --fail-under=99

security-audit:
	python scripts/run_security_audit.py

check: lint typecheck openapi-gate test

ci: lint typecheck openapi-gate migration-smoke test-integration test-e2e test-coverage security-audit

ci-local: ci

docker-build:
	docker build -t lotus-report:ci-test .

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.ruff_cache', '.mypy_cache']]; [pathlib.Path(p).unlink(missing_ok=True) for p in ['.coverage', '.coverage.unit', '.coverage.integration', '.coverage.e2e']]"
