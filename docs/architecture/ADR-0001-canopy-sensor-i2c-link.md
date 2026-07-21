# ADR-0001 — Canopy T/RH sensor link over the 1-3 m node gap

**Status:** Accepted
**Date:** 2026-07-10
**Owner:** technical-director
**Related:** project-overview.md § Known constraints (SHT4X I2C reliability);
docs/testbeds/san-joaquin.md; memory [[data-contract-gateway-web]]

## Context

The environmental sensor is the **Seeed Studio Grove SHT4X** (SHT40), an
**I2C-only** device at address `0x44`. The SensCAP Solar Node P1 Pro exposes a
**Grove port** that carries I2C / UART / GPIO. Because the SHT4X speaks I2C on a
known address, the **Meshtastic Environmental Telemetry module reads it natively**
— the firmware scans the I2C bus, finds `0x44`, and reports temperature/humidity
with **zero firmware modification**. Preserving this is a **hard requirement**
(Phase 1 does not touch firmware).

The deployment imposes a physical split: the solar node must sit **1-3 m above
the vine** (LoRa antenna height + solar exposure), but the sensor must sit **down
in the canopy** — measuring T/RH inside the canopy is the scientifically correct
location (VPD, leaf wetness, disease models depend on canopy microclimate, not on
node-height air).

**Problem:** I2C is not designed for a 1-3 m run. The bus-capacitance limit is
**400 pF**; cable adds ~50-100 pF/m, so 3 m alone is ~150-300 pF, and the Grove
I2C Hub + connectors push it over budget. Rise times degrade: the 1-bit ACK at
`0x44` still passes but the multi-byte `readSerial()` fails — the exact failure
already documented as the "SHT4X I2C reliability" constraint, now aggravated by
distance.

## Decision

Extend the I2C bus over the canopy gap with a **protocol-transparent differential
I2C extender** — a **PCA9615** (differential I2C / DPI2C) pair (fallback:
**P82B96** buffer pair), one at the node end and one at the sensor end:

- The SHT4X stays a plain I2C slave at `0x44`; the extender is invisible to the
  protocol, so **Meshtastic firmware is untouched** — the hard requirement is met.
- Signalling runs as **differential pairs over twisted pair (Cat5/6)**: SDA+/SDA-,
  SCL+/SCL-, plus 3.3 V and GND down the same cable (~6 conductors → Cat5 fits).
- **Remove the Grove I2C Hub** (its capacitance is what tips the budget) and clock
  the bus at **100 kHz** (standard mode) for margin.
- Weatherproof the **canopy-end** extender + sensor in an IP-rated enclosure.

## Consequences

**Positive**
- Firmware and telemetry data contract unchanged; sensor remains canonical `0x44`.
- Robust over 1-3 m (differential I2C is rated well beyond, ~10-30 m).
- Cheapest change that satisfies the "don't touch firmware" requirement.

**Negative / costs**
- Two extender breakouts + an extra 3.3 V rail carried down the cable (BOM +
  assembly).
- A weatherproof enclosure and a new outdoor failure point at the canopy end;
  watch condensation and outdoor ESD.

## Alternatives considered (rejected)

- **Raw/slowed I2C + strong pull-ups over the same cable** — fragile at 1-3 m
  outdoors; does not solve the capacitance problem, only postpones it.
- **Satellite MCU in the canopy** (reads SHT4X locally, uplinks over UART/1-Wire/
  RS-485) — **rejected: breaks firmware transparency** (the node would no longer
  see an I2C SHT4X) and needs powered/programmed electronics in the shaded canopy
  (bad for solar).
- **RS-485/Modbus or SDI-12 canopy-grade T/RH probe** — the "right tool" for
  long field runs, but **not read natively by Meshtastic**, so it needs custom
  firmware/module. **Rejected for Phase 1**; revisit in Phase 2/3 if scaling to
  many sensors/nodes makes differential I2C unwieldy.

## Follow-ups

- Source PCA9615 (or P82B96) breakouts; confirm Grove-port I2C pinout on the P1 Pro.
- Update project-overview.md § Known constraints to reference this ADR.
- Bench-test at target cable length before field install.
