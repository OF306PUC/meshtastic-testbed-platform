import os

# InfluxDB
DB_HOST     = os.environ.get("DB_HOST",     "localhost")
DB_PORT     = int(os.environ.get("DB_PORT", 8086))
DB_USERNAME = os.environ.get("DB_USERNAME", "admin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "admin")
DB_NAME     = os.environ.get("DB_NAME",     "cpsrtc_meshtastic_telemetry")

# MQTT broker
BROKER_ADDRESS = os.environ.get("BROKER_ADDRESS", "localhost")
BROKER_PORT    = int(os.environ.get("BROKER_PORT", 1883))
CLIENT_ID      = os.environ.get("CLIENT_ID",      "meshtastic-testbed-monitor")

# Broker credentials. Empty means anonymous, which the broker refuses once
# mosquitto.conf sets `allow_anonymous false`. This account is read-only —
# see mqtt/aclfile. The per-role name comes straight from configuration.env via
# `env_file:`; the generic one is the fallback for running outside compose.
MQTT_USERNAME  = os.environ.get("MQTT_USERNAME_MONITOR") or os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD  = os.environ.get("MQTT_PASSWORD_MONITOR") or os.environ.get("MQTT_PASSWORD", "")

# Subscribe to all nodes, all data types: meshtastic-testbed/<node>/<type>
SUBSCRIBE_TOPIC = os.environ.get("SUBSCRIBE_TOPIC", "meshtastic-testbed/+/+")

# Surveyed node positions live in mesh_config.json, mounted read-only into the
# container. Absent or unreadable is a normal state, not an error: the map then
# falls back to what the nodes report over GPS.
MESH_CONFIG_PATH = os.environ.get("MESH_CONFIG_PATH", "/app/mesh_config.json")

TOTAL_SAMPLES_48HRS = 576
TOTAL_SAMPLES_24HRS = 288