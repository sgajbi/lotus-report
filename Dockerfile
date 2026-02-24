FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN python -m pip install --upgrade pip && pip install .

COPY src /app/src

ENV PYTHONPATH=/app/src
EXPOSE 8300

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
