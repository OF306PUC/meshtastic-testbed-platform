"""
Regression tests for MeshReceiver._on_receive data-contract.

The web-app consumer depends on exact payload field names.  These tests lock
down that contract and guard the "omit-not-zero" fix for absent metrics.

Run:
    .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import time
import struct
import unittest
from pathlib import Path

# Put src/ on the path so package-absolute imports inside src/ work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.mesh_receiver import MeshReceiver  # noqa: E402
from gateway.mqtt_connector import MQTTConnector  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_NODE_ID  = "!0b64122b"
_KNOWN_NODE_LABEL = "node-1"
_GATEWAY_ID     = "!aabbccdd"
_GATEWAY_NUM    = 0xAABBCCDD

# A packet id counter so each synthetic packet gets a unique id.
_pkt_id_counter = 0


def _next_pkt_id():
    global _pkt_id_counter
    _pkt_id_counter += 1
    return _pkt_id_counter


def _make_receiver(intervals=None):
    """Return a freshly constructed (MeshReceiver, FakeMQTT) pair."""
    fake = FakeMQTT()
    receiver = MeshReceiver(
        mqtt=fake,
        known_nodes={_KNOWN_NODE_ID: _KNOWN_NODE_LABEL},
        intervals=intervals,
    )
    receiver.my_id  = _GATEWAY_ID
    receiver.my_num = _GATEWAY_NUM
    return receiver, fake


def _private_packet(payload: bytes, pkt_id=None):
    """Synthetic PRIVATE_APP packet carrying a raw proxy frame."""
    return {
        "fromId":   _KNOWN_NODE_ID,
        "from":     0x0B64122B,
        "id":       pkt_id if pkt_id is not None else _next_pkt_id(),
        "rxRssi":   -95,
        "rxSnr":    3.5,
        "hopLimit": 2,
        "hopStart": 3,
        "decoded": {
            "portnum": "PRIVATE_APP",
            "payload": payload,
        },
    }


def _proxy_frame(fw_ver=1, src=0x6C743130, dst=0xBB8106C4, content=b"hello"):
    """Builds a little-endian [fw_ver][src_id][dst_id][content] frame."""
    return struct.pack("<BII", fw_ver, src, dst) + content


def _env_packet(temp=22.5, humidity=55.0, include_humidity=True, pkt_id=None):
    """Synthetic TELEMETRY_APP packet with environmentMetrics."""
    metrics = {"temperature": temp}
    if include_humidity:
        metrics["relativeHumidity"] = humidity
    return {
        "fromId":   _KNOWN_NODE_ID,
        "from":     0x0B64122B,
        "id":       pkt_id if pkt_id is not None else _next_pkt_id(),
        "rxRssi":   -85,
        "rxSnr":    7.5,
        "hopLimit": 3,
        "hopStart": 5,
        "decoded": {
            "portnum": "TELEMETRY_APP",
            "telemetry": {
                "time": 1700000000,
                "environmentMetrics": metrics,
            },
        },
    }


def _device_packet(pkt_id=None):
    """Synthetic TELEMETRY_APP packet with deviceMetrics."""
    return {
        "fromId":   _KNOWN_NODE_ID,
        "from":     0x0B64122B,
        "id":       pkt_id if pkt_id is not None else _next_pkt_id(),
        "rxRssi":   -90,
        "rxSnr":    5.0,
        "hopLimit": 2,
        "hopStart": 4,
        "decoded": {
            "portnum": "TELEMETRY_APP",
            "telemetry": {
                "time": 1700000001,
                "deviceMetrics": {
                    "batteryLevel":       85,
                    "voltage":            3.95,
                    "channelUtilization": 0.12,
                    "airUtilTx":          0.05,
                    "uptimeSeconds":      3600,
                },
            },
        },
    }


def _position_packet(include_altitude=True, pkt_id=None):
    """Synthetic POSITION_APP packet."""
    pos = {
        "latitude":  -33.4489,
        "longitude": -70.6693,
        "time":      1700000002,
    }
    if include_altitude:
        pos["altitude"] = 567
    return {
        "fromId":   _KNOWN_NODE_ID,
        "from":     0x0B64122B,
        "id":       pkt_id if pkt_id is not None else _next_pkt_id(),
        "rxRssi":   -80,
        "rxSnr":    9.0,
        "hopLimit": 1,
        "hopStart": 3,
        "decoded": {
            "portnum": "POSITION_APP",
            "position": pos,
        },
    }


# ---------------------------------------------------------------------------
# FakeMQTT — captures publish_* calls without needing a broker
# ---------------------------------------------------------------------------

class FakeMQTT:
    """Drop-in stand-in for MQTTConnector that records calls instead of sending."""

    def __init__(self):
        self.env_calls      = []   # list of (label, payload_dict)
        self.device_calls   = []
        self.position_calls = []
        self.message_calls  = []
        self.pdr_calls      = []

    def publish_env(self, label: str, payload: dict):
        self.env_calls.append((label, payload))

    def publish_device(self, label: str, payload: dict):
        self.device_calls.append((label, payload))

    def publish_position(self, label: str, payload: dict):
        self.position_calls.append((label, payload))

    def publish_message(self, label: str, payload: dict):
        self.message_calls.append((label, payload))

    def publish_pdr(self, label: str, payload: dict):
        self.pdr_calls.append((label, payload))

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Meta-field names that must appear on every payload
# ---------------------------------------------------------------------------

META_KEYS = {"node_id", "node_label", "rssi", "snr", "hop", "device_ts", "received_at"}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestEnvTelemetry(unittest.TestCase):
    """Tests 1 & 2 — environment payload contract and omit-not-zero."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    # -- Test 1 ---------------------------------------------------------------

    def test_env_full_payload_has_all_required_keys(self):
        """Env packet with temp+humidity produces payload with all meta + both metrics."""
        self.receiver._on_receive(_env_packet(), interface=None)

        self.assertEqual(len(self.fake.env_calls), 1)
        label, payload = self.fake.env_calls[0]

        self.assertEqual(label, _KNOWN_NODE_LABEL)
        for key in META_KEYS:
            self.assertIn(key, payload, f"meta key '{key}' missing from env payload")
        self.assertIn("temperature", payload)
        self.assertIn("humidity",    payload)

    def test_env_full_payload_correct_values(self):
        """Env payload contains the exact values from the packet."""
        self.receiver._on_receive(_env_packet(temp=22.5, humidity=55.0), interface=None)

        _, payload = self.fake.env_calls[0]
        self.assertAlmostEqual(payload["temperature"], 22.5)
        self.assertAlmostEqual(payload["humidity"],    55.0)
        self.assertEqual(payload["node_id"],    _KNOWN_NODE_ID)
        self.assertEqual(payload["node_label"], _KNOWN_NODE_LABEL)
        self.assertEqual(payload["rssi"],       -85)
        self.assertAlmostEqual(payload["snr"],  7.5)
        # hop = hopStart - hopLimit = 5 - 3 = 2
        self.assertEqual(payload["hop"], 2)

    def test_env_topic_format(self):
        """publish_env is called with the correct MQTT topic format."""
        # FakeMQTT receives the label, not the topic.  We verify the topic by
        # checking that MQTTConnector.TOPIC_ENV.format(node_label=label) is correct.
        self.receiver._on_receive(_env_packet(), interface=None)
        label, _ = self.fake.env_calls[0]
        expected_topic = MQTTConnector.TOPIC_ENV.format(node_label=label)
        self.assertEqual(expected_topic, f"meshtastic-testbed/{_KNOWN_NODE_LABEL}/environment")

    # -- Test 2 (omit-not-zero contract) --------------------------------------

    def test_missing_humidity_omitted_not_zero(self):
        """Env packet without relativeHumidity must NOT set humidity=0.0 in payload."""
        self.receiver._on_receive(
            _env_packet(include_humidity=False), interface=None
        )

        self.assertEqual(len(self.fake.env_calls), 1)
        _, payload = self.fake.env_calls[0]
        self.assertIn("temperature", payload)
        self.assertNotIn("humidity", payload,
            "REGRESSION: absent relativeHumidity must be omitted, not set to 0.0")

    def test_missing_temperature_omitted(self):
        """If temperature key absent entirely, temperature not in payload."""
        pkt = _env_packet()
        del pkt["decoded"]["telemetry"]["environmentMetrics"]["temperature"]
        self.receiver._on_receive(pkt, interface=None)

        # environmentMetrics is empty-ish — handler returns early when env is falsy
        # (empty dict after deletion of the only key). Either zero calls or one call
        # without temperature — both are acceptable; what's forbidden is temperature=0.0.
        for _, payload in self.fake.env_calls:
            self.assertNotEqual(payload.get("temperature"), 0.0,
                "REGRESSION: absent temperature must never appear as 0.0")


class TestDeviceTelemetry(unittest.TestCase):
    """Test 3 — device payload contract."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    def test_device_payload_has_all_required_keys(self):
        """Device packet produces payload with all meta keys + device metrics."""
        self.receiver._on_receive(_device_packet(), interface=None)

        self.assertEqual(len(self.fake.device_calls), 1)
        label, payload = self.fake.device_calls[0]

        self.assertEqual(label, _KNOWN_NODE_LABEL)
        for key in META_KEYS:
            self.assertIn(key, payload, f"meta key '{key}' missing from device payload")
        for key in ("battery_level", "voltage", "channel_util", "air_util_tx", "uptime_seconds"):
            self.assertIn(key, payload, f"device metric '{key}' missing from payload")

    def test_device_payload_correct_values(self):
        self.receiver._on_receive(_device_packet(), interface=None)
        _, payload = self.fake.device_calls[0]
        self.assertEqual(payload["battery_level"], 85)
        self.assertAlmostEqual(payload["voltage"],       3.95)
        self.assertAlmostEqual(payload["channel_util"],  0.12)
        self.assertAlmostEqual(payload["air_util_tx"],   0.05)
        self.assertEqual(payload["uptime_seconds"],      3600)
        # hop = hopStart - hopLimit = 4 - 2 = 2
        self.assertEqual(payload["hop"], 2)

    def test_device_topic_format(self):
        self.receiver._on_receive(_device_packet(), interface=None)
        label, _ = self.fake.device_calls[0]
        expected_topic = MQTTConnector.TOPIC_DEVICE.format(node_label=label)
        self.assertEqual(expected_topic, f"meshtastic-testbed/{_KNOWN_NODE_LABEL}/device")

    def test_missing_battery_level_omitted_not_zero(self):
        """Device packet without batteryLevel must NOT have battery_level key."""
        pkt = _device_packet()
        del pkt["decoded"]["telemetry"]["deviceMetrics"]["batteryLevel"]
        self.receiver._on_receive(pkt, interface=None)

        self.assertEqual(len(self.fake.device_calls), 1)
        _, payload = self.fake.device_calls[0]
        self.assertNotIn("battery_level", payload,
            "REGRESSION: absent batteryLevel must be omitted, not set to 0.0")


class TestPositionTelemetry(unittest.TestCase):
    """Test 4 — position payload contract and omit-not-zero for altitude."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    def test_position_payload_has_all_required_keys(self):
        """Position packet produces payload with all meta + lat/lon/alt."""
        self.receiver._on_receive(_position_packet(), interface=None)

        self.assertEqual(len(self.fake.position_calls), 1)
        label, payload = self.fake.position_calls[0]

        self.assertEqual(label, _KNOWN_NODE_LABEL)
        for key in META_KEYS:
            self.assertIn(key, payload, f"meta key '{key}' missing from position payload")
        for key in ("latitude", "longitude", "altitude"):
            self.assertIn(key, payload, f"position field '{key}' missing from payload")

    def test_position_payload_correct_values(self):
        self.receiver._on_receive(_position_packet(), interface=None)
        _, payload = self.fake.position_calls[0]
        self.assertAlmostEqual(payload["latitude"],  -33.4489)
        self.assertAlmostEqual(payload["longitude"], -70.6693)
        self.assertEqual(payload["altitude"], 567)
        # hop = hopStart - hopLimit = 3 - 1 = 2
        self.assertEqual(payload["hop"], 2)

    def test_position_topic_format(self):
        self.receiver._on_receive(_position_packet(), interface=None)
        label, _ = self.fake.position_calls[0]
        expected_topic = MQTTConnector.TOPIC_POSITION.format(node_label=label)
        self.assertEqual(expected_topic, f"meshtastic-testbed/{_KNOWN_NODE_LABEL}/position")

    def test_missing_altitude_omitted_not_zero(self):
        """Position packet without altitude must NOT include an altitude key."""
        self.receiver._on_receive(_position_packet(include_altitude=False), interface=None)

        self.assertEqual(len(self.fake.position_calls), 1)
        _, payload = self.fake.position_calls[0]
        self.assertIn("latitude",  payload)
        self.assertIn("longitude", payload)
        self.assertNotIn("altitude", payload,
            "REGRESSION: absent altitude must be omitted, not set to 0.0")


class TestFilteringGuards(unittest.TestCase):
    """Tests 5, 6, 7 — unknown node, loopback, and dedup guards."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    # -- Test 5 ---------------------------------------------------------------

    def test_unknown_node_id_not_published(self):
        """Packet from a node not in known_nodes must produce no publish calls."""
        pkt = _env_packet()
        pkt["fromId"] = "!deadbeef"
        pkt["from"]   = 0xDEADBEEF
        self.receiver._on_receive(pkt, interface=None)

        self.assertEqual(len(self.fake.env_calls), 0)
        self.assertEqual(len(self.fake.device_calls), 0)
        self.assertEqual(len(self.fake.position_calls), 0)

    # -- Test 6 ---------------------------------------------------------------

    def test_loopback_own_id_not_published(self):
        """Packet whose fromId equals the gateway's own my_id is silently dropped."""
        pkt = _env_packet()
        pkt["fromId"] = _GATEWAY_ID
        pkt["from"]   = _GATEWAY_NUM
        # Add to known_nodes so it would pass the node-unknown check
        self.receiver.known_nodes[_GATEWAY_ID] = "gateway"
        self.receiver._on_receive(pkt, interface=None)

        self.assertEqual(len(self.fake.env_calls), 0)

    def test_loopback_own_num_not_published(self):
        """Packet whose 'from' num equals gateway's my_num is silently dropped."""
        pkt = _env_packet()
        # different fromId but same numeric address
        pkt["fromId"] = "!aabbccde"
        pkt["from"]   = _GATEWAY_NUM
        self.receiver.known_nodes["!aabbccde"] = "spoof"
        self.receiver._on_receive(pkt, interface=None)

        self.assertEqual(len(self.fake.env_calls), 0)

    # -- Test 7 ---------------------------------------------------------------

    def test_duplicate_packet_id_published_only_once(self):
        """Sending identical packet id twice must result in exactly one publish."""
        shared_id = _next_pkt_id()
        pkt1 = _env_packet(pkt_id=shared_id)
        pkt2 = _env_packet(pkt_id=shared_id)

        self.receiver._on_receive(pkt1, interface=None)
        self.receiver._on_receive(pkt2, interface=None)

        self.assertEqual(len(self.fake.env_calls), 1,
            "Duplicate packet id must be deduplicated")


class TestHopCalculation(unittest.TestCase):
    """Test 8 — hop computation and None-safety for absent hopStart."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    def test_hop_computed_when_both_fields_present(self):
        """hop = hopStart - hopLimit when both are present."""
        pkt = _env_packet()
        pkt["hopStart"] = 7
        pkt["hopLimit"] = 4
        self.receiver._on_receive(pkt, interface=None)

        _, payload = self.fake.env_calls[0]
        self.assertEqual(payload["hop"], 3)

    def test_hop_is_none_when_hop_start_absent(self):
        """hop key is present but None when hopStart is absent (was: crash guard)."""
        pkt = _env_packet()
        pkt["hopStart"] = None     # explicitly absent / None
        self.receiver._on_receive(pkt, interface=None)

        # Must not raise; must produce payload with hop=None
        self.assertEqual(len(self.fake.env_calls), 1)
        _, payload = self.fake.env_calls[0]
        self.assertIn("hop", payload)
        self.assertIsNone(payload["hop"])

    def test_hop_is_none_when_hop_limit_absent(self):
        """hop key is None when hopLimit is absent."""
        pkt = _env_packet()
        pkt["hopLimit"] = None
        self.receiver._on_receive(pkt, interface=None)

        self.assertEqual(len(self.fake.env_calls), 1)
        _, payload = self.fake.env_calls[0]
        self.assertIsNone(payload["hop"])

    def test_hop_is_none_when_both_absent(self):
        """hop is None when both hopStart and hopLimit are absent."""
        pkt = _env_packet()
        del pkt["hopStart"]
        del pkt["hopLimit"]
        self.receiver._on_receive(pkt, interface=None)

        self.assertEqual(len(self.fake.env_calls), 1)
        _, payload = self.fake.env_calls[0]
        self.assertIsNone(payload["hop"])

    def test_no_crash_on_none_hop_start(self):
        """_on_receive must not raise when hopStart is None."""
        pkt = _env_packet()
        pkt["hopStart"] = None
        try:
            self.receiver._on_receive(pkt, interface=None)
        except TypeError as exc:
            self.fail(f"_on_receive raised TypeError with hopStart=None: {exc}")


class TestReceivedAtTimestamp(unittest.TestCase):
    """Verify received_at is a recent Unix timestamp (sanity check)."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    def test_received_at_is_recent_unix_timestamp(self):
        before = int(time.time())
        self.receiver._on_receive(_env_packet(), interface=None)
        after  = int(time.time())

        _, payload = self.fake.env_calls[0]
        self.assertGreaterEqual(payload["received_at"], before)
        self.assertLessEqual(payload["received_at"],    after)

    def test_device_ts_from_packet_time(self):
        """device_ts must come from the packet's telemetry.time, not wall clock."""
        self.receiver._on_receive(_env_packet(), interface=None)
        _, payload = self.fake.env_calls[0]
        self.assertEqual(payload["device_ts"], 1700000000)


class TestMalformedPackets(unittest.TestCase):
    """Extra robustness — malformed inputs must not raise."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    def test_none_packet_no_crash(self):
        try:
            self.receiver._on_receive(None, interface=None)
        except Exception as exc:
            self.fail(f"None packet raised {exc}")

    def test_empty_dict_no_crash(self):
        try:
            self.receiver._on_receive({}, interface=None)
        except Exception as exc:
            self.fail(f"Empty dict raised {exc}")

    def test_unknown_portnum_no_publish(self):
        pkt = _env_packet()
        pkt["decoded"]["portnum"] = "NODEINFO_APP"
        self.receiver._on_receive(pkt, interface=None)
        self.assertEqual(len(self.fake.env_calls), 0)
        self.assertEqual(len(self.fake.device_calls), 0)

    def test_missing_decoded_key_no_crash(self):
        pkt = {"fromId": _KNOWN_NODE_ID, "from": 0x0B64122B, "id": _next_pkt_id()}
        try:
            self.receiver._on_receive(pkt, interface=None)
        except Exception as exc:
            self.fail(f"Missing 'decoded' raised {exc}")


class TestProxyMessageFrame(unittest.TestCase):
    """PRIVATE_APP frames from the BLE proxy: [fw_ver][src_id][dst_id][content]."""

    def setUp(self):
        self.receiver, self.fake = _make_receiver()

    def test_valid_frame_publishes_src_and_dst(self):
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)

        self.assertEqual(len(self.fake.message_calls), 1)
        label, payload = self.fake.message_calls[0]

        self.assertEqual(label, _KNOWN_NODE_LABEL)
        self.assertEqual(payload["src_id"], "!6c743130")
        self.assertEqual(payload["dst_id"], "!bb8106c4")
        self.assertEqual(payload["fw_ver"], 1)
        self.assertFalse(payload["malformed"])
        self.assertEqual(payload["content_len"], len(b"hello"))
        self.assertEqual(payload["payload_len"], 9 + len(b"hello"))

    def test_node_id_is_the_relay_not_the_originator(self):
        """
        node_id is the mesh node the frame was heard from; src_id is the
        app-level originator. Conflating them would misattribute every relayed
        message to the relay.
        """
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)
        _, payload = self.fake.message_calls[0]
        self.assertEqual(payload["node_id"], _KNOWN_NODE_ID)
        self.assertNotEqual(payload["src_id"], payload["node_id"])

    def test_link_quality_is_carried(self):
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)
        _, payload = self.fake.message_calls[0]
        self.assertEqual(payload["rssi"], -95)
        self.assertAlmostEqual(payload["snr"], 3.5)
        self.assertEqual(payload["hop"], 1)      # hopStart 3 - hopLimit 2

    def test_empty_content_is_valid(self):
        """A header-only frame is well-formed: 9 bytes, zero-length content."""
        self.receiver._on_receive(
            _private_packet(_proxy_frame(content=b"")), interface=None)

        _, payload = self.fake.message_calls[0]
        self.assertFalse(payload["malformed"])
        self.assertEqual(payload["content_len"], 0)

    def test_truncated_frame_reported_as_malformed(self):
        """
        A payload shorter than the 9-byte header must be reported, not guessed
        at — an unreported malformed frame is an invisible loss.
        """
        self.receiver._on_receive(
            _private_packet(b"\x01\x02\x03"), interface=None)

        self.assertEqual(len(self.fake.message_calls), 1)
        _, payload = self.fake.message_calls[0]
        self.assertTrue(payload["malformed"])
        self.assertEqual(payload["payload_len"], 3)

    def test_non_bytes_payload_reported_as_malformed(self):
        """Defends against a firmware/library change handing us a str."""
        self.receiver._on_receive(
            _private_packet("not-bytes"), interface=None)

        _, payload = self.fake.message_calls[0]
        self.assertTrue(payload["malformed"])

    def test_seq_is_none_until_firmware_emits_it(self):
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)
        _, payload = self.fake.message_calls[0]
        self.assertIsNone(payload["seq"])

    def test_seq_parsed_when_frame_has_seq_enabled(self):
        """Forward-compat: flipping FRAME_HAS_SEQ picks up the 2-byte counter."""
        frame = struct.pack("<BII", 1, 0x6C743130, 0xBB8106C4) \
            + struct.pack("<H", 4242) + b"hi"
        original = MeshReceiver.FRAME_HAS_SEQ
        MeshReceiver.FRAME_HAS_SEQ = True
        try:
            self.receiver._on_receive(_private_packet(frame), interface=None)
        finally:
            MeshReceiver.FRAME_HAS_SEQ = original

        _, payload = self.fake.message_calls[0]
        self.assertEqual(payload["seq"], 4242)
        self.assertEqual(payload["content_len"], len(b"hi"))

    def test_content_not_published_by_default(self):
        """
        Message bodies are real phone traffic and the MQTT stream is persisted,
        so content is opt-in via capture_content.
        """
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)
        _, payload = self.fake.message_calls[0]
        self.assertNotIn("content_hex", payload)

    def test_content_published_when_capture_enabled(self):
        self.receiver.capture_content = True
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)
        _, payload = self.fake.message_calls[0]
        self.assertEqual(payload["content_hex"], b"hello".hex())

    def test_messages_carry_no_pdr_fields(self):
        """Phone messages have no cadence, so no ratio can be inferred."""
        self.receiver._on_receive(_private_packet(_proxy_frame()), interface=None)
        _, payload = self.fake.message_calls[0]
        for key in ("pdr", "pdr_window", "missed_est"):
            self.assertNotIn(key, payload)


class TestPdrIntegration(unittest.TestCase):
    """PDR fields ride along inside the existing telemetry payloads."""

    def test_pdr_fields_present_on_device_payload(self):
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120, "environment": 120,
                                           "position": 600}})
        receiver._on_receive(_device_packet(), interface=None)

        _, payload = fake.device_calls[0]
        for key in ("pdr", "pdr_window", "pdr_window_slots", "rx_count",
                    "missed_est", "early_count", "cadence_violated"):
            self.assertIn(key, payload, f"PDR field '{key}' missing")
        self.assertIsNone(payload["pdr"], "first packet yields no ratio")
        self.assertEqual(payload["rx_count"], 1)

    def test_pdr_absent_when_node_declares_no_cadence(self):
        """
        A node with no cadence for a flow gets no PDR rather than a ratio
        measured against an interval it was never configured with.
        """
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120}})   # no environment
        receiver._on_receive(_env_packet(), interface=None)

        _, payload = fake.env_calls[0]
        self.assertNotIn("pdr", payload)
        self.assertIn("temperature", payload, "the telemetry itself still publishes")

    def test_device_and_env_are_separate_flows(self):
        """
        Both ride on TELEMETRY_APP but are distinct broadcasts, so one must not
        consume the other's slot.
        """
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120, "environment": 120}})
        receiver._on_receive(_device_packet(), interface=None)
        receiver._on_receive(_env_packet(), interface=None)

        self.assertEqual(fake.device_calls[0][1]["rx_count"], 1)
        self.assertEqual(fake.env_calls[0][1]["rx_count"], 1)

    def test_reboot_detected_from_falling_uptime(self):
        """A decreasing uptimeSeconds re-anchors the node's flows."""
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120}})

        pkt1 = _device_packet()
        pkt1["decoded"]["telemetry"]["deviceMetrics"]["uptimeSeconds"] = 7200
        receiver._on_receive(pkt1, interface=None)

        pkt2 = _device_packet()
        pkt2["decoded"]["telemetry"]["deviceMetrics"]["uptimeSeconds"] = 30
        receiver._on_receive(pkt2, interface=None)

        # The second packet lands microseconds after the first, so without the
        # reboot re-anchor it would be classified off-cadence (early).
        _, payload = fake.device_calls[1]
        self.assertEqual(payload["early_count"], 0,
            "a reboot must restart the cadence grid, not flag an early packet")

    def test_sweep_publishes_losses_for_silent_flows(self):
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120}})
        receiver._on_receive(_device_packet(), interface=None)

        # Sweep 3 intervals after the reception the tracker recorded.
        receiver._run_sweep(receiver._pdr._flows[(_KNOWN_NODE_LABEL, "device")]["last"]
                            + 3.1 * 120)

        self.assertEqual(len(fake.pdr_calls), 1)
        label, payload = fake.pdr_calls[0]
        self.assertEqual(label, _KNOWN_NODE_LABEL)
        self.assertEqual(payload["flow"], "device")
        self.assertEqual(payload["source"], "sweep")
        self.assertEqual(payload["missed_est"], 2)
        self.assertIn("received_at", payload)

    def test_sweep_is_silent_when_nothing_is_overdue(self):
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120}})
        receiver._on_receive(_device_packet(), interface=None)
        receiver._run_sweep(receiver._pdr._flows[(_KNOWN_NODE_LABEL, "device")]["last"])

        self.assertEqual(len(fake.pdr_calls), 0)

    def test_duplicate_packet_does_not_inflate_pdr(self):
        """
        Dedup is load-bearing here: a rebroadcast counted as a second reception
        would add a slot the sender never transmitted.
        """
        receiver, fake = _make_receiver(
            intervals={_KNOWN_NODE_LABEL: {"device": 120}})
        shared = _next_pkt_id()
        receiver._on_receive(_device_packet(pkt_id=shared), interface=None)
        receiver._on_receive(_device_packet(pkt_id=shared), interface=None)

        self.assertEqual(len(fake.device_calls), 1)
        self.assertEqual(fake.device_calls[0][1]["rx_count"], 1)


if __name__ == "__main__":
    unittest.main()
