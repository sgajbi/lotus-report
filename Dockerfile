FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN python -m pip install --upgrade pip && pip install .

COPY migrations /app/migrations
COPY src /app/src

ENV PYTHONPATH=/app/src
EXPOSE 8300

CMD ["sh", "-c", "python -m app.runtime_schema && exec uvicorn app.main:app --host 0.0.0.0 --port 8300"]
