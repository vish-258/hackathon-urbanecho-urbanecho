FROM python:3.12.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --gid 10001 noise \
    && useradd --uid 10001 --gid 10001 --no-create-home noise \
    && mkdir -p /data/audio \
    && chown -R noise:noise /data/audio

COPY --chown=noise:noise app ./app
COPY --chown=noise:noise migrations ./migrations
COPY --chown=noise:noise scripts ./scripts
COPY --chown=noise:noise tests ./tests
COPY --chown=noise:noise alembic.ini pyproject.toml ./

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
