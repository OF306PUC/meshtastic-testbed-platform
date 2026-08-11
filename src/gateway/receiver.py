import os
import sys
import json
import argparse
from pathlib import Path

# Make the `src/` package root importable when run directly (python src/gateway/receiver.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import mesh_config  # noqa: E402
from gateway import config as gateway_params  # noqa: E402
from gateway.mqtt_connector import MQTTConnector  # noqa: E402
from gateway.mesh_receiver import MeshReceiver  # noqa: E402


def load_mesh_config(config_path: str) -> dict:
    """Reads mesh_config.json, exiting with a clear message on any problem."""
    try:
        return mesh_config.load(config_path)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {os.path.abspath(config_path)}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {config_path}: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def load_known_nodes(data: dict) -> dict:
    """
    Builds the node-id to label map.

    Returns:
        dict: { "!0b64122b": "node-1", ... }
    """
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


def load_intervals(data: dict) -> dict:
    """
    Builds the {label: {kind: seconds}} cadence map the PDR tracker measures
    against — the same values the provisioning scripts write to the nodes.

    Returns:
        dict: { "node-1": {"device": 120, "environment": 120, "position": 600}, ... }
    """
    intervals = {}
    for label, cfg in data["nodes_cfg"].items():
        if "id" not in cfg:
            continue
        try:
            intervals[f"node-{label}"] = mesh_config.intervals_for(cfg)
        except ValueError as e:
            print(f"ERROR: node '{label}': {e}")
            sys.exit(1)
    return intervals


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

    data        = load_mesh_config(gateway_params.MESH_CONFIG_PATH)
    known_nodes = load_known_nodes(data)
    print(f"Loaded {len(known_nodes)} known nodes: {known_nodes}\n")

    mqtt = MQTTConnector(
        broker_address=gateway_params.BROKER_ADDRESS,
        port=gateway_params.BROKER_PORT,
        client_id=gateway_params.CLIENT_ID,
        username=gateway_params.MQTT_USERNAME,
        password=gateway_params.MQTT_PASSWORD,
    )
    mqtt.connect()
    mqtt.wait_until_connected()

    receiver = MeshReceiver(
        mqtt=mqtt,
        known_nodes=known_nodes,
        intervals=load_intervals(data),
        pdr_window_sec=mesh_config.pdr_window_sec(data),
        sweep_interval_sec=mesh_config.sweep_interval_sec(data),
    )
    receiver.connect(devPath=port)
    receiver.listen()  


if __name__ == "__main__":
    main()