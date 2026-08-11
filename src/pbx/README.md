# `src/pbx/` — PBX-attached node configuration

This directory holds the **host-side CLI configuration** for the Meshtastic node
that hangs off the **PBX**. It is the PBX counterpart of `src/node/` and
`src/gateway/`: Python scripts that drive the `meshtastic` CLI to provision the
LoRa node, sharing channels/region/preset/PSKs from `src/common/radio_config.py`.

> **The PBX firmware itself is NOT in this repo.**
>
> The PBX is a separate project — a Zephyr firmware for the **Nordic nRF52840**
> that acts as a Meshtastic-compatible BLE peripheral and multiplexes **up to 6
> phones onto a single Meshtastic node** over UART.
>
> **→ https://github.com/OF306PUC/meshpbx**
>
> Everything about the PBX design, build/flash steps (nRF Connect SDK / Zephyr),
> the GATT service, the UART Stream API wiring, and the phone-app integration guide
> lives in that repo's README and `docs/`.

## How the two repos fit together

```
 phones (≤6)  ──BLE──▶  nRF52840 PBX  ──UART1 (Stream API)──▶  Meshtastic node  ──LoRa mesh──▶ …
                        └ firmware:                              └ configured by the
                          meshpbx repo                scripts in THIS directory
```

- **`../meshpbx`** (separate GitHub repo) → the nRF52840 firmware that
  *is* the PBX.
- **`src/pbx/`** (here) → configures the Meshtastic node the PBX talks to over
  UART: BLE disabled on the node (the PBX serves the BLE side), Serial module in
  `PROTO` mode on UART1, and the shared testbed channels.

## Files

| File | Responsibility |
|---|---|
| `configure.py` | Provisions a PBX-attached node via the `meshtastic` CLI: LoRa (region/preset/hop limit), device role, BLE off, both testbed channels (`CPS_RTC` telemetry + `PUC_NET` messaging) with PSKs from `.env`, telemetry, Serial module (Stream API on UART1), and GPS. Takes `--node-id p1\|p2` and `--port`. |
| `configure_params.py` | PBX-node parameter constants. Imports the shared channel/region/preset/rebroadcast settings from `common/radio_config.py`; keeps only PBX-specific values (Bluetooth off, Serial `PROTO` on GPIO15/35 @115200, telemetry 900 s, GPS 1800 s). |
| `fetch_node_config.py` | Standalone measuring tool: captures the node's boot `want_config` FromRadio burst to size the firmware's config cache (`CONFIG_CACHE_ARENA_BYTES`). Not part of the provisioning flow. |
| `__init__.py` | Marks `pbx` as an importable package (same pattern as `node`/`gateway`). |

## Surveyed positions

`mesh_config.json` accepts an optional `position` block per node, provisioned as
the radio's `position.fixed_position`:

```json
"p1": {"id": "!6c743130", "hop_limit": 2, "device_role": "CLIENT",
       "intervals": {"device": 600, "position": 1800},
       "position": {"lat": -33.4757888, "lon": -70.5953792, "alt": 590}}
```

It is a **record, not a setting**: nothing writes it to a radio. The dashboard
reads it so the map shows where the nodes actually are, and `configure.py`
ignores it entirely.

Provisioning it as `position.fixed_position` was considered and rejected. The
node would then report the survey instead of its own fix, which destroys the one
comparison worth having — **survey minus GPS-reported is the GPS error**, and on
a measurement testbed that is a result, not noise to be eliminated. Keeping them
apart preserves both numbers, which is why `/api/nodes` returns the surveyed
coordinate as `lat`/`lon` with `source: "surveyed"` and carries the reported one
alongside as `gps_lat`/`gps_lon`.

Absent means "not surveyed yet" — the same convention `intervals` uses. There is
no default, because inventing coordinates puts a node somewhere it has never
been. A node with no survey falls back to `source: "gps"`.

A malformed block fails loudly and that node is skipped, rather than reaching the
map: since nothing provisions this field, the dashboard is its only consumer and
therefore the only place its validation ever runs.

When surveying, average several readings at each point. A 5 m error per node is
noise you do not need when the coordinates go on to produce inter-node distances
for the adjacency matrix.

> **Unrelated but adjacent:** the nodes currently report one byte-identical
> lat/lon for all three while altitude varies normally, which is the signature of
> a reduced per-channel `module_settings.position_precision` quantising lat/lon
> onto a grid. Surveying works around it for the map, but it still makes *reported*
> position useless. Read it with `meshtastic --port <dev> --info`.

## Usage

Per-PBX settings (`p1` and `p2` differ — e.g. hop limit) live in
`mesh_config.json`; `configure.py` reads them via `--node-id`. Set
`LORA_MSG_CHANNEL_PSK` in `.env` first (the script fails early if it is missing).

```sh
# From the repo root, with the PBX node connected on a serial port:
python src/pbx/configure.py --node-id p1 --port /dev/ttyUSB0
python src/pbx/configure.py --node-id p2 --port /dev/ttyUSB0
```

See the root `README.md` for the full testbed setup and `.env` conventions.
