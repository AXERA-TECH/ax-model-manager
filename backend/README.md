# AXERA Model Manager Backend

Current status: **read-only model discovery MVP by default**. A
development-only `fake-subprocess` backend implements local start/stop job
lifecycles for tests, but it is not enabled by normal `./scripts/dev_server.sh`.

## Install

```bash
cd <repository-directory>
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pip install -e backend
```

## Tests

```bash
.venv/bin/python -m pytest backend/tests -q
```

## Local server

From the project root:

```bash
./scripts/dev_server.sh
```

The script creates local fixture models and starts:

```text
http://127.0.0.1:18090
```

The development token is:

```text
local-dev-token
```

Examples:

```bash
curl http://127.0.0.1:18090/health

curl \
  -H 'Authorization: Bearer local-dev-token' \
  http://127.0.0.1:18090/api/v1/models

curl \
  -H 'Authorization: Bearer local-dev-token' \
  http://127.0.0.1:18090/api/v1/models/fixture-model/validate
```

Interactive API docs:

```text
http://127.0.0.1:18090/docs
```

## Built-in admin UI

The backend serves a dependency-free admin UI:

```text
http://127.0.0.1:18090/
```

Enter the configured bearer token in the top-right corner and click
**Connect**. The UI can discover models, confirm inferred manifests, start and
stop runtimes, inspect jobs, read logs, and view audit records.

## Current API

| Method | Path | Description |
---|---|---|
| GET | `/health` | Public health and mode |
| GET | `/api/v1/config` | Runtime configuration, secrets omitted |
| GET | `/api/v1/models` | Model summaries |
| GET | `/api/v1/models/{id}` | Model detail and artifact checks |
| GET | `/api/v1/models/{id}/manifest` | Package or inferred draft manifest |
| GET | `/api/v1/models/{id}/validate` | Manifest/artifact validation result |

Additional endpoints when `enable_control=true`:

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/models/{id}/start` | Start the selected runtime |
| POST | `/api/v1/models/{id}/stop` | Stop with IN USE guard |
| POST | `/api/v1/models/{id}/switch-to` | Stop current runtime and start target |
| POST | `/api/v1/models/{id}/restart` | Stop and start the same model |
| PUT | `/api/v1/models/{id}/profiles/{profile}` | Save a structured launch profile |
| DELETE | `/api/v1/models/{id}/profiles/{profile}` | Delete a non-default profile |
| POST | `/api/v1/models/{id}/manifest/confirm` | Confirm an inferred manifest |
| GET | `/api/v1/jobs` | List jobs |
| GET | `/api/v1/jobs/{job_id}` | Get job state |
| GET | `/api/v1/jobs/{job_id}/logs` | Read a job log tail |
| GET | `/api/v1/audit` | Read audit records |
| GET | `/api/v1/dashboard` | Aggregated dashboard state |

Production systemd start/stop/switch/restart is implemented.
Running jobs also retain the model IDs advertised by the runtime's
`GET /v1/models` endpoint. The admin UI uses this to show the correct
OpenAI-compatible base URL and request example.
