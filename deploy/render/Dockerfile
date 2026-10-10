# One free web service supervises the API, measurement worker and classifier.
# Only disposable audio cache lives here; PostgreSQL and originals use Supabase.
FROM python:3.12.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    LOCAL_BROWSER_ACCESS=false \
    AUDIO_STORAGE_BACKEND=supabase \
    AUDIO_ROOT=/tmp/urbanecho-audio-cache \
    MIGRATION_SEARCH_PATH=public,extensions \
    CLASSIFICATION_ENABLED=true \
    CLASSIFICATION_SCOPE=incidents \
    CLASSIFICATION_MODEL_PATH=/opt/urbanecho-models/yamnet.tflite \
    DB_POOL_SIZE=3 \
    DB_MAX_OVERFLOW=1
WORKDIR /app

COPY requirements.txt requirements-classification.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip install --no-cache-dir --require-hashes -r requirements-classification.txt \
    && groupadd --gid 10001 noise \
    && useradd --uid 10001 --gid 10001 --no-create-home noise \
    && mkdir -p /tmp/urbanecho-audio-cache \
    && chown noise:noise /tmp/urbanecho-audio-cache \
    && chmod 0700 /tmp/urbanecho-audio-cache

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini pyproject.toml ./
RUN python -m scripts.fetch_yamnet --output /opt/urbanecho-models/yamnet.tflite

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "scripts.serve_render"]
