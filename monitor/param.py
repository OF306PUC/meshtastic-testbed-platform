import os

# InfluxDB
DB_HOST     = os.environ.get("DB_HOST",     "localhost")
DB_PORT     = int(os.environ.get("DB_PORT", 8086))
DB_USERNAME = os.environ.get("DB_USERNAME", "admin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "admin")
DB_NAME     = os.environ.get("DB_NAME",     "cpsrtc_lora_telemetry")

# MQTT broker
BROKER_ADDRESS = os.environ.get("BROKER_ADDRESS", "localhost")
BROKER_PORT    = int(os.environ.get("BROKER_PORT", 1883))
CLIENT_ID      = os.environ.get("CLIENT_ID",      "lora-testbed-monitor")

# Subscribe to all nodes, all data types: lora-testbed/<node>/<type>
SUBSCRIBE_TOPIC = os.environ.get("SUBSCRIBE_TOPIC", "lora-testbed/+/+")

TOTAL_SAMPLES_48HRS = 576
TOTAL_SAMPLES_24HRS = 288