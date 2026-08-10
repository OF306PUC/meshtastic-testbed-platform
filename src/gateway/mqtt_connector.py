import time
import json
import threading
import paho.mqtt.client as mqtt


class MQTTConnector:
    """
    Manages connection to the MQTT broker and message publishing.
    Publish-only — no subscriptions or incoming message handling.
    Handles automatic reconnection on disconnect.

    Attributes:
        broker_address (str): The address of the MQTT broker.
        port (int): The port to connect to the MQTT broker.
        client_id (str): The client ID for the MQTT connection.
        client (mqtt.Client): The Paho MQTT client instance.
    """

    TOPIC_POSITION = "meshtastic-testbed/{node_label}/position"
    TOPIC_DEVICE = "meshtastic-testbed/{node_label}/device"
    TOPIC_ENV    = "meshtastic-testbed/{node_label}/environment"
    TOPIC_MESSAGE = "meshtastic-testbed/{node_label}/message"
    TOPIC_PDR    = "meshtastic-testbed/{node_label}/pdr"

    def __init__(self, broker_address: str, port: int = 1883, client_id: str = "",
                 username: str = "", password: str = ""):
        """
        Initialize the MQTTConnector.

        Args:
            broker_address (str): The address of the MQTT broker.
            port (int, optional): The port to connect to. Defaults to 1883.
            client_id (str, optional): The client ID for the connection. Defaults to "".
            username (str, optional): Broker username. Empty means connect
                anonymously, which the broker only accepts if it is configured
                with `allow_anonymous true`.
            password (str, optional): Broker password.
        """
        self.broker_address  = broker_address
        self.port            = port
        self.client_id       = client_id
        self.client          = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
        if username:
            self.client.username_pw_set(username, password)
        self.client.reconnect_delay_set(min_delay=5, max_delay=60)
        self._connected_event = threading.Event()
        self.client.on_connect    = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_connect(self, client, userdata, flags, rc, properties):
        """
        Called when the client receives a CONNACK response from the broker.

        Args:
            client: The client instance for this callback.
            userdata: Private user data.
            flags: Response flags sent by the broker.
            rc: The connection result code.
            properties: MQTT v5.0 properties.
        """
        if rc == 0:
            print("[MQTT] Connected to broker!")
            self._connected_event.set()
        else:
            print(f"[MQTT] Failed to connect, return code {rc}")

    def on_disconnect(self, client, userdata, flags, rc, properties):
        """
        Called when the client disconnects from the broker.
        Paho's loop_start() handles reconnection automatically via its
        internal loop_forever(); reconnect_delay_set controls the timing.
        """
        self._connected_event.clear()
        if rc != 0:
            print(f"[MQTT] Disconnected unexpectedly (rc={rc}). Will reconnect automatically...")

    def wait_until_connected(self, timeout: float = 10.0):
        """Block until the broker connection is established."""
        if not self._connected_event.wait(timeout):
            raise RuntimeError(f"[MQTT] Could not connect to broker at {self.broker_address}:{self.port} within {timeout}s")

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self):
        """Connect to the MQTT broker and start background network loop."""
        self.client.connect(self.broker_address, self.port, keepalive=60)
        self.client.loop_start()

    def close(self):
        """Stop the network loop and disconnect from the broker."""
        self.client.loop_stop()
        self.client.disconnect()
        print("[MQTT] Disconnected from broker.")

    # ── Publishing ────────────────────────────────────────────────────────────

    def publish(self, topic: str, message: str):
        """
        Publish a raw string message to a topic.

        Args:
            topic (str): The topic to publish to.
            message (str): The message payload to publish.
        """
        rc, _ = self.client.publish(topic, message, qos=1)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[MQTT] ERROR publishing to {topic}: rc={rc}")
            return
        print(f"[MQTT] → {topic}: {message}")

    def publish_position(self, node_label: str, payload: dict):
        """
        Publish position data for a node.

        Args:
            node_label (str): Human-readable node label (e.g. "node-1").
            payload (dict): Position data payload.
        """
        topic = self.TOPIC_POSITION.format(node_label=node_label)
        self.publish(topic, json.dumps(payload))

    def publish_device(self, node_label: str, payload: dict):
        """
        Publish device telemetry for a node.

        Args:
            node_label (str): Human-readable node label (e.g. "node-1").
            payload (dict): Device telemetry data.
        """
        topic = self.TOPIC_DEVICE.format(node_label=node_label)
        self.publish(topic, json.dumps(payload))

    def publish_env(self, node_label: str, payload: dict):
        """
        Publish environment telemetry for a node.

        Args:
            node_label (str): Human-readable node label (e.g. "node-1").
            payload (dict): Environment telemetry data.
        """
        topic = self.TOPIC_ENV.format(node_label=node_label)
        self.publish(topic, json.dumps(payload))

    def publish_message(self, node_label: str, payload: dict):
        """
        Publish PBX message metadata (src/dst ids, link quality, frame sizes).

        Args:
            node_label (str): Label of the mesh node the frame was heard FROM —
                which is the relay, not necessarily the originator. The
                app-level originator is the payload's "src_id".
            payload (dict): Message metadata.
        """
        topic = self.TOPIC_MESSAGE.format(node_label=node_label)
        self.publish(topic, json.dumps(payload))

    def publish_pdr(self, node_label: str, payload: dict):
        """
        Publish a packet-delivery-ratio update inferred while a flow was silent.

        Receptions carry their PDR fields inside the position/device/environment
        payloads; this topic exists for the losses that have no packet behind
        them, which are the only way a fully dead node becomes visible.

        Args:
            node_label (str): Human-readable node label (e.g. "node-1").
            payload (dict): PDR snapshot including "flow" and "source".
        """
        topic = self.TOPIC_PDR.format(node_label=node_label)
        self.publish(topic, json.dumps(payload))