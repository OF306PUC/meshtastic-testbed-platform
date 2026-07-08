"""
Regression tests for load_known_nodes (src/gateway/receiver.py).

Verifies the JSON→dict mapping, error paths, and skipping of nodes
missing the 'id' field.

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

from gateway.receiver import load_known_nodes  # noqa: E402


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
        result = load_known_nodes(path)

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
        result = load_known_nodes(path)
        self.assertEqual(result["!0b64122b"], "node-1")

    def test_label_format_is_node_dash_label(self):
        """Node value follows exact pattern 'node-<label>'."""
        config = {"nodes_cfg": {"alpha": {"id": "!deadbeef"}}}
        path = _write_tmp_json(config)
        result = load_known_nodes(path)
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
        result = load_known_nodes(path)

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
        result = load_known_nodes(path)

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
            load_known_nodes(path)
        self.assertEqual(ctx.exception.code, 1)


class TestLoadKnownNodesErrorPaths(unittest.TestCase):
    """Error paths: file not found, bad JSON, missing nodes_cfg."""

    def test_file_not_found_exits(self):
        """Non-existent config path must call sys.exit(1)."""
        with self.assertRaises(SystemExit) as ctx:
            load_known_nodes("/tmp/does_not_exist_at_all_xyz.json")
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
            load_known_nodes(tmp.name)
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_nodes_cfg_key_exits(self):
        """Config that has no 'nodes_cfg' key must call sys.exit(1)."""
        config = {"something_else": {}}
        path = _write_tmp_json(config)
        with self.assertRaises(SystemExit) as ctx:
            load_known_nodes(path)
        self.assertEqual(ctx.exception.code, 1)

    def test_empty_nodes_cfg_exits(self):
        """Empty nodes_cfg dict must call sys.exit(1) (no valid nodes found)."""
        config = {"nodes_cfg": {}}
        path = _write_tmp_json(config)
        with self.assertRaises(SystemExit) as ctx:
            load_known_nodes(path)
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
