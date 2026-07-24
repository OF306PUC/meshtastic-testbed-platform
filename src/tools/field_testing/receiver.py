"""receiver.py  –  Field-testing gateway receiver (CSV logging)
================================================================
Connects to a portable Meshtastic gateway over serial and logs every telemetry
/ position packet from known nodes to per-node CSV files (with RSSI/SNR), plus
the gateway's own GPS position for walk-tests. Offline and self-contained — it
does NOT touch MQTT/InfluxDB (that is the production pipeline in src/gateway/).

Use it during solar-node installation to confirm nodes are alive and to measure
link quality / coverage. Analyse the CSVs afterwards with plot_data.py.

Usage:
    python src/tools/field_testing/receiver.py --port /dev/ttyACM0
    python src/tools/field_testing/receiver.py --port /dev/ttyACM0 --data-dir field-testing-data/run1
"""
import sys
import json
import argparse
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))            # for sibling module import (mesh_receiver)

from mesh_receiver import MeshReceiver     # noqa: E402

# mesh_config.json lives at the repo root (this file is at src/tools/field_testing/),
# so resolve it relative to the file rather than the current working directory.
_ROOT = _HERE.parents[2]
MESH_CONFIG_PATH = _ROOT / "mesh_config.json"
DEFAULT_DATA_DIR = _ROOT / "field-testing-data"


def load_known_nodes(config_path: Path) -> dict:
    with open(config_path, "r") as f:
        data = json.load(f)
    return {
        cfg["id"]: f"node-{label}"
        for label, cfg in data["nodes_cfg"].items()
    }


def main():
    parser = argparse.ArgumentParser(
        description="Field-testing gateway receiver with CSV logging"
    )
    parser.add_argument(
        "--port", type=str, required=True,
        help="Serial port for the gateway node (e.g. /dev/ttyACM0)"
    )
    parser.add_argument(
        "--data-dir", type=str, default=str(DEFAULT_DATA_DIR),
        help="Directory for CSV output (default: <repo>/field-testing-data)"
    )
    args = parser.parse_args()

    known_nodes = load_known_nodes(MESH_CONFIG_PATH)
    print(f"Loaded {len(known_nodes)} node(s): {known_nodes}")
    print(f"Writing CSVs to: {args.data_dir}\n")

    receiver = MeshReceiver(known_nodes=known_nodes, data_dir=args.data_dir)
    receiver.connect(devPath=args.port)
    receiver.listen()


if __name__ == "__main__":
    main()
