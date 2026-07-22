"""Shared helper for driving the Meshtastic CLI with retry-on-transient-error.

Previously duplicated verbatim in the node and gateway configuration scripts.
"""
import subprocess
import time

MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds to wait before retrying

# Keywords that indicate a transient serial error worth retrying
RETRYABLE_ERRORS = [
    "couldn't be opened",
    "Input/output error",
    "OS Error",
    "serial device",
    "write failed",
]


def is_retryable(stderr: str, stdout: str) -> bool:
    combined = (stderr + stdout).lower()
    return any(keyword.lower() in combined for keyword in RETRYABLE_ERRORS)


def run(cmd, retries=MAX_RETRIES):
    print(f"\nRunning: {cmd}")
    for attempt in range(1, retries + 1):
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode == 0 and not is_retryable(result.stderr, result.stdout):
            time.sleep(2)
            return  # success
        # Something went wrong
        print(f"ERROR (attempt {attempt}/{retries}):", result.stderr or result.stdout)
        if attempt < retries:
            print(f"Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        else:
            print(f"Command failed after {retries} attempts. Continuing...")
            time.sleep(2)


def get_config_value(argv_prefix, key):
    """Read back a single config value via `meshtastic --get <key>`.

    `argv_prefix` is the argv list up to --get, e.g. ['meshtastic', '--port', p]
    (no shell, so ports/paths need no quoting). Returns the reported value as a
    string, or '' if it couldn't be read. Used to verify a --set actually
    persisted — e.g. device.rebroadcast_mode, which was silently not sticking
    when set in the same command as device.role.
    """
    result = subprocess.run(argv_prefix + ["--get", key], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        # The CLI prints lines like "device.rebroadcast_mode: LOCAL_ONLY".
        if key in line and ":" in line:
            return line.split(":", 1)[1].strip()
    return ""
