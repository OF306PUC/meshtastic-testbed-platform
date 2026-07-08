#!/usr/bin/env bash
# Installs lora-gateway.service as a systemd unit.
# Usage: sudo ./install_service.sh [--port /dev/ttyACM0]

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
SERIAL_PORT="/dev/ttyACM0"

while [[ $# -gt 0 ]]; do
    case $1 in
        --port) SERIAL_PORT="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ── Paths (resolved from the script's own location) ───────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/lora-gateway.service"

# ── Checks ────────────────────────────────────────────────────────────────────
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "ERROR: venv not found at $VENV_PYTHON — run: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

if [[ ! -c "$SERIAL_PORT" ]]; then
    echo "WARNING: Serial port $SERIAL_PORT not found. The service will still be installed."
fi

# ── Write unit file ───────────────────────────────────────────────────────────
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=LoRa Meshtastic Gateway Receiver
After=network.target docker.service
Requires=docker.service

[Service]
User=$SUDO_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PYTHON src/gateway/receiver.py --port $SERIAL_PORT
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "Written → $SERVICE_FILE"

# ── Enable & start ────────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable lora-gateway.service
systemctl restart lora-gateway.service

echo ""
echo "Service installed and started."
echo "  Status : sudo systemctl status lora-gateway"
echo "  Logs   : journalctl -u lora-gateway -f"
echo "  Stop   : sudo systemctl stop lora-gateway"
