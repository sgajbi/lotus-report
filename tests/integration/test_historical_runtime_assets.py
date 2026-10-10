"""The ordinary runtime wheel must contain exact producer schemas and validator."""

import json
import shutil
import subprocess
import sys
from email.parser import Parser
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile


def test_runtime_wheel_contains_exact_historical_schema_closure(tmp_path):
    root = Path(__file__).parents[2]
    build_root = tmp_path / "build-source"
    build_root.mkdir()
    shutil.copy2(root / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copytree(
        root / "src", build_root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info")
    )
    wheels = tmp_path / "wheels"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(build_root),
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheels),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (wheel,) = wheels.glob("*.whl")
    manifest = json.loads(
        (root / "tests/fixtures/composite-historical-policy/manifest.json").read_bytes()
    )
    with ZipFile(wheel) as archive:
        schemas = [
            name
            for name in archive.namelist()
            if name.startswith("app/composite_reporting/historical_schemas/")
        ]
        assert len(schemas) == 8
        for name in schemas:
            assert (
                sha256(archive.read(name)).hexdigest()
                == manifest["raw_file_sha256"][Path(name).name]
            )
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        dependencies = (
            Parser().parsestr(archive.read(metadata).decode()).get_all("Requires-Dist", [])
        )
        assert "jsonschema==4.23.0" in dependencies
