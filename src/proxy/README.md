# `src/proxy/` — Proxy-attached node configuration

This directory holds the **host-side CLI configuration** for the Meshtastic node
that hangs off the **BLE proxy**. It is the proxy counterpart of `src/node/` and
`src/gateway/`: Python scripts that drive the `meshtastic` CLI to provision the
LoRa node, sharing channels/region/preset/PSKs from `src/common/radio_config.py`.

> **The proxy firmware itself is NOT in this repo.**
>
> The proxy is a separate project — a Zephyr firmware for the **Nordic nRF52840**
> that acts as a Meshtastic-compatible BLE peripheral and multiplexes **up to 6
> phones onto a single Meshtastic node** over UART.
>
> **→ https://github.com/OF306PUC/meshtastic-ble-proxy**
>
> Everything about the proxy design, build/flash steps (nRF Connect SDK / Zephyr),
> the GATT service, the UART Stream API wiring, and the phone-app integration guide
> lives in that repo's README and `docs/`.

## How the two repos fit together

```
 phones (≤6)  ──BLE──▶  nRF52840 proxy  ──UART1 (Stream API)──▶  Meshtastic node  ──LoRa mesh──▶ …
                        └ firmware:                              └ configured by the
                          meshtastic-ble-proxy repo                scripts in THIS directory
```

- **`../meshtastic-ble-proxy`** (separate GitHub repo) → the nRF52840 firmware that
  *is* the proxy.
- **`src/proxy/`** (here) → configures the Meshtastic node the proxy talks to over
  UART: BLE disabled on the node (the proxy serves the BLE side), Serial module in
  `PROTO` mode on UART1, and the shared testbed channels.

## Files

| File | Responsibility |
|---|---|
| `configure.py` | Provisions a proxy-attached node via the `meshtastic` CLI: LoRa (region/preset/hop limit), device role, BLE off, both testbed channels (`CPS_RTC` telemetry + `PUC_NET` messaging) with PSKs from `.env`, telemetry, Serial module (Stream API on UART1), and GPS. Takes `--node-id p1\|p2` and `--port`. |
| `configure_params.py` | Proxy-node parameter constants. Imports the shared channel/region/preset/rebroadcast settings from `common/radio_config.py`; keeps only proxy-specific values (Bluetooth off, Serial `PROTO` on GPIO15/35 @115200, telemetry 900 s, GPS 1800 s). |
| `fetch_node_config.py` | Standalone measuring tool: captures the node's boot `want_config` FromRadio burst to size the firmware's config cache (`CONFIG_CACHE_ARENA_BYTES`). Not part of the provisioning flow. |
| `__init__.py` | Marks `proxy` as an importable package (same pattern as `node`/`gateway`). |

## Usage

Per-proxy settings (`p1` and `p2` differ — e.g. hop limit) live in
`mesh_config.json`; `configure.py` reads them via `--node-id`. Set
`LORA_MSG_CHANNEL_PSK` in `.env` first (the script fails early if it is missing).

```sh
# From the repo root, with the proxy node connected on a serial port:
python src/proxy/configure.py --node-id p1 --port /dev/ttyUSB0
python src/proxy/configure.py --node-id p2 --port /dev/ttyUSB0
```

See the root `README.md` for the full testbed setup and `.env` conventions.
