# Session Log — Bitácora

Chronological log of every work session. A new entry is added at the start of
each session and filled in real time as work progresses. **Newest entry on top.**

Entry format:
```
## YYYY-MM-DD — [brief session title]
**Time:** HH:MM (aprox)
**User request:** ...
### Actions taken
- ...
### Decisions
- ...
### Outcomes
- ...
### Next steps / open questions
- ...
```

---

## 2026-07-08 — Namespace rename: channel + `lora-testbed` → `meshtastic-testbed`
**Time:** (aprox)
**User request:** `/start`; luego cerrar el cambio sin commitear en `radio_config.py` (rename de canal, sin commitear); y renombrar todo el namespace `lora-testbed` → `meshtastic-testbed` (incluida la DB InfluxDB).
### Actions taken
- **Canal:** confirmado rename `CHANNEL_NAME "TB CPS-RTC" → "CPS_RTC"` (fuente única en `src/common/radio_config.py`). Sincronizadas 5 referencias en README, project-overview, testbeds/san-joaquin, hardware/radio-inventory-schema. Verificado que el contrato gateway↔web NO se afecta (topics usan prefijo, no el nombre de canal).
- **Namespace MQTT/DB:** `lora-testbed` → `meshtastic-testbed` y `cpsrtc_lora_telemetry` → `cpsrtc_meshtastic_telemetry` en los 3 lados del contrato + periféricos: `mqtt_connector.py`, `telegraf.conf`, `monitor/param.py`, `docker-compose.yaml`, `src/gateway/config.py`, `src/tools/plot_history.py`, `configuration.env.example`, `tests/test_mesh_receiver.py` (3 asserts), README, project-overview, y la memoria `data-contract-gateway-web`.
- **Nombre de repo:** corregidas referencias factuales al repo viejo en README (URL de clone → `meshtastic-testbed-platform.git`, `cd`, raíz del árbol) y en project-overview (raíz del árbol). Historial de `session-log` dejado intacto.
- Scaffold de `/start`: creado `.tmp/`; `execution/` creado y luego descartado a pedido del owner (no debe commitearse; `rm` bloqueado por permisos — queda como dir vacío sin trackear).
### Decisions
- Rename de canal y de namespace confirmados como definitivos por el owner.
- DB InfluxDB **sí** renombrada (owner lo pidió). Implica que el histórico queda en la DB vieja `cpsrtc_lora_telemetry`; migración/descarte es decisión operativa aparte del owner.
- **Nada commiteado** esta sesión (posture "solo working tree"), por decisión del owner.
### Outcomes
- Namespace `lora-testbed`/`cpsrtc_lora` con cero ocurrencias en el árbol (fuera de historial git y entradas históricas de la bitácora). `py_compile` OK en los .py editados. Tests no ejecutables en este entorno (`meshtastic` no instalado; falla en import, ajeno al cambio).
### Next steps / open questions
- **Operativo (owner):** re-correr `configure.py` en los 3 nodos + gateway para reunirlos en el canal `CPS_RTC`; reiniciar gateway + telegraf + monitor para el nuevo prefijo de topic; decidir migración del histórico InfluxDB `cpsrtc_lora_telemetry` → `cpsrtc_meshtastic_telemetry`.
- **git-lead:** commit del rename (canal + namespace + doc-sync) cuando el owner lo apruebe.
- Eliminar `execution/` (`rm -rf execution`) si se quiere fuera del working tree.

## 2026-07-08 — Repo rename + reorg to meshtastic-testbed-platform
**Time:** (aprox)
**User request:** "Start over" the project reusing existing content: reorganise, add
documentation (San Joaquín testbed), incorporate a Raspberry Pi + 5G HAT gateway hosting
the LILYGO, and design an Excel of radio hardware + node parameters for a scalability study.
### Actions taken
- Owner created and cloned a fresh GitHub repo `OF306PUC/meshtastic-testbed-platform`
  (empty). Migrated content into it via `rsync` from `LoRa-TestBed-Platform`, excluding
  secrets (`.env`, `configuration.env`) and runtime cruft (`data/`, `log/`, `__pycache__/`,
  `.venv/`, source `.git/`). Copied the studio harness (`.claude/` + `CLAUDE.md`) per owner.
- Reorganised `docs/`: moved diagrams/images/`read_config.txt` into `docs/diagrams/`;
  created `docs/testbeds/`, `docs/architecture/`, `docs/scalability/`, `docs/hardware/`.
- New docs: `testbeds/san-joaquin.md` (site skeleton, physical data marked TBD),
  `architecture/gateway-rpi-5g.md` (RPi+5G plan + open questions — EXPLORING, not committed),
  `scalability/README.md` (framing stub — metrics/power deferred), and
  `hardware/radio-inventory-schema.md` (Excel content: Radio Hardware + LoRa Parameters +
  Reference sheets, pre-filled with the 3 current nodes + gateway).
- Updated README (title → "Meshtastic TestBed Platform", diagram paths → `docs/diagrams/`,
  gateway-host wording) and `project-overview.md` (identity, docs directory tree).
### Decisions
- Renamed conceptually to a *platform* that hosts multiple physical testbeds; San Joaquín
  is the first. Owner is creating the `.xlsx` themselves — I provide the schema/content as
  a doc, not a generated file.
- Scalability metrics + power-consumption modelling **deferred** at owner's request (still
  defining equations/architecture and evaluating Meshtastic simulators).
- Local folder stays `LoRa-TestBed-Platform`; the new repo lives at
  `../meshtastic-testbed-platform`.
### Outcomes
- New repo populated and reorganised; four new docs in place. Nothing committed yet.
### Next steps / open questions
- Owner to fill San Joaquín physical data (coords, topology, distances) and build the .xlsx.
- First commit on the new repo (git-lead) once content reviewed.
- Resume scalability study (equations + simulator) and RPi+5G exploration when ready.

## 2026-06-22 — Git checkpoint — push to main
**Time:** (async checkpoint)
**User request:** Push accumulated work to origin/main and record checkpoint.
### Actions taken
- Pushed 6 commits (063f101a..934491e) to github.com:OF306PUC/LoRa-TestBed-Platform main:
  - 066a235 chore(security): untrack configuration.env + env templates
  - f08b232 refactor(structure): reorganise source into src/{common,gateway,node,tools}
  - 390e885 feat(config): unify radio config + move credentials to environment
  - 8aaf04d test(gateway): add unittest suite (38 tests)
  - f8002c3 feat(monitor): integrate Flask+SocketIO web dashboard into monorepo
  - 934491e docs(readme): sync README + add project-overview and session-log
- Ran `git rm --cached configuration.env`; confirmed .claude/ and CLAUDE.md are gitignored and NOT in the pushed tree.
### Decisions
- No PR, no tag for this push (direct to main, testbed internal repo).
### Outcomes
- Remote origin/main is now current with local main.
- configuration.env is untracked; env templates committed instead.
### Next steps / open questions
- Route to devops-lead: GitHub Dependabot flagged 4 vulnerability alerts on default branch (2 high, 2 moderate) — triage required.
- Still open: cosmetic node-label mismatch between monitor/app.py NODES dict and mesh_config.json.

## 2026-06-22 — Doc sync: src/ package reorganisation
**Time:** (session continuation)
**User request:** Update README.md and project-overview.md to reflect the src/ package reorganisation completed this session.
### Actions taken
- Rewrote README.md "Repository Structure" tree to reflect `src/{common,gateway,node,tools}/` layout, added `.env`/`.env.example` and `configuration.env.example`, removed non-existent files (`gateway/config/receiver_config.yaml`, `node/config.yaml`, `node/firmware_inital_config.yaml`).
- Updated all run commands in README.md to the new `python src/...` forms; marked `--port` as required for `receiver.py`.
- Fixed "Key Files" parameter values: preset changed from `MEDIUM_FAST` to `LONG_FAST`; telemetry interval from `300s` to `60s` (device & env); GPS update `300s`, GPS broadcast `600s`.
- Added "Configuration & Secrets" section describing `.env` / `configuration.env` split and `src/common/radio_config.py` as single source of truth.
- Added note that realtime dashboard lives in sibling repo `../LoRa-TestBed-Web`.
- Updated Current Status checklist with two new completed items (src/ reorganisation, env file migration).
- Fully replaced all `_TBD_` placeholders in `directives/project-overview.md`: identity, current status checklist, vision, architecture & stack (with pipeline diagram), key design decisions (CLIENT_MUTE, LOCAL_ONLY, single-source radio_config, --port required, sibling repo split), directory structure (new src/ layout), known constraints (SHT4X, usbPower, PSK history, configuration.env tracking, missing app.py, no license).
### Decisions
- README "Key Files" now leads with `src/common/radio_config.py` (new shared layer) rather than the old per-component param files.
- Removed emoji characters from README per studio style standards.
### Outcomes
- README.md and project-overview.md are current as of the src/ reorganisation.
### Next steps / open questions
- `git rm --cached configuration.env` before next commit (flagged for git-lead).
- `LoRa-TestBed-Web/app.py` missing — bring in or rebuild.

## 2026-06-22 — Cleanup & mejora del proyecto (Fases A+B)
**Time:** 14:30 (aprox)
**User request:** Ordenar y mejorar el proyecto; revisar arquitectura y proponer mejoras.
### Actions taken
- Revisión completa del código (gateway/, node/, infra). Veredicto: arquitectura base correcta; el trabajo es organización/config/calidad de datos.
- Descubierto y mapeado `LoRa-TestBed-Web/` (Flask + SocketIO + InfluxDB) → **movido a carpeta hermana** `../LoRa-TestBed-Web` (le falta `app.py`).
- Memoria creada: `data-contract-gateway-web` (contrato MQTT/InfluxDB invariante).
- **Fase A (quick wins):** `mesh_receiver.py` omite campos `None` en vez de `0.0` (arregla mapa en (0,0) y ceros falsos); `hop_taken` protegido contra `None`; `.gitignore` deja de ignorar `plot_history.py`; rutas a `mesh_config.json` resueltas con `Path(__file__)`.
- **Fase B (unificar config):** `radio_config.py` como fuente única de canal/región/preset/PSK importada por nodo y gateway; PSK movido a `.env` (+ `.env.example`); credenciales InfluxDB sacadas del `telegraf.conf` hardcodeado → `${ENV}` vía `env_file` (+ `configuration.env.example`).
### Decisions
- No tocar la arquitectura (correcta). PSK ya estaba en historial git (commit 63f101a) — rotar si se considera sensible (es clave de canal de testbed).
- Cambio de credenciales del pipeline EN VIVO verificado end-to-end (punto de test publicado, confirmado en InfluxDB, y borrado) — no rompió la ingesta.
### Outcomes
- Fases A+B completas y verificadas (`py_compile` OK; imports nodo/gateway comparten provablemente la misma PSK; pipeline sigue escribiendo).
- `configuration.env` sigue trackeado en git → requiere `git rm --cached` al commitear (git-lead).
### Next steps / open questions
- Confirmar layout exacto de Fase C (reorg a paquete `src/`) antes de ejecutarla.
- `LoRa-TestBed-Web/app.py` ausente — ¿se trae o se reconstruye?
- Pendiente: tests qa-tester sobre contrato de payload; fix de drift en README.

## 2026-06-22 — Project scaffolded
**Time:** 10:36 (aprox)
**User request:** Ran `/start` (team-session-start) on the LoRa TestBed Platform repo.
### Actions taken
- `/start` bootstrap detected the `.claude/` harness present but no project scaffold.
- Created the CLAUDE.md / AGENTS.md / GEMINI.md trio from the template.
- Created `directives/` (session-log.md, project-overview.md),
  `production/session-state/active.md`, `execution/`, `.tmp/`, and `memory/MEMORY.md`.
### Decisions
- Git repo already existed (`.git/` + `.gitignore`) — skipped `git init`.
### Outcomes
- Studio scaffold now present over an existing Python LoRa testbed codebase.
### Next steps / open questions
- doc-keeper to fill `project-overview.md` (currently placeholders) from the
  existing code (gateway/, node/, mqtt/, telegraf/, docker-compose, README).
