# Auto-Start (macOS)

Run Claude Croxy on login via a LaunchAgent, with auto-resume of the last MLX model and a watchdog that restarts crashed MLX processes.

## Setup

Run the app manually at least once to seed the admin account. Make sure `.env` has `CCM_ADMIN_EMAIL` and `CCM_ADMIN_PASSWORD` set (see [`/.env.example`](../.env.example)), then:

```bash
python -m app.main
```

Then install the LaunchAgent:

```bash
bash scripts/launchd_setup.sh install
```

The app will now auto-start on login, auto-restart if killed, and resume the last MLX model you used.

## Management commands

```bash
bash scripts/launchd_setup.sh status     # Check if service is running
bash scripts/launchd_setup.sh restart    # Restart the service
bash scripts/launchd_setup.sh logs       # Tail log files
bash scripts/launchd_setup.sh uninstall  # Remove auto-start
```

Logs are written to `logs/ccm-stdout.log` and `logs/ccm-stderr.log` in the project directory.

## `.env` and LaunchAgent

The app loads `.env` from the project root using an absolute path (resolved at import time from `app/main.py`), so the LaunchAgent picks it up the same way a shell run does. You don't need to mirror env vars into the plist's `EnvironmentVariables`.

If you ever want to override a single variable just for the launchd run (e.g. force `CCM_LOG_LEVEL=DEBUG` without touching `.env`), add it to the `EnvironmentVariables` dict in `scripts/com.ccm.effortless-claude-code.plist` and reinstall — those values take precedence over `.env`.

## MLX Watchdog

When the app starts, it automatically:

1. Resumes the last MLX model you selected (stored in the database)
2. Starts a watchdog thread that checks the MLX process every 30 seconds
3. If the MLX process crashes, the watchdog restarts it (up to 5 consecutive retries)
4. If you stop the MLX server intentionally via the admin UI, the watchdog will not restart it

The retry counter resets after 5 minutes of stable running, so transient failures are handled gracefully.
