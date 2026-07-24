import os
import sys
import json
import argparse
from pathlib import Path

# Make the `src/` package root importable when run directly (python src/gateway/receiver.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import config as gateway_params  # noqa: E402
from gateway.mqtt_connector import MQTTConnector  # noqa: E402
from gateway.mesh_receiver import MeshReceiver  # noqa: E402


def load_known_nodes(config_path: str) -> dict:
    """
    Loads node IDs and labels from mesh_config.json.

    Returns:
        dict: { "!0b64122b": "node-1", ... }
    """
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {os.path.abspath(config_path)}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {config_path}: {e}")
        sys.exit(1)

    if "nodes_cfg" not in data:
        print(f"ERROR: 'nodes_cfg' key missing in {config_path}")
        sys.exit(1)

    known_nodes = {}
    for label, cfg in data["nodes_cfg"].items():
        if "id" not in cfg:
            print(f"WARNING: Node '{label}' missing 'id' field, skipping")
            continue
        known_nodes[cfg["id"]] = f"node-{label}"

    if not known_nodes:
        print("ERROR: No valid nodes found in configuration")
        sys.exit(1)

    return known_nodes


def main():
    parser = argparse.ArgumentParser(description="Run the Meshtastic MQTT gateway receiver")
    parser.add_argument(
        "--port",
        type=str,
        default=os.getenv("GATEWAY_SERIAL_PORT"),
        help="Serial port for Meshtastic gateway (e.g., /dev/ttyACM0). "
             "Falls back to the GATEWAY_SERIAL_PORT env var (used by the container).",
    )

    args = parser.parse_args()
    port = args.port
    if not port:
        parser.error("a serial port is required: pass --port or set GATEWAY_SERIAL_PORT")

    known_nodes = load_known_nodes(gateway_params.MESH_CONFIG_PATH)
    print(f"Loaded {len(known_nodes)} known nodes: {known_nodes}\n")

    mqtt = MQTTConnector(
        broker_address=gateway_params.BROKER_ADDRESS, port=gateway_params.BROKER_PORT, client_id=gateway_params.CLIENT_ID
    )
    mqtt.connect()
    mqtt.wait_until_connected()

    receiver = MeshReceiver(mqtt=mqtt, known_nodes=known_nodes)
    receiver.connect(devPath=port)
    receiver.listen()  # blocks until Ctrl+C


if __name__ == "__main__":
    main()