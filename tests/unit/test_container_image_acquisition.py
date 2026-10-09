"""Every Report image acquisition stays on the reviewed immutable mirror mapping."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
QUALIFIED_PLATFORM = "386b40e13e76e60e761c6c4068fbe7a11256ca29"
APPROVED = {
    "python": "public.ecr.aws/docker/library/python@sha256:"
    "a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1",
    "postgres": "public.ecr.aws/docker/library/postgres@sha256:"
    "721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea",
}


def _assert_acquisitions(sources: dict[str, str]) -> None:
    assert sources, "No image acquisition sources were inspected"
    dockerfile = sources["Dockerfile"]
    assert re.findall(r"^FROM (\S+)$", dockerfile, re.M) == ["${PYTHON_IMAGE}"]
    assert f"ARG PYTHON_IMAGE={APPROVED['python']}" in dockerfile
    assert (
        'LABEL org.opencontainers.image.base.name="docker.io/library/python@sha256:' in dockerfile
    )
    assert 'LABEL io.lotus.image.distribution="${PYTHON_IMAGE}"' in dockerfile
    # Count every invocation, not just an expected matching substring: an added
    # mutable fallback or a second image would otherwise remain invisible.
    make_images = re.findall(r"docker run[^\n]*?\s(\S+) bash -c", sources["Makefile"])
    assert make_images == ['"$(PYTHON_IMAGE)"', '"$(PYTHON_IMAGE)"']
    assert f"PYTHON_IMAGE ?= {APPROVED['python']}" in sources["Makefile"]
    assert '--build-arg PYTHON_IMAGE="$(PYTHON_IMAGE)" -f Dockerfile' in sources["Makefile"]
    for workflow in ("feature-lane", "pr-merge-gate", "main-releasability"):
        jobs = yaml.safe_load(sources[f".github/workflows/{workflow}.yml"])["jobs"]
        image_job = jobs["python-image"]
        assert image_job["uses"] == "./.github/workflows/image-acquisition.yml"
        assert image_job["with"]["distribution-image"] == APPROVED["python"]
        assert image_job["with"]["source-image"] == APPROVED["python"].replace(
            "public.ecr.aws/docker", "docker.io"
        )
        security = jobs["lint-typecheck-security"]
        assert "python-image" in security["needs"]
        assert security["env"]["PYTHON_IMAGE"] == "${{ needs.python-image.outputs.image }}"
        if workflow == "feature-lane":
            assert all("services" not in job for job in jobs.values())
            continue
        assert jobs["postgres-image"]["with"]["distribution-image"] == APPROVED["postgres"]
        assert jobs["postgres-image"]["with"]["source-image"] == APPROVED["postgres"].replace(
            "public.ecr.aws/docker", "docker.io"
        )
        assert jobs["postgres-image"]["uses"] == "./.github/workflows/image-acquisition.yml"
        services = [
            service for job in jobs.values() for service in job.get("services", {}).values()
        ]
        assert len(services) == 2
        assert all(
            service["image"] == "${{ needs.postgres-image.outputs.image }}" for service in services
        )
        assert all("--platform linux/amd64" in service["options"] for service in services)
        for job in (jobs["lint-typecheck-security"], jobs["test-suites"]):
            assert "postgres-image" in job["needs"]
            assert job["env"]["POSTGRES_IMAGE"] == "${{ needs.postgres-image.outputs.image }}"
        build = jobs["docker-build"]
        assert "python-image" in build["needs"]
        assert build["env"]["PYTHON_IMAGE"] == "${{ needs.python-image.outputs.image }}"
    compose = yaml.safe_load(sources["docker-compose.yml"])["services"]
    external = [service["image"] for service in compose.values() if "build" not in service]
    assert external == [APPROVED["postgres"]]
    assert compose["lotus-report-postgres"]["platform"] == "linux/amd64"
    for text in sources.values():
        assert "python:3.12-slim" not in text and "postgres:16-alpine" not in text


def _sources() -> dict[str, str]:
    return {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "Dockerfile",
            "Makefile",
            "docker-compose.yml",
            ".github/workflows/pr-merge-gate.yml",
            ".github/workflows/main-releasability.yml",
            ".github/workflows/feature-lane.yml",
            ".github/workflows/image-acquisition.yml",
        )
    }


def test_all_actual_acquisitions_use_the_reviewed_mapping() -> None:
    _assert_acquisitions(_sources())


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        ("Dockerfile", APPROVED["python"], "python:3.12-slim"),
        ("Makefile", APPROVED["python"], "public.ecr.aws/docker/library/python:3.12-slim"),
        ("docker-compose.yml", APPROVED["postgres"], "postgres:16-alpine"),
        (
            ".github/workflows/pr-merge-gate.yml",
            APPROVED["postgres"],
            APPROVED["postgres"][:-1] + "0",
        ),
        (
            ".github/workflows/main-releasability.yml",
            APPROVED["postgres"],
            APPROVED["postgres"].replace("public.ecr.aws", "unreviewed.example"),
        ),
    ],
)
def test_mutable_unknown_or_changed_acquisitions_are_refused(path: str, old: str, new: str) -> None:
    sources = _sources()
    sources[path] = sources[path].replace(old, new, 1)
    with pytest.raises(AssertionError):
        _assert_acquisitions(sources)


def test_missing_input_and_added_fallback_are_refused() -> None:
    with pytest.raises(AssertionError):
        _assert_acquisitions({})
    sources = _sources()
    sources["Makefile"] += "\n\tdocker run python:3.12-slim bash -c 'fallback'\n"
    with pytest.raises(AssertionError):
        _assert_acquisitions(sources)


def test_central_admission_has_no_services_and_checks_before_output() -> None:
    workflow = yaml.safe_load(_sources()[".github/workflows/image-acquisition.yml"])
    job = workflow["jobs"]["admission"]
    assert "services" not in job and "continue-on-error" not in job
    assert job["outputs"]["image"] == "${{ steps.admission.outputs.image }}"
    checkouts = [
        step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["repository"] == "sgajbi/lotus-platform"
    admission = next(step for step in job["steps"] if step.get("id") == "admission")
    assert "continue-on-error" not in admission
    for argument in (
        "automation/validate_technology_governance_policy.py",
        '--source-image "$SOURCE_IMAGE"',
        '--distribution-image "$DISTRIBUTION_IMAGE"',
        "--platform linux/amd64",
        "--verify-distribution",
        '--github-output "$GITHUB_OUTPUT"',
    ):
        assert argument in admission["run"]
    _assert_platform_ref(checkouts[0]["with"]["ref"])


def _assert_platform_ref(ref: str) -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", ref) and ref == QUALIFIED_PLATFORM


@pytest.mark.parametrize("ref", ["main", "0" * 40, "PENDING_QUALIFIED_PLATFORM_COMMIT"])
def test_unqualified_platform_revisions_are_refused(ref: str) -> None:
    with pytest.raises(AssertionError):
        _assert_platform_ref(ref)


@pytest.mark.parametrize(
    "mutation", ["direct-service", "lost-dependency", "audit-unbound", "build-unbound"]
)
def test_bypassing_admission_output_is_refused(mutation: str) -> None:
    sources = _sources()
    path = ".github/workflows/pr-merge-gate.yml"
    jobs = yaml.safe_load(sources[path])["jobs"]
    if mutation == "direct-service":
        jobs["test-suites"]["services"]["postgres"]["image"] = APPROVED["postgres"]
    elif mutation == "lost-dependency":
        jobs["test-suites"]["needs"] = ["lint-typecheck-security"]
    elif mutation == "audit-unbound":
        jobs["lint-typecheck-security"]["env"]["PYTHON_IMAGE"] = APPROVED["python"]
    else:
        jobs["docker-build"]["env"]["PYTHON_IMAGE"] = APPROVED["python"]
    sources[path] = yaml.safe_dump({"jobs": jobs})
    with pytest.raises(AssertionError):
        _assert_acquisitions(sources)
