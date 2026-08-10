# Architecture — Raspberry Pi + 5G HAT Gateway

> **Status: EXPLORING.** This document captures the plan and open questions for
> moving the LoRa gateway host from a USB-connected desktop to a Raspberry Pi
> with a 5G HAT. No implementation is committed yet — this is a design scratchpad,
> not a spec. It will graduate to an ADR once the approach is decided.
>
> **Scope note (2026-08-06):** this is about the **gateway** only. The PBX sites
> also get a Raspberry Pi, but as an edge collector on building WiFi with no
> cellular backhaul — decided separately in
> [`ADR-0002`](./ADR-0002-pbx-site-edge-collector.md). Do not conflate the two.

## Motivation

Today the LILYGO gateway is tethered to a host desktop over USB, which pins the
testbed to wherever that machine lives and to campus wired/Wi-Fi networking. A
**Raspberry Pi + 5G HAT** as the gateway host would:

- Let the gateway sit near the mesh (better RF placement) instead of near a desk.
- Provide independent **5G backhaul** for telemetry, decoupling the testbed from
  campus network access.
- Make the whole gateway a self-contained, field-deployable unit (Pi + HAT +
  LILYGO + power).

## Proposed topology

```
[LoRa mesh nodes]
      | LoRa
      v
[LILYGO gateway (CLIENT_MUTE)]
      | USB serial (/dev/ttyACM0)
      v
[Raspberry Pi]  --- 5G HAT ---> cellular backhaul
      |
      | runs: src/gateway/receiver.py -> mqtt_connector.py
      |
      v
[MQTT broker + Telegraf + InfluxDB + monitor/]
```

**Decided 2026-08-07:** the **full pipeline runs on the Pi** — Mosquitto,
Telegraf, InfluxDB and `monitor/`. The gateway Pi is therefore the centre of
gravity of the system, and the three collectors defined in
[`ADR-0002`](./ADR-0002-pbx-site-edge-collector.md) publish to it. Two
requirements follow: it must **boot from a USB SSD** (InfluxDB on microSD will
not survive continuous writes and power loss), and it needs a **stable address**
on the building network, since the remote collectors and every browser have to
reach it. Backhaul for the first deployment is building WiFi, not cellular — the
5G HAT below remains a later option, not a dependency.

## What likely carries over unchanged

- `src/gateway/receiver.py` (serial read; `--port` required) and
  `mqtt_connector.py` — Python runs on the Pi (ARM). Verify the `meshtastic` CLI
  and serial stack work on Raspberry Pi OS.
- The data contract (MQTT topics + InfluxDB schema) — see memory
  `data-contract-gateway-web`. **Must not change** regardless of host.
- `install_service.sh` (systemd) — likely adaptable to the Pi.

## Open questions

- **Pi model & resources.** Which Pi? Can it host InfluxDB + Docker stack, or
  only the receiver? (InfluxDB on a Pi is feasible but I/O-sensitive.)
- **5G HAT model & carrier.** Which HAT (e.g. SIM8200/RM50x-class)? Carrier/APN,
  data plan, static IP vs NAT (affects whether the pipeline can live off-Pi).
- **Where does the pipeline live?** All-on-Pi (edge) vs receiver-on-Pi +
  pipeline-in-cloud/lab (backhaul carries MQTT). Trade-off: edge resilience vs
  central storage.
- **Power & enclosure.** Field power budget for Pi + 5G HAT (both are power-hungry
  vs the solar nodes); outdoor enclosure and thermals.
- **Backhaul cost/latency.** 5G data volume of the telemetry stream; buffering
  strategy if the link drops (store-and-forward on the Pi).
- **Security.** Exposing the pipeline over cellular — VPN/tunnel vs public
  endpoint; credential handling on a field device.
- **Docker on ARM.** Confirm the `docker-compose.yaml` images have arm64 builds
  (mosquitto, influxdb, telegraf) if running the stack on the Pi.

## Next steps (when exploration resumes)

1. Confirm Pi model + 5G HAT model → pin resource budget.
2. Decide pipeline placement (edge vs backhaul).
3. Bench-test `receiver.py` + `meshtastic` CLI on the Pi over USB.
4. Promote the chosen design to an ADR and update `docs/project-overview.md`.
