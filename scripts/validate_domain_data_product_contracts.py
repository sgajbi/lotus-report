from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DECLARATION_DIR = ROOT / "contracts" / "domain-data-products"
# Developer machines keep lotus-platform as a sibling checkout; CI cannot, so the
# gate step checks the platform contracts out inside the workspace and points
# LOTUS_PLATFORM_ROOT at them. The gate fails loudly either way when the
# validator is absent - it never degrades to a skip.
PLATFORM_ROOT = Path(os.environ.get("LOTUS_PLATFORM_ROOT", ROOT.parent / "lotus-platform"))
PLATFORM_DECLARATION_DIR = PLATFORM_ROOT / "platform-contracts" / "domain-data-products"
PLATFORM_VOCABULARY_DIR = PLATFORM_ROOT / "platform-contracts" / "domain-vocabulary"
PLATFORM_VALIDATOR_PATH = PLATFORM_DECLARATION_DIR / "validate_domain_data_product_contracts.py"

PRODUCT_PATTERN = "*-products.v1.json"
CONSUMER_PATTERN = "*-consumers.v1.json"
VOCABULARY_FILENAMES = (
    "domain-data-product-semantics.v1.json",
    "domain-data-product-trust-metadata.v1.json",
)


def _load_platform_validator():
    if not PLATFORM_VALIDATOR_PATH.exists():
        raise FileNotFoundError(
            f"Platform validator not found at {PLATFORM_VALIDATOR_PATH}. "
            "Ensure the sibling lotus-platform repository is available."
        )

    spec = importlib.util.spec_from_file_location(
        "lotus_platform_domain_product_validator", PLATFORM_VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load platform validator from {PLATFORM_VALIDATOR_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_local_declaration_paths(source_directory: Path) -> list[Path]:
    return sorted(source_directory.glob(PRODUCT_PATTERN)) + sorted(
        source_directory.glob(CONSUMER_PATTERN)
    )


def _collect_required_upstream_product_paths(source_directory: Path) -> list[Path]:
    required_repositories: set[str] = set()
    local_producer_repositories: set[str] = set()

    for path in sorted(source_directory.glob(PRODUCT_PATTERN)):
        payload = _load_json(path)
        producer_repository = payload.get("producer_repository")
        if isinstance(producer_repository, str) and producer_repository:
            local_producer_repositories.add(producer_repository)

    for path in sorted(source_directory.glob(CONSUMER_PATTERN)):
        payload = _load_json(path)
        for dependency in payload.get("dependencies", []):
            if not isinstance(dependency, dict):
                continue
            producer_repository = dependency.get("producer_repository")
            if isinstance(producer_repository, str) and producer_repository:
                required_repositories.add(producer_repository)

    upstream_paths: list[Path] = []
    for producer_repository in sorted(required_repositories - local_producer_repositories):
        candidate = PLATFORM_DECLARATION_DIR / f"{producer_repository}-products.v1.json"
        if not candidate.exists():
            raise FileNotFoundError(
                f"Required upstream producer declaration not found at {candidate}. "
                "Machine-readable consumer coverage cannot validate until the upstream "
                "producer declaration exists."
            )
        upstream_paths.append(candidate)

    return upstream_paths


def platform_validation_dependencies_available(
    source_directory: Path = LOCAL_DECLARATION_DIR,
) -> bool:
    required_paths = [
        PLATFORM_VALIDATOR_PATH,
        *(PLATFORM_VOCABULARY_DIR / file_name for file_name in VOCABULARY_FILENAMES),
    ]
    try:
        required_paths.extend(_collect_required_upstream_product_paths(source_directory))
    except FileNotFoundError:
        return False
    return all(path.exists() for path in required_paths)


def validate_repo_native_contracts(source_directory: Path = LOCAL_DECLARATION_DIR) -> list[str]:
    source_directory = source_directory.resolve()
    if not source_directory.exists():
        return [f"{source_directory}: repo-native declaration directory does not exist"]

    local_paths = _collect_local_declaration_paths(source_directory)
    if not local_paths:
        return [f"{source_directory}: no repo-native declaration files were found"]

    validator = _load_platform_validator()
    upstream_paths = _collect_required_upstream_product_paths(source_directory)

    with tempfile.TemporaryDirectory(prefix="lotus-report-domain-products-") as temp_dir_string:
        temp_root = Path(temp_dir_string)
        temp_declaration_dir = temp_root / "domain-data-products"
        temp_vocabulary_dir = temp_root / "domain-vocabulary"
        temp_declaration_dir.mkdir(parents=True, exist_ok=True)
        temp_vocabulary_dir.mkdir(parents=True, exist_ok=True)

        for declaration_path in local_paths:
            shutil.copy2(declaration_path, temp_declaration_dir / declaration_path.name)

        for upstream_path in upstream_paths:
            shutil.copy2(upstream_path, temp_declaration_dir / upstream_path.name)

        for vocabulary_file_name in VOCABULARY_FILENAMES:
            shutil.copy2(
                PLATFORM_VOCABULARY_DIR / vocabulary_file_name,
                temp_vocabulary_dir / vocabulary_file_name,
            )

        return validator.validate_contract_directory(temp_declaration_dir)


def main() -> int:
    issues = validate_repo_native_contracts()
    if issues:
        for issue in issues:
            print(issue)
        return 1

    producer_count = len(list(LOCAL_DECLARATION_DIR.glob(PRODUCT_PATTERN)))
    consumer_count = len(list(LOCAL_DECLARATION_DIR.glob(CONSUMER_PATTERN)))
    print(
        "Validated "
        f"{producer_count} repo-native producer declaration(s) and "
        f"{consumer_count} repo-native consumer declaration(s) in {LOCAL_DECLARATION_DIR}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
