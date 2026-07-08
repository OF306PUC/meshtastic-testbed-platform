# Radio Inventory Spreadsheet — Content & Schema

> The spreadsheet itself (`.xlsx`) is maintained by the owner. This document is
> the **agreed content**: which sheets exist, their columns, allowed values, and
> example rows pre-filled with the current San Joaquín deployment. Copy these
> tables into the workbook and extend as the testbed grows.
>
> Scope note: only the **Radio Hardware**, **LoRa Parameters**, and **Reference**
> sheets are defined now. The **Scalability Experiments** sheet (metrics + power
> consumption) is intentionally deferred until the study equations settle — see
> `docs/scalability/README.md`.

---

## Sheet 1 — Radio Hardware

Physical inventory: one row per radio device (nodes, gateway, future repeaters).

| Column             | Type / allowed values                                   | Notes |
|--------------------|---------------------------------------------------------|-------|
| `asset_id`         | text (your tag, e.g. `SJ-N1`)                           | Stable internal label |
| `node_ref`         | text (`node-1`, `gw`, …)                                | Cross-refs LoRa Parameters sheet |
| `node_id`          | text (`!0b64122b`)                                      | Meshtastic node ID |
| `device_model`     | text                                                    | e.g. SensCAP Solar Node P1 Pro / LILYGO |
| `chipset_mcu`      | text (`nRF52840`, `ESP32`)                              | |
| `role`             | `CLIENT` \| `CLIENT_MUTE` \| `ROUTER` \| `REPEATER`     | Meshtastic device role |
| `firmware_version` | text (`2.7.19`)                                         | Meshtastic firmware |
| `antenna_type`     | text                                                    | e.g. whip / external |
| `antenna_gain_dBi` | number                                                  | |
| `tx_power_dBm`     | number                                                  | Configured TX power |
| `power_source`     | `solar` \| `USB` \| `mains` \| `battery`                | |
| `host`             | text (`—`, `RPi+5G`, `USB-PC`)                          | Host the radio hangs off (gateways) |
| `location`         | text / coords                                           | Site placement |
| `status`           | `planned` \| `deployed` \| `fault` \| `retired`         | |
| `notes`            | text                                                    | |

**Example rows** (current deployment; `_TBD_` = owner to fill):

| asset_id | node_ref | node_id     | device_model              | chipset_mcu | role         | firmware_version | antenna_type | antenna_gain_dBi | tx_power_dBm | power_source | host    | location | status   | notes |
|----------|----------|-------------|---------------------------|-------------|--------------|------------------|--------------|------------------|--------------|--------------|---------|----------|----------|-------|
| SJ-N1    | node-1   | `!0b64122b` | SensCAP Solar Node P1 Pro | nRF52840    | CLIENT       | 2.7.19           | _TBD_        | _TBD_            | _TBD_        | solar        | —       | _TBD_    | deployed | hop_limit 3 |
| SJ-N2    | node-2   | `!7c70da02` | SensCAP Solar Node P1 Pro | nRF52840    | CLIENT       | 2.7.19           | _TBD_        | _TBD_            | _TBD_        | solar        | —       | _TBD_    | deployed | hop_limit 3 |
| SJ-N3    | node-3   | `!32fe0d4e` | SensCAP Solar Node P1 Pro | nRF52840    | CLIENT       | 2.7.19           | _TBD_        | _TBD_            | _TBD_        | solar        | —       | _TBD_    | deployed | hop_limit 2 |
| SJ-GW    | gw       | _TBD_       | LILYGO                    | ESP32       | CLIENT_MUTE  | 2.7.19           | _TBD_        | _TBD_            | _TBD_        | USB          | RPi+5G  | _TBD_    | planned  | host move to Pi under study |

---

## Sheet 2 — LoRa Parameters

Radio/link configuration: one row per node (the RF + Meshtastic channel settings).

| Column                 | Type / allowed values                        | Notes |
|------------------------|----------------------------------------------|-------|
| `node_ref`             | text (`node-1`, `gw`)                        | Cross-refs Radio Hardware sheet |
| `region`               | `ANZ` (current) \| other Meshtastic regions  | Sets legal frequency band |
| `modem_preset`         | `LONG_FAST` (current) \| see Reference       | Determines SF/BW/CR |
| `bandwidth_kHz`        | number (derived from preset)                 | See Reference sheet |
| `spreading_factor`     | integer 7–12 (derived from preset)           | See Reference sheet |
| `coding_rate`          | `4/5`..`4/8` (derived from preset)           | See Reference sheet |
| `channel_name`         | text (`TB CPS-RTC`)                          | |
| `hop_limit`            | integer                                      | From `mesh_config.json` |
| `rebroadcast_mode`     | `LOCAL_ONLY` \| `ALL` \| `KNOWN_ONLY` \| `NONE` (CLIENT_MUTE ≈ no rebroadcast) | |
| `tx_power_dBm`         | number                                       | |
| `telemetry_interval_s` | integer (device telemetry period)           | Currently 60 |
| `gps_update_s`         | integer                                      | Currently ~300 |
| `notes`                | text                                         | |

**Example rows:**

| node_ref | region | modem_preset | bandwidth_kHz | spreading_factor | coding_rate | channel_name | hop_limit | rebroadcast_mode | tx_power_dBm | telemetry_interval_s | gps_update_s | notes |
|----------|--------|--------------|---------------|------------------|-------------|--------------|-----------|------------------|--------------|----------------------|--------------|-------|
| node-1   | ANZ    | LONG_FAST    | 250           | 11               | 4/5         | TB CPS-RTC   | 3         | LOCAL_ONLY       | _TBD_        | 60                   | 300          | |
| node-2   | ANZ    | LONG_FAST    | 250           | 11               | 4/5         | TB CPS-RTC   | 3         | LOCAL_ONLY       | _TBD_        | 60                   | 300          | |
| node-3   | ANZ    | LONG_FAST    | 250           | 11               | 4/5         | TB CPS-RTC   | 2         | LOCAL_ONLY       | _TBD_        | 60                   | 300          | |
| gw       | ANZ    | LONG_FAST    | 250           | 11               | 4/5         | TB CPS-RTC   | —         | CLIENT_MUTE      | _TBD_        | —                    | —            | receive-only |

---

## Sheet 3 — Reference (allowed values & preset map)

**Meshtastic modem presets → radio params** (verify against firmware v2.7.19; the
firmware is the source of truth as presets can shift between versions):

| Preset          | Bandwidth (kHz) | Spreading factor | Coding rate |
|-----------------|-----------------|------------------|-------------|
| SHORT_TURBO     | 500             | 7                | 4/5         |
| SHORT_FAST      | 250             | 7                | 4/5         |
| SHORT_SLOW      | 250             | 8                | 4/5         |
| MEDIUM_FAST     | 250             | 9                | 4/5         |
| MEDIUM_SLOW     | 250             | 10               | 4/5         |
| LONG_FAST ★     | 250             | 11               | 4/5         |
| LONG_MODERATE   | 125             | 11               | 4/8         |
| LONG_SLOW       | 125             | 12               | 4/8         |

★ = current testbed preset.

**Region band (frequency):**

| Region | Band (MHz) | Notes |
|--------|------------|-------|
| ANZ    | 915–928    | Current testbed region (Australia/NZ plan) |

**Enumerations used above:**

- `role`: `CLIENT`, `CLIENT_MUTE`, `ROUTER`, `REPEATER`
- `rebroadcast_mode`: `ALL`, `LOCAL_ONLY`, `KNOWN_ONLY`, `NONE`
- `power_source`: `solar`, `USB`, `mains`, `battery`
- `status`: `planned`, `deployed`, `fault`, `retired`

---

## Sheet 4 — Scalability Experiments (DEFERRED)

**Not yet defined.** Will hold the experiment matrix and measured metrics (PDR,
hop count, airtime, duty cycle, latency, **power consumption**) once the owner
finalises the governing equations and simulator setup. See
`docs/scalability/README.md`. Leave this sheet out of the workbook for now, or
add it as an empty placeholder.
