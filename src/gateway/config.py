from pathlib import Path

BROKER_ADDRESS = "localhost"
BROKER_PORT = 1883
CLIENT_ID = "meshtastic-testbed-gateway"

# Resolve relative to the repo root (this file lives at <root>/src/gateway/config.py),
# so it works regardless of the current working directory (e.g. under systemd).
MESH_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "mesh_config.json")