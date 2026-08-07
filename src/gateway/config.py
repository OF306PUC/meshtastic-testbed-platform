import os
from pathlib import Path

# All values are env-overridable so the receiver runs identically on the host
# (defaults below) and inside a container (BROKER_ADDRESS=mosquitto, etc.).
BROKER_ADDRESS = os.getenv("BROKER_ADDRESS", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
CLIENT_ID = os.getenv("CLIENT_ID", "meshtastic-testbed-gateway")

# Broker credentials. Empty means connect anonymously, which the broker refuses
# once mosquitto.conf sets `allow_anonymous false` — see mqtt/aclfile for which
# topics this account may write.
#
# The per-role name is read first because that is what configuration.env defines
# and what `env_file:` injects verbatim; ${VAR} in a compose `environment:` block
# cannot see env_file values, so remapping there would pass empty strings. The
# generic name stays as a fallback for running the receiver outside compose.
MQTT_USERNAME = os.getenv("MQTT_USERNAME_GATEWAY") or os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD_GATEWAY") or os.getenv("MQTT_PASSWORD", "")

# Resolve relative to the repo root (this file lives at <root>/src/gateway/config.py),
# so it works regardless of the current working directory (e.g. under systemd).
# In a container the file is mounted at /app/mesh_config.json, which is where
# parents[2] resolves to as well; override with MESH_CONFIG_PATH if needed.
MESH_CONFIG_PATH = os.getenv(
    "MESH_CONFIG_PATH",
    str(Path(__file__).resolve().parents[2] / "mesh_config.json"),
)