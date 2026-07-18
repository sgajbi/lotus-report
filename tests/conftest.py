import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration.postgres_adapter_ownership import postgres_adapter_scope

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def close_owned_postgres_adapters() -> Iterator[None]:
    """Close database-backed adapters explicitly after every test."""

    with postgres_adapter_scope():
        yield
