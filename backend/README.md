# AXERA Model Manager Backend

Current status: **read-only model discovery MVP**. It deliberately has no
start/stop/restart endpoints yet.

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

## Current API

| Method | Path | Description |
---|---|---|
| GET | `/health` | Public health and mode |
| GET | `/api/v1/config` | Runtime configuration, secrets omitted |
| GET | `/api/v1/models` | Model summaries |
| GET | `/api/v1/models/{id}` | Model detail and artifact checks |
| GET | `/api/v1/models/{id}/manifest` | Package or inferred draft manifest |
| GET | `/api/v1/models/{id}/validate` | Manifest/artifact validation result |

Control endpoints such as start, stop, restart, and switch are intentionally
not implemented in this phase.
