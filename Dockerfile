FROM python:3.12.14-slim-bookworm AS base

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

# Only this separate process needs the local ML runtime and model weights.
# The model is downloaded and checksum-verified at build time, never per request.
FROM base AS classifier
USER root
COPY requirements-classification.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-classification.txt \
    && python -m scripts.fetch_yamnet --output /opt/urbanecho-models/yamnet.tflite
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER 10001:10001
CMD ["python", "-m", "app.classification_worker"]

# Keep the default API/measurement-worker build free of ML dependencies.
FROM base AS application
