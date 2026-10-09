"""Actual Docker metadata confirmation refuses missing, mismatched and failed inputs."""

import json
from types import SimpleNamespace

import pytest

from scripts.verify_ci_image_acquisition import verify

REFERENCE = "public.ecr.aws/docker/library/postgres@sha256:" + "a" * 64


def _runner(*, image=None, container="sha256:actual", error=None):
    calls = []
    metadata = (
        image
        if image is not None
        else {
            "Id": "sha256:actual",
            "RepoDigests": [REFERENCE],
            "Os": "linux",
            "Architecture": "amd64",
        }
    )

    def run(command, **kwargs):
        calls.append(command)
        assert "pull" not in command and kwargs["timeout"] == 20
        if error:
            return SimpleNamespace(returncode=1, stdout="", stderr=error)
        value = container if command[1] == "container" else metadata
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    return run, calls


def test_confirms_actual_service_image_without_reading_environment(capsys):
    run, calls = _runner()
    verify(REFERENCE, "exact-service-container", run)
    assert len(calls) == 2 and calls[-1][-1] == "exact-service-container"
    assert calls[-1][-2] == "{{json .Image}}"
    assert "RepoDigests" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error", ["unavailable", "unauthorized", "toomanyrequests", "daemon unavailable"]
)
def test_acquisition_failure_is_preserved_and_refused(error, capsys):
    run, calls = _runner(error=error)
    with pytest.raises(ValueError, match="unavailable"):
        verify(REFERENCE, "exact-service-container", run)
    assert len(calls) == 1 and error in capsys.readouterr().err


@pytest.mark.parametrize(
    "change",
    [
        {"RepoDigests": []},
        {"RepoDigests": [REFERENCE[:-1] + "b"]},
        {"Architecture": "arm64"},
        {"Os": "windows"},
        {"Id": None},
    ],
)
def test_digest_platform_and_identity_mismatch_are_refused(change):
    image = {
        "Id": "sha256:actual",
        "RepoDigests": [REFERENCE],
        "Os": "linux",
        "Architecture": "amd64",
        **change,
    }
    run, _ = _runner(image=image)
    with pytest.raises(ValueError):
        verify(REFERENCE, None, run)


def test_service_container_must_use_the_verified_image():
    run, _ = _runner(container="sha256:another-image")
    with pytest.raises(ValueError, match="Service container"):
        verify(REFERENCE, "exact-service-container", run)


@pytest.mark.parametrize(
    "reference",
    [
        "postgres:16-alpine",
        "public.ecr.aws/docker/library/postgres:16-alpine",
        "unknown.example/postgres@sha256:" + "a" * 64,
        REFERENCE[:-1],
    ],
)
def test_unreviewed_or_malformed_reference_is_refused_before_docker(reference):
    run, calls = _runner()
    with pytest.raises(ValueError, match="immutable"):
        verify(reference, None, run)
    assert calls == []
