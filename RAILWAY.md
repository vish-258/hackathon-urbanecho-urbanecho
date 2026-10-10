# Railway deployment

## Runtime update — 10 October 2026

**Cloud deployment remains blocked.** A fresh database deployment attempt on 10 October was rejected because Railway restricted the workspace after detecting unusual activity and requires a paid-plan upgrade to lift the restriction. No public application URL is verified. The existing project and volumes are retained.

The deployment package now includes the incident classifier and verified YAMNet model as well as the API and measurement worker. Use `deploy/railway/Dockerfile.application` for the application service; the root Dockerfile deliberately remains the lightweight local API image. Ten-second recording groups and daily reports run within the measurement worker. This packaging update alone does not establish a successful cloud deployment; complete the checks below before sharing a public URL.

Public `/app` access uses the existing administrator-token sign-in. Set `LOCAL_BROWSER_ACCESS=false`; automatic loopback sessions must never be enabled behind Railway's proxy. Keep credentials out of URLs and submission forms.

## Earlier deployment attempt — 9 October 2026

The deployment package passed local verification. **The Railway backend is not live.** Railway blocked the first database upload and displayed an account restriction requiring a paid-plan upgrade. There is no working public application URL yet, and no cloud end-to-end or persistence result is claimed.

Prepared project: [noise-monitor](https://railway.com/project/8c46a493-9cc5-4d74-892a-9ca5233678d4), environment `production`.

| Component | Railway ID | Prepared state |
|---|---|---|
| Project | `8c46a493-9cc5-4d74-892a-9ca5233678d4` | Created |
| Environment | `aa867f42-3abd-4d30-b204-2faf738b638f` | Production |
| `noise-postgis` | `e0e4a95b-ec3f-4a6d-841a-db4b9869b5f8` | Configured; first upload failed |
| `noise-backend` | `93796158-b8f4-45ea-8fac-8cd2fdbf9403` | Configured; not deployed |
| Database volume | `e49d0d42-ec20-4b15-97ef-5bfc30d75d14` | 500 MB, `/var/lib/postgresql/data` |
| Audio volume | `73299fdf-1e96-4a3c-bec0-317c3d58258a` | 500 MB, `/data/audio` |

Fresh deployment credentials are configured in Railway service variables; they are not included in source or the ZIP. Local development credentials/data were not copied. The existing local installation was not changed. No `/test` endpoint, dummy check-in loop, or daily processing was added.

## Layout

One application service runs the API, measurement worker and incident classifier together through `python -m scripts.serve_railway`. They use the same mounted audio directory; classification reads originals without changing them. A separate private database service runs PostgreSQL 17/PostGIS 3.5. Each service needs its own persistent volume. Railway does not run this project's local Docker Compose stack directly, and a volume cannot be shared between two services. See [Railway volumes](https://docs.railway.com/volumes/reference).

`deploy/railway/Dockerfile.postgis` packages the existing database initialization script. It creates the restricted application role and PostGIS extension. It stores PostgreSQL files below the mount root at `/var/lib/postgresql/data/pgdata`.

The application launcher validates `PORT`, requires a real mounted audio volume on Railway, changes only the mount root's ownership, and permanently drops to UID/GID 10001 before starting its three child processes. An unexpected exit of any child stops all children and returns failure so Railway can restart the service. SIGTERM/SIGINT trigger bounded cleanup. Migrations run separately before deployment. The combined service needs enough memory for the Python API, workers and model runtime; the local classifier alone has a 1 GB limit.

## Required service settings

The application Dockerfile below supersedes the setting prepared on 9 October. Reapply it to the existing service before uploading the current source.

| Setting | `noise-postgis` | `noise-backend` |
|---|---|---|
| Dockerfile, from source root | `deploy/railway/Dockerfile.postgis` | `deploy/railway/Dockerfile.application` |
| Start command | Image default | `python -m scripts.serve_railway` |
| Pre-deploy command | None | `alembic upgrade head` |
| Pre-deploy timeout | — | 300 seconds |
| Readiness path / timeout | Database checked before app deploy | `/health/ready` / 300 seconds |
| Replicas | 1 | 1 |
| Serverless sleep | Off | Off |
| Restart policy | On failure, up to 10 retries | On failure, up to 10 retries |
| Draining period | 30 seconds | 30 seconds |
| Public networking | None | Generate HTTPS domain targeting port 8000 after deploy |

The original settings were applied explicitly through Railway's API. Do not add a legacy `railway.toml`/`railway.json` to this project. See [configuration guidance](https://docs.railway.com/infrastructure-as-code) when configuration files are desired.

Database variables:

```text
RAILWAY_DOCKERFILE_PATH=deploy/railway/Dockerfile.postgis
POSTGRES_DB=noise_monitor
POSTGRES_USER=noise_migrate
POSTGRES_PASSWORD=<private generated value>
APP_DB_USER=noise_app
APP_DB_PASSWORD=<different private generated value>
PGDATA=/var/lib/postgresql/data/pgdata
```

Application variables:

```text
RAILWAY_DOCKERFILE_PATH=deploy/railway/Dockerfile.application
DB_HOST=${{noise-postgis.RAILWAY_PRIVATE_DOMAIN}}
DB_PORT=5432
POSTGRES_DB=${{noise-postgis.POSTGRES_DB}}
POSTGRES_USER=${{noise-postgis.POSTGRES_USER}}
POSTGRES_PASSWORD=${{noise-postgis.POSTGRES_PASSWORD}}
APP_DB_USER=${{noise-postgis.APP_DB_USER}}
APP_DB_PASSWORD=${{noise-postgis.APP_DB_PASSWORD}}
ADMIN_TOKEN=<private generated value, different from local development>
RAILWAY_RUN_UID=0
AUDIO_ROOT=/data/audio
PORT=8000
DB_RETRY_ATTEMPTS=15
DB_RETRY_DELAY_SECONDS=2
LOCAL_BROWSER_ACCESS=false
CLASSIFICATION_ENABLED=true
CLASSIFICATION_SCOPE=incidents
CLASSIFICATION_MODEL_PATH=/opt/urbanecho-models/yamnet.tflite
```

Railway supplies `RAILWAY_ENVIRONMENT_ID` and `RAILWAY_VOLUME_MOUNT_PATH` when its volume is attached; the latter must equal `/data/audio`. Root is used only for storage initialization, addressing [Railway's volume ownership behavior](https://docs.railway.com/volumes). API, worker and classifier run without root privileges.

The privileged migration password remains a Railway service variable because pre-deploy needs it. The launcher removes `POSTGRES_PASSWORD`, `POSTGRES_USER`, and `MIGRATION_DATABASE_URL` from child environments; this is not isolation from the service configuration or its supervisor. The normal app connection uses the restricted role. [Pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command) have private-network access but no mounted audio volume.

Changing PostgreSQL password variables after initial database creation does not rotate an existing database role. Use an explicit coordinated credential rotation when required.

## Resume once Railway lifts the restriction

Use the existing project and volumes; do not create duplicates. Check current deployment state before retrying. Deploy the database first from this exact source directory:

```sh
railway up . --path-as-root \
  --project 8c46a493-9cc5-4d74-892a-9ca5233678d4 \
  --environment aa867f42-3abd-4d30-b204-2faf738b638f \
  --service e0e4a95b-ec3f-4a6d-841a-db4b9869b5f8 --detach
```

Confirm database startup and private connectivity, then deploy the same source to the application service:

```sh
railway up . --path-as-root \
  --project 8c46a493-9cc5-4d74-892a-9ca5233678d4 \
  --environment aa867f42-3abd-4d30-b204-2faf738b638f \
  --service 93796158-b8f4-45ea-8fac-8cd2fdbf9403 --detach
```

Wait for successful pre-deploy migrations and readiness, then generate the application HTTPS domain with target port 8000. Confirm both services/volumes occupy the same region and that the database has no public domain or TCP proxy. Upload only this source directory: `.railwayignore` excludes credentials, recordings, backups and local working files. See [CLI upload behavior](https://docs.railway.com/cli/up).

Before reporting the cloud installation as working, verify HTTPS readiness at the latest migration, protected routes rejecting unauthenticated requests, disabled automatic local sessions, `/app` sign-in, a clearly labelled synthetic WAV upload processed by the worker, incident events over authenticated SSE/reconnect, classification model readiness, playable incident audio with a saved sound estimate, and database/audio/event persistence after one controlled restart. Cloud networking, HTTPS, Railway volume permissions, worker survival and account resource limits remain unverified until those checks pass. Physical devices need cloud endpoint settings and their own cloud registration; deploying the backend does not redirect existing boards or copy local recordings.

The two currently allocated volumes are 500 MB each. Select suitable capacity, retention and backup arrangements before sustained real-device recording. Do not remove volumes to resolve a deployment failure.

## Local verification

```sh
./scripts/test-railway.sh
```

The current harness builds the full application image and checks all three processes, the current Alembic head, disabled local access, classifier model readiness and saved incident audio/classification. It retains the original upload, restart/persistence, privilege and storage checks. Its database and audio volumes are disposable and separate from the development installation.

**10 October 2026:** all 31 launcher tests and the full isolated Railway harness passed. The combined runtime verified authentication, current schema, restricted database privileges, all process identities, model readiness, saved incident audio/classification, original-audio and incident/event persistence after recreation, classifier failure supervision, and rejection of missing persistent storage. These are local packaging results; the Railway account restriction still prevents cloud deployment and no public URL is verified.

Historical result, 9 October: the earlier harness passed with a separate disposable Compose project, fresh private credentials, the custom PostGIS image, a root-owned audio volume, dynamic port 8087, and the two-process launcher. It confirmed migration `0003_legacy_audio_guard`, restricted database privileges, real upload/processing/incident behavior, original-audio checksums and event/recovery persistence after container recreation, privilege dropping, child credential filtering, failure supervision, and refusal to start without mounted audio storage. All 27 launcher tests also passed then. Those historical results do not validate the newer classifier package. See `VERIFICATION.md` for the broader backend's earlier verification evidence.
