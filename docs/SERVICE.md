# Running Ambient As A Service

Ambient is designed to run continuously under a supervisor. The two supported
examples below are:

- systemd (Linux)
- launchd (macOS)

Operationally important commands:

- `ambient doctor <repo>`: preflight checks (Docker present, sandbox image present, tools runnable)
- `ambient watch <repo>`: long-running daemon process (foreground)
- `ambient status <repo> --health`: supervision-friendly health check (exit code)

## systemd (Linux)

Example unit: `docs/service/systemd/ambient-swarm@.service`

Notes:

- Prefer `--approval-mode webhook` for unattended operation.
- `Restart=on-failure` is recommended.
- Set `AMBIENT_TELEMETRY_PATH` to a persistent location if desired.

## launchd (macOS)

Example plist: `docs/service/launchd/com.ambient.swarm.plist`

Notes:

- Ensure Docker Desktop is installed and running before starting the job.
- Use `ambient doctor` during bootstrapping to validate the sandbox image.

