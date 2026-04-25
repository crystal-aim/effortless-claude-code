#!/usr/bin/env bash
set -euo pipefail

LABEL="com.ccm.effortless-claude-code"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
VENV_BIN="$PROJECT_DIR/.venv/bin"
PLIST_TEMPLATE="$SCRIPT_DIR/$LABEL.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$PROJECT_DIR/logs"

cmd_install() {
    if [ ! -f "$VENV_PYTHON" ]; then
        echo "Error: venv not found at $VENV_PYTHON"
        echo "Run: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
        exit 1
    fi

    if [ ! -f "$PLIST_TEMPLATE" ]; then
        echo "Error: plist template not found at $PLIST_TEMPLATE"
        exit 1
    fi

    mkdir -p "$LOG_DIR"

    sed \
        -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__VENV_BIN__|$VENV_BIN|g" \
        "$PLIST_TEMPLATE" > "$PLIST_DEST"

    launchctl load "$PLIST_DEST"

    echo "Installed and loaded $LABEL"
    echo "  Plist: $PLIST_DEST"
    echo "  Logs:  $LOG_DIR/ccm-stdout.log"
    echo "         $LOG_DIR/ccm-stderr.log"

    if [ ! -f "$PROJECT_DIR/data.db" ]; then
        echo ""
        echo "Warning: No data.db found. On first run you need:"
        echo "  CCM_ADMIN_EMAIL=... CCM_ADMIN_PASSWORD=... python -m app.main"
        echo "Then reinstall this service."
    fi
}

cmd_uninstall() {
    if [ -f "$PLIST_DEST" ]; then
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
        rm -f "$PLIST_DEST"
        echo "Uninstalled $LABEL"
    else
        echo "Not installed (plist not found at $PLIST_DEST)"
    fi
}

cmd_status() {
    echo "=== launchd ==="
    launchctl list 2>/dev/null | grep "$LABEL" || echo "(not loaded)"

    echo ""
    echo "=== health check ==="
    if curl -s --max-time 3 http://localhost:4000/healthz 2>/dev/null; then
        echo ""
        echo "App is responding."
    else
        echo "App is not responding on port 4000."
    fi
}

cmd_restart() {
    if [ -f "$PLIST_DEST" ]; then
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
        launchctl load "$PLIST_DEST"
        echo "Restarted $LABEL"
    else
        echo "Not installed. Run: $0 install"
        exit 1
    fi
}

cmd_logs() {
    if [ -d "$LOG_DIR" ]; then
        tail -f "$LOG_DIR/ccm-stdout.log" "$LOG_DIR/ccm-stderr.log"
    else
        echo "No logs directory found at $LOG_DIR"
        exit 1
    fi
}

usage() {
    echo "Usage: $0 {install|uninstall|status|restart|logs}"
    echo ""
    echo "Commands:"
    echo "  install    Install and start the LaunchAgent"
    echo "  uninstall  Stop and remove the LaunchAgent"
    echo "  status     Check if the service is running"
    echo "  restart    Restart the service"
    echo "  logs       Tail the log files"
}

case "${1:-}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    status)    cmd_status ;;
    restart)   cmd_restart ;;
    logs)      cmd_logs ;;
    *)         usage; exit 1 ;;
esac
