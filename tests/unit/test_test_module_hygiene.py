from collections import defaultdict
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]


def test_pytest_module_basenames_are_unique_across_the_repository() -> None:
    modules_by_name: dict[str, list[str]] = defaultdict(list)
    for path in TESTS_ROOT.rglob("test_*.py"):
        modules_by_name[path.name].append(path.relative_to(TESTS_ROOT).as_posix())

    duplicates = {name: paths for name, paths in sorted(modules_by_name.items()) if len(paths) > 1}

    assert duplicates == {}, (
        "Duplicate pytest module basenames can cause import-file mismatch during repository-wide "
        f"collection: {duplicates}"
    )
