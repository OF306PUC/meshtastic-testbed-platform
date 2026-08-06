"""
Regression tests for the mesh_config readers in src/gateway/receiver.py.

Verifies the JSON→dict mapping, error paths, skipping of nodes missing the 'id'
field, and the per-node PDR cadence map.

File reading now lives in load_mesh_config() and the mapping in
load_known_nodes(data), so the tests compose both through the _load helper —
the observable behaviour (exit code 1 on any bad config) is unchanged.

Run:
    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Put src/ on the path so package-absolute imports inside src/ work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.receiver import (  # noqa: E402
    load_mesh_config, load_known_nodes, load_intervals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tmp_json(data: dict) -> str:
    """Write data as JSON to a NamedTemporaryFile and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    json.dump(data, tmp)
    tmp.flush()
    tmp.close()
    return tmp.name


def _load(path: str) -> dict:
    """Reads a config file and returns the {node_id: label} map."""
    return load_known_nodes(load_mesh_config(path))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestLoadKnownNodes(unittest.TestCase):
    """Test 9 — load_known_nodes maps ids → node-<label>."""

    def test_basic_mapping(self):
        """Standard config: each label maps to 'node-<label>' keyed by id."""
        config = {
            "nodes_cfg": {
                "1": {"id": "!0b64122b"},
                "2": {"id": "!7c70da02"},
            }
        }
        path = _write_tmp_json(config)
        result = _load(path)

        self.assertIn("!0b64122b", result)
        self.assertIn("!7c70da02", result)
        self.assertEqual(result["!0b64122b"], "node-1")
        self.assertEqual(result["!7c70da02"], "node-2")

    def test_extra_fields_in_cfg_ignored(self):
        """Extra keys in node config (hop_limit, device_role) are silently ignored."""
        config = {
            "nodes_cfg": {
                "1": {"id": "!0b64122b", "hop_limit": 3, "device_role": "CLIENT"},
            }
        }
        path = _write_tmp_json(config)
        result = _load(path)
        self.assertEqual(result["!0b64122b"], "node-1")

    def test_label_format_is_node_dash_label(self):
        """Node value follows exact pattern 'node-<label>'."""
        config = {"nodes_cfg": {"alpha": {"id": "!deadbeef"}}}
        path = _write_tmp_json(config)
        result = _load(path)
        self.assertEqual(result["!deadbeef"], "node-alpha")

    def test_three_nodes_from_real_config_shape(self):
        """Mirrors the real mesh_config.json with 3 nodes."""
        config = {
            "nodes_cfg": {
                "1": {"id": "!0b64122b", "hop_limit": 3, "device_role": "CLIENT"},
                "2": {"id": "!7c70da02", "hop_limit": 3, "device_role": "CLIENT"},
                "3": {"id": "!32fe0d4e", "hop_limit": 2, "device_role": "CLIENT"},
            }
        }
        path = _write_tmp_json(config)
        result = _load(path)

        self.assertEqual(len(result), 3)
        self.assertEqual(result["!0b64122b"], "node-1")
        self.assertEqual(result["!7c70da02"], "node-2")
        self.assertEqual(result["!32fe0d4e"], "node-3")


class TestLoadKnownNodesMissingFields(unittest.TestCase):
    """Verify that nodes missing 'id' are skipped (not fatal)."""

    def test_node_missing_id_is_skipped(self):
        """A node entry without 'id' is skipped; other valid nodes are still loaded."""
        config = {
            "nodes_cfg": {
                "1": {"id": "!0b64122b"},       # valid
                "2": {"hop_limit": 3},           # missing 'id' — should be skipped
                "3": {"id": "!32fe0d4e"},        # valid
            }
        }
        path = _write_tmp_json(config)
        result = _load(path)

        self.assertIn("!0b64122b", result)
        self.assertIn("!32fe0d4e", result)
        self.assertEqual(len(result), 2,
            "Node without 'id' must be skipped, not crash")

    def test_all_nodes_missing_id_exits(self):
        """If every node lacks 'id', load_known_nodes calls sys.exit(1)."""
        config = {
            "nodes_cfg": {
                "1": {"hop_limit": 3},
                "2": {"hop_limit": 2},
            }
        }
        path = _write_tmp_json(config)
        with self.assertRaises(SystemExit) as ctx:
            _load(path)
        self.assertEqual(ctx.exception.code, 1)


class TestLoadKnownNodesErrorPaths(unittest.TestCase):
    """Error paths: file not found, bad JSON, missing nodes_cfg."""

    def test_file_not_found_exits(self):
        """Non-existent config path must call sys.exit(1)."""
        with self.assertRaises(SystemExit) as ctx:
            _load("/tmp/does_not_exist_at_all_xyz.json")
        self.assertEqual(ctx.exception.code, 1)

    def test_invalid_json_exits(self):
        """Malformed JSON must call sys.exit(1)."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        tmp.write("{not valid json}")
        tmp.flush()
        tmp.close()

        with self.assertRaises(SystemExit) as ctx:
            _load(tmp.name)
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_nodes_cfg_key_exits(self):
        """Config that has no 'nodes_cfg' key must call sys.exit(1)."""
        config = {"something_else": {}}
        path = _write_tmp_json(config)
        with self.assertRaises(SystemExit) as ctx:
            _load(path)
        self.assertEqual(ctx.exception.code, 1)

    def test_empty_nodes_cfg_exits(self):
        """Empty nodes_cfg dict must call sys.exit(1) (no valid nodes found)."""
        config = {"nodes_cfg": {}}
        path = _write_tmp_json(config)
        with self.assertRaises(SystemExit) as ctx:
            _load(path)
        self.assertEqual(ctx.exception.code, 1)


class TestLoadIntervals(unittest.TestCase):
    """The PDR cadence map: what the gateway measures against."""

    def test_intervals_read_per_node(self):
        """Each node's declared cadences are keyed by its 'node-<label>' name."""
        config = {
            "nodes_cfg": {
                "1":  {"id": "!0b64122b",
                       "intervals": {"device": 120, "environment": 120, "position": 600}},
                "p1": {"id": "!6c743130", "intervals": {"device": 900, "position": 1800}},
            }
        }
        intervals = load_intervals(load_mesh_config(_write_tmp_json(config)))

        self.assertEqual(intervals["node-1"],
                         {"device": 120, "environment": 120, "position": 600})
        self.assertEqual(intervals["node-p1"], {"device": 900, "position": 1800})

    def test_absent_kind_is_not_defaulted(self):
        """
        A kind a node does not declare must stay absent.

        Filling it from defaults would make the gateway infer losses against a
        cadence the node was never configured with — the proxy nodes broadcast
        no environment telemetry at all.
        """
        config = {"nodes_cfg": {"p1": {"id": "!6c743130",
                                       "intervals": {"device": 900, "position": 1800}}}}
        intervals = load_intervals(load_mesh_config(_write_tmp_json(config)))
        self.assertNotIn("environment", intervals["node-p1"])

    def test_null_interval_disables_flow(self):
        """An explicit null means 'this node does not broadcast it'."""
        config = {"nodes_cfg": {"1": {"id": "!0b64122b",
                                      "intervals": {"device": 120, "position": None}}}}
        intervals = load_intervals(load_mesh_config(_write_tmp_json(config)))
        self.assertEqual(intervals["node-1"], {"device": 120})

    def test_node_without_intervals_block_gets_defaults(self):
        """Legacy config with no 'intervals' falls back to the sensing profile."""
        config = {"nodes_cfg": {"1": {"id": "!0b64122b", "hop_limit": 2}}}
        intervals = load_intervals(load_mesh_config(_write_tmp_json(config)))
        self.assertEqual(intervals["node-1"],
                         {"device": 120, "environment": 120, "position": 600})

    def test_unknown_flow_kind_exits(self):
        """A typo'd kind must fail loudly, not silently disable tracking."""
        config = {"nodes_cfg": {"1": {"id": "!0b64122b",
                                      "intervals": {"devise": 120}}}}
        path = _write_tmp_json(config)
        with self.assertRaises(SystemExit) as ctx:
            load_intervals(load_mesh_config(path))
        self.assertEqual(ctx.exception.code, 1)

    def test_non_positive_interval_exits(self):
        """A zero/negative cadence would divide the gap estimator by zero."""
        for bad in (0, -120):
            config = {"nodes_cfg": {"1": {"id": "!0b64122b",
                                          "intervals": {"device": bad}}}}
            path = _write_tmp_json(config)
            with self.assertRaises(SystemExit) as ctx:
                load_intervals(load_mesh_config(path))
            self.assertEqual(ctx.exception.code, 1, f"interval {bad} must be rejected")


if __name__ == "__main__":
    unittest.main()
