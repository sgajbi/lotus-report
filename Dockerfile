ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
FROM ${PYTHON_IMAGE}
ARG PYTHON_IMAGE

LABEL org.opencontainers.image.base.name="docker.io/library/python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
LABEL org.opencontainers.image.base.digest="sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
LABEL io.lotus.image.distribution="${PYTHON_IMAGE}"

WORKDIR /app

# constraints.txt pins the image's runtime subset to the same resolved
# closure CI validates (report#345): a constraints file only constrains what
# is installed, so the runtime-only resolve stays a subset of the recorded
# dev closure instead of a third, unrecorded resolution.
COPY pyproject.toml /app/pyproject.toml
COPY constraints.txt /app/constraints.txt
RUN python -m pip install --upgrade pip && pip install . -c constraints.txt

COPY migrations /app/migrations
COPY src /app/src

ENV PYTHONPATH=/app/src
EXPOSE 8300

CMD ["sh", "-c", "python -m app.runtime_schema && exec uvicorn app.main:app --host 0.0.0.0 --port 8300"]
