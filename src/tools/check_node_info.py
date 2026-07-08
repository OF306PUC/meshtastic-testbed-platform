import argparse
import meshtastic
import meshtastic.serial_interface

def main(): 
    
    parser = argparse.ArgumentParser(description="Check node info using meshtastic CLI")
    parser.add_argument("--port", type=str, required=True, help="Serial port to connect to (e.g., '/dev/ttyACM0')")
    
    args = parser.parse_args()
    port = str(args.port)

    iface = meshtastic.serial_interface.SerialInterface(port)
    node_info = iface.getMyNodeInfo()

    print("Node info:")
    node_num = node_info["num"]
    node_id = node_info["user"]["id"]
    longName = node_info["user"]["longName"]
    hwModel = node_info["user"]["hwModel"]
    print(f"Node Number: {node_num}")
    print(f"Node ID: {node_id}")
    print(f"Long Name: {longName}")
    print(f"Hardware Model: {hwModel}")


if __name__ == "__main__":
    main()