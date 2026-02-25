import argparse
import json
import re
from pathlib import Path

KEYWORDS = (
    "amount",
    "price",
    "rate",
    "value",
    "market_value",
    "cost",
    "pnl",
    "return",
    "risk",
    "notional",
    "weight",
)
IGNORE_DIRS = {"tests", ".venv", "venv", "docs", "rfcs", "output", "build", "dist", "__pycache__"}

FLOAT_ANNOTATION = re.compile(r"\bfloat\b")


def is_candidate(path: Path) -> bool:
    parts = set(path.parts)
    if any(p in parts for p in IGNORE_DIRS):
        return False
    return path.suffix == ".py"


def scan_repo(repo_root: Path) -> list[str]:
    findings: list[str] = []
    for file_path in repo_root.rglob("*.py"):
        if not is_candidate(file_path.relative_to(repo_root)):
            continue
        rel = file_path.relative_to(repo_root).as_posix()
        for line_no, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
            lowered = line.lower()
            if not any(k in lowered for k in KEYWORDS):
                continue
            if not FLOAT_ANNOTATION.search(lowered):
                continue
            if "# monetary-float-allow" in lowered:
                continue
            finding = f"{rel}:{line_no}:{line.strip()}"
            findings.append(finding)
    return sorted(set(findings))


def load_allowlist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("allowlist", []))


def write_allowlist(path: Path, findings: list[str]) -> None:
    payload = {
        "description": "Approved baseline monetary-float findings. New findings fail CI.",
        "allowlist": findings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard against unauthorized monetary float usage")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--allowlist",
        default="docs/standards/monetary-float-allowlist.json",
    )
    parser.add_argument("--update-allowlist", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    allowlist_path = (repo_root / args.allowlist).resolve()
    findings = scan_repo(repo_root)

    if args.update_allowlist:
        write_allowlist(allowlist_path, findings)
        print(f"Updated allowlist with {len(findings)} finding(s): {allowlist_path}")
        return 0

    allowlist = load_allowlist(allowlist_path)
    unexpected = sorted(set(findings) - allowlist)

    if unexpected:
        print("Unauthorized monetary float usage detected:")
        for item in unexpected:
            print(f" - {item}")
        print(f"\nBaseline allowlist file: {allowlist_path}")
        print("If intentional and approved, run with --update-allowlist in dedicated PR.")
        return 1

    print(f"Monetary float guard passed. Findings={len(findings)}, allowlisted={len(allowlist)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
