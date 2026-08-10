import os

# Which PBX site this collector runs at. It is both the MQTT topic segment and
# the broker account name, because mqtt/aclfile confines each site to its own
# subtree — a collector at p1 cannot write p2's namespace even by mistake.
SITE = os.getenv("COLLECTOR_SITE", "p1")

# Serial console of the LiLyGO (the Meshtastic node). Prefer a
# /dev/serial/by-id/ path: the ttyACM numbering is assignment order, not
# identity, and reading the wrong device starts cleanly and reports nothing.
NODE_SERIAL_PORT = os.getenv("NODE_SERIAL_PORT", "")
NODE_BAUDRATE    = int(os.getenv("NODE_BAUDRATE", "115200"))

BROKER_ADDRESS = os.getenv("BROKER_ADDRESS", "localhost")
BROKER_PORT    = int(os.getenv("BROKER_PORT", "1883"))
CLIENT_ID      = os.getenv("CLIENT_ID", f"meshtastic-testbed-collector-{SITE}")

# The account is the site name; the password is looked up per site so one
# configuration.env can hold both without either collector seeing the other's.
MQTT_USERNAME = os.getenv("MQTT_USERNAME", SITE)
MQTT_PASSWORD = (os.getenv(f"MQTT_PASSWORD_{SITE.upper()}")
                 or os.getenv("MQTT_PASSWORD", ""))

TOPIC = os.getenv("COLLECTOR_TOPIC", f"meshtastic-testbed/{SITE}/pbx")

# How long an unresolved packet is held before being reported as finished.
# It MUST exceed the firmware's retransmission budget: expire too early and a
# frame still being retried is published as `unacked` while it is in fact still
# in progress. The observed retransmission delay is ~6 s per attempt.
EXPIRY_SEC = float(os.getenv("COLLECTOR_EXPIRY_SEC", "120"))

# Reading blocks for this long before yielding an idle tick. The tick is what
# drives expiry, so it also bounds how late a `dropped_before_tx` is reported.
READ_TIMEOUT_SEC    = float(os.getenv("COLLECTOR_READ_TIMEOUT_SEC", "1"))
RECONNECT_DELAY_SEC = float(os.getenv("COLLECTOR_RECONNECT_DELAY_SEC", "5"))
