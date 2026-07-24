import os
from pathlib import Path

# All values are env-overridable so the receiver runs identically on the host
# (defaults below) and inside a container (BROKER_ADDRESS=mosquitto, etc.).
BROKER_ADDRESS = os.getenv("BROKER_ADDRESS", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
CLIENT_ID = os.getenv("CLIENT_ID", "meshtastic-testbed-gateway")

# Resolve relative to the repo root (this file lives at <root>/src/gateway/config.py),
# so it works regardless of the current working directory (e.g. under systemd).
# In a container the file is mounted at /app/mesh_config.json, which is where
# parents[2] resolves to as well; override with MESH_CONFIG_PATH if needed.
MESH_CONFIG_PATH = os.getenv(
    "MESH_CONFIG_PATH",
    str(Path(__file__).resolve().parents[2] / "mesh_config.json"),
)