"""One governed document, one name (render#120 programme)."""

from app.reporting_render.document_reference import mint_document_reference


def _reference(**overrides):
    identity = {
        "report_job_id": "job_1",
        "snapshot_id": "rsnap_1",
        "template_id": "portfolio-review",
        "template_version": "v1",
    }
    identity.update(overrides)
    return mint_document_reference(**identity)


def test_a_rerender_of_the_same_snapshot_converges_on_the_same_reference():
    """Two attempts at the same governed document must not mint two names -
    the reference binds the financial question (job, snapshot, template), and
    nothing per-attempt is even a parameter."""

    assert _reference() == _reference()


def test_each_identity_input_changes_the_reference():
    """A regenerate (new snapshot), a different job, and a corrected template
    are each their own governed document and carry their own identity, per
    the lifecycle's correction rule."""

    base = _reference()
    variants = {
        base,
        _reference(report_job_id="job_2"),
        _reference(snapshot_id="rsnap_2"),
        _reference(template_id="proof-pack"),
        _reference(template_version="v2"),
    }

    assert len(variants) == 5


def test_the_format_is_the_recorded_contract():
    """`rdoc_<uuid5>` - opaque to every consumer. Render places it, Archive
    stores it, nobody parses it; but the prefix makes a reference legible in
    a log as what it is."""

    reference = _reference()

    assert reference.startswith("rdoc_")
    assert len(reference) == len("rdoc_") + 36
