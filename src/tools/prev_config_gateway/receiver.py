"""
receiver.py  –  LoRa gateway receiver
======================================
Loads node config, connects to the local Meshtastic gateway over serial,
and blocks while MeshReceiver logs all telemetry to per-node CSV files.

Usage:
    python receiver.py --port /dev/ttyACM0
"""

import json
import argparse

from mesh_receiver import MeshReceiver

MESH_CONFIG_PATH = "mesh_config.json"
DATA_DIR         = "data"


def load_known_nodes(config_path: str) -> dict:
    with open(config_path, "r") as f:
        data = json.load(f)
    return {
        cfg["id"]: f"node-{label}"
        for label, cfg in data["nodes_cfg"].items()
    }


def main():
    parser = argparse.ArgumentParser(
        description="LoRa TestBed – gateway receiver with CSV logging"
    )
    parser.add_argument(
        "--port", type=str, required=True,
        help="Serial port for the gateway node (e.g. /dev/ttyACM0)"
    )
    args = parser.parse_args()

    known_nodes = load_known_nodes(MESH_CONFIG_PATH)
    print(f"Loaded {len(known_nodes)} node(s): {known_nodes}\n")

    receiver = MeshReceiver(known_nodes=known_nodes, data_dir=DATA_DIR)
    receiver.connect(devPath=args.port)
    receiver.listen()


if __name__ == "__main__":
    main()
