"""Run the repository's mypy, refusing to run it from the wrong environment.

The pre-commit hook invokes mypy through the interpreter on `PATH`, which is
the only way to make the hook and CI the same checker. It also means a commit
from an unactivated shell resolves whichever interpreter happens to be there.

That case has to be refused rather than tolerated, because it does not fail on
its own. `mypy.ini` sets `ignore_missing_imports = True`, so an interpreter
carrying mypy but not this project's dependencies resolves every project
import to `Any`, finds nothing, and reports:

    Success: no issues found in 129 source files

which is exactly the vacuous pass this hook exists to prevent. A missing mypy
at least fails loudly; a mypy with a missing dependency graph does not.

So: assert the environment can import what the typecheck depends on, name the
first one that is absent, then hand over to mypy.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

#: Third-party packages whose contracts the typecheck actually reads. Absence
#: of any one of them means mypy would silently resolve that library's types
#: to `Any` rather than checking against them.
REQUIRED_FOR_TYPECHECK = (
    "pydantic",
    "pydantic_settings",
    "fastapi",
    "starlette",
    "httpx",
    "psycopg",
    "prometheus_fastapi_instrumentator",
)


def _missing() -> list[str]:
    return [name for name in REQUIRED_FOR_TYPECHECK if importlib.util.find_spec(name) is None]


def main() -> int:
    missing = _missing()
    if missing:
        print(
            "mypy would run without this project's dependencies, and "
            "`ignore_missing_imports` would make that a silent pass.\n"
            f"  interpreter : {sys.executable}\n"
            f"  missing     : {', '.join(missing)}\n"
            "Activate the project environment and commit again "
            "(see wiki/Getting-Started.md). Do not use --no-verify.",
            file=sys.stderr,
        )
        return 1

    return subprocess.call([sys.executable, "-m", "mypy", "--config-file", "mypy.ini"])


if __name__ == "__main__":
    raise SystemExit(main())
