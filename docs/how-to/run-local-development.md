# How to run ChannelWatch in local development

Use the source backend and Next development server together when changing the browser UI. The development proxy keeps `/api`, `/healthz`, and `/metrics` same-origin in the browser; production continues to use the static Next export served by FastAPI.

## Prerequisites

Install the Python and frontend dependencies, then create a disposable local configuration directory. Do not point `CONFIG_PATH` at a production `/config` directory or the Windows snapshots.

```bash
cd /path/to/ChannelWatch
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -c deploy/requirements/dev.constraints.txt -r deploy/requirements/runtime.txt -r deploy/requirements/dev.txt
corepack enable
cd app/ui
pnpm install --frozen-lockfile
cd ../..
mkdir -p .dev-config
```

## Start FastAPI and Next together

Run this sequence from the repository root. It starts the FastAPI backend on `127.0.0.1:8501`, starts Next on `127.0.0.1:3000`, and stops the background backend when the Next process exits.

```bash
source .venv/bin/activate
CONFIG_PATH="$PWD/.dev-config" CW_DISABLE_AUTH=true \
  CHANNELWATCH_IMAGE_APP_DIR="$PWD/app" PYTHONPATH=app \
  python -m core.runtime_launcher ui \
    --host 127.0.0.1 --port 8501 --log-level warning &
channelwatch_backend_pid=$!
trap 'kill "$channelwatch_backend_pid" 2>/dev/null || true' EXIT INT TERM
cd app/ui
CHANNELWATCH_DEV_API_ORIGIN=http://127.0.0.1:8501 pnpm dev
```

Open `http://127.0.0.1:3000`. `CW_DISABLE_AUTH=true` is suitable only for this loopback development session and produces an intentional warning. Omit it when testing authentication. The FastAPI-only development process does not run the core DVR monitor; use the Docker integration environment for monitor lifecycle or live-DVR testing.

To confirm that production export behavior remains intact, run:

```bash
cd app/ui
pnpm typecheck
pnpm test
pnpm build
```
