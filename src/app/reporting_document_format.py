"""One document per existing Report render lifecycle."""

from collections.abc import Collection


def document_output_format(output_formats: Collection[str] | None) -> str | None:
    formats = set(output_formats or ()) & {"pdf", "xlsx"}
    if len(formats) > 1:
        raise ValueError("REPORT_MULTIPLE_DOCUMENT_FORMATS_NOT_SUPPORTED")
    return next(iter(formats), None)
