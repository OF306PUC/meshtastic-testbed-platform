# Testbed — San Joaquín

> One document per physical deployment. This is the first testbed hosted by the
> Meshtastic TestBed Platform. Fields marked _TBD (owner)_ need the physical/site
> data that only the deployer can supply — fill them in as the deployment settles.

## Identity

- **Site:** San Joaquín campus, Pontificia Universidad Católica de Chile.
- **Operated by:** Cyber-Physical Systems Research and Technology Center (CPS-RTC).
- **Purpose:** Outdoor LoRa mesh testbed for environmental monitoring
  (temperature, humidity, GPS, power) and for studying **mesh scalability** under
  the Meshtastic stack.
- **Status:** _TBD (owner)_ — e.g. planning / partial deployment / live.

## Site & location

- **Area / building references:** _TBD (owner)_
- **Approximate extent (m):** _TBD (owner)_
- **Reference coordinates (lat, lon):** _TBD (owner)_
- **Terrain / obstacles (line-of-sight notes):** _TBD (owner)_
- **Power availability per site (solar vs mains):** _TBD (owner)_

## Physical topology

_TBD (owner)_ — describe where each node sits and the intended mesh links.
Suggested content: a site map or sketch (drop into `docs/diagrams/`), inter-node
distances, and which hops are expected. Reference diagrams currently available:
`docs/diagrams/sensecap-mesh-mesh.drawio.png`, `docs/diagrams/gateway.drawio.png`.

```
[node-1] --? m--> [node-2] --? m--> [node-3]
                                        |
                                   (LoRa mesh)
                                        v
                              [LILYGO gateway] --USB--> [Raspberry Pi + 5G HAT]
                                                              |
                                                          5G backhaul
                                                              v
                                                   [MQTT/InfluxDB pipeline]
```

## Node roster

Canonical per-node radio/hardware parameters live in the radio inventory
spreadsheet (schema: `docs/hardware/radio-inventory-schema.md`). Summary of the
current deployment (from `mesh_config.json`):

| Ref    | Node ID     | Device                     | Role         | Hop limit |
|--------|-------------|----------------------------|--------------|-----------|
| node-1 | `!0b64122b` | SensCAP Solar Node P1 Pro  | `CLIENT`     | 3         |
| node-2 | `!7c70da02` | SensCAP Solar Node P1 Pro  | `CLIENT`     | 3         |
| node-3 | `!32fe0d4e` | SensCAP Solar Node P1 Pro  | `CLIENT`     | 2         |
| gw     | _TBD_       | LILYGO (ESP32)             | `CLIENT_MUTE`| —         |

> Note: there is a known cosmetic label mismatch between `monitor/app.py` and
> `mesh_config.json` for `!7c70da02`/`!0b64122b`. See project-overview "Known
> constraints". Reconcile before publishing site labels.

## Gateway & backhaul

- **Gateway radio:** LILYGO (ESP32), Meshtastic role `CLIENT_MUTE` (receives, does
  not rebroadcast).
- **Host:** Raspberry Pi with a 5G HAT (new — see
  `docs/architecture/gateway-rpi-5g.md`). The Pi hosts the LILYGO over USB and
  carries telemetry off-site over 5G. **Still in exploration — not yet deployed.**

## Network / radio configuration

- **Region:** `ANZ`
- **Modem preset:** `LONG_FAST`
- **Channel name:** `TB CPS-RTC`
- **Rebroadcast mode:** nodes `LOCAL_ONLY`; gateway `CLIENT_MUTE`
- **PSK:** shared channel key stored in `.env` (loaded by `src/common/radio_config.py`)
- **Meshtastic firmware:** v2.7.19

## Deployment status & history

_TBD (owner)_ — dated log of what was installed/moved/changed on site.

## Open questions

- Exact node placement and inter-node distances (topology).
- Gateway node ID once the LILYGO is provisioned behind the Pi.
- Whether the 5G-hosted gateway changes the mesh role assignment.
