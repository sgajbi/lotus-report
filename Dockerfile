FROM python:3.12-slim

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
