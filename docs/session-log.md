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

## 2026-07-15 — Integración de las configuraciones del proxy BLE
**Time:** (aprox)
**User request:** "Incluir las configuraciones del proxy" (con preguntas de
clarificación). Respuestas del owner: integrar `src/proxy/` al esquema común
(`radio_config.py`), registrarlo en `mesh_config.json`, completar `.env.example`;
canales = CPS_RTC + PUC_NET compartidos; PSKs desde `.env`; arreglar de paso los
imports rotos de node/gateway. El proxy es el diseñado en `../meshtastic-ble-proxy`
(firmware nRF52840 que multiplexa 6 teléfonos BLE sobre un nodo Meshtastic vía
UART). Hay **dos pares nordic-lilygo** → entradas `p1` y `p2`.
### Actions taken
- `src/proxy/configure_params.py` reescrito al patrón node/gateway: importa de
  `common/radio_config.py` (CPS_RTC idx 0 + PUC_NET idx 1, región/preset/
  rebroadcast); conserva lo específico del nodo-proxy (BT off, serial PROTO
  GPIO15/35 @115200, telemetría 900 s, GPS 1800 s, rol CLIENT, hop 2+1).
  Eliminados `EasterNet`/`NUM_CHANNELS=8` (venían de otra red).
- `src/proxy/configure.py`: fix import roto (`param_node` → `configure_params`),
  eliminado el mecanismo `channel_psks.txt` (generación local de 8 llaves);
  ahora configura exactamente 2 canales con PSKs del `.env` (decodifica
  `base64:` y escribe bytes vía API Python — se conserva el workaround del bug
  str/bytes del CLI). Falla temprano (exit 2) si falta `LORA_MSG_CHANNEL_PSK`.
- `src/proxy/__init__.py` creado (paquete importable como node/gateway).
- Imports rotos por el rename de `radio_config.py` arreglados:
  `src/node/configure_params.py` + `configure.py`,
  `src/gateway/configure_params.py` + `configure.py` → `CHANNEL_TELEMETRY_*`.
  `node/configure_params.py` ahora también importa `REBROADCAST_MODE` de common
  (antes duplicado local).
- `mesh_config.json`: entradas `p1`/`p2` (nodos lilygo de los dos proxies) con
  id placeholder `!CHANGE_ME_P1/P2`, hop_limit 3, rol CLIENT.
- `.env.example`: renombrado documentado + agregado `LORA_MSG_CHANNEL_PSK`.
- Verificación: `py_compile` OK en los 8 archivos; smoke test de imports OK
  (CPS_RTC/PUC_NET/LONG_TURBO consistentes entre node/gateway/proxy);
  `tests/test_load_nodes.py` 10/10 OK con las entradas nuevas.
### Decisions
- Proxy usa los mismos 2 canales compartidos y PSKs desde `.env` (fuente única);
  `channel_psks.txt` descartado.
- `fetch_node_config.py` (herramienta de medición del config burst para
  CONFIG_CACHE_ARENA_BYTES del firmware) se deja tal cual — es standalone.
- **PUC_NET es mesh-wide** (decisión del owner, 2ª iteración): nodos 1–3 y
  gateway también configuran el canal 1, para que `LOCAL_ONLY` retransmita los
  mensajes de los teléfonos a través de la malla. `node/configure.py` y
  `gateway/configure.py` agregan `--ch-add PUC_NET` + PSK (idx 1) y fallan
  temprano si falta `LORA_MSG_CHANNEL_PSK`.
- **Hop limits por proxy** (owner): p1 = 1 (el owner dijo "0 ó 1"; se eligió 1 —
  llega directo igual y tolera un relevo), p2 = 3. Como difieren,
  `proxy/configure.py` ahora recibe `--node-id p1|p2` y lee
  `hop_limit`/`device_role` desde `mesh_config.json` (mismo patrón que
  `node/configure.py`); se quitaron HOP_LIMIT/DEVICE_ROLE de los params del proxy.
### Outcomes
- `src/proxy/` integrado al esquema común; PUC_NET configurado mesh-wide; todo
  el árbol compila e importa (smoke test OK, test_load_nodes 10/10).
  Nada commiteado (protocolo: espera aprobación de git-lead).
### Next steps / open questions
- Owner: reemplazar `!CHANGE_ME_P1/P2` en `mesh_config.json` con los node ids
  reales de los lilygo cuando estén conectados.
- Owner: generar y setear `LORA_MSG_CHANNEL_PSK` en `.env`; luego re-correr
  `configure.py` en nodos 1–3 + gateway (nuevo canal 1) y en p1/p2
  (`--node-id p1 --port ...`).
- Git checkpoint pendiente: ADR-0001 + este trabajo del proxy sin commitear.

## 2026-07-10 — /start reconciliation + ADR-0001 (canopy sensor I2C link)
**Time:** (aprox)
**User request:** `/start`; luego consulta de ingeniería sobre el sensor SHT4X que
debe ir dentro del dosel de la vid mientras el nodo solar queda 1-3 m por encima —
duda sobre si I2C es adecuado para ese tramo. Pidió (b) abrir un ADR, con el
requisito duro de no tocar el firmware.
### Actions taken
- `/start` briefing: verificado que el rename de namespace YA está commiteado
  (`fae668d`), árbol limpio. `active.md` estaba desactualizado (decía "NOT
  committed") → reconciliado.
- Consulta de ingeniería: explicado por qué I2C no sobrevive 1-3 m (presupuesto de
  capacitancia de bus = 400 pF; cable ~50-100 pF/m + Grove Hub lo revienta → ACK de
  1 bit pasa pero la lectura multi-byte falla, mismo síntoma ya documentado).
- Creado **ADR-0001** (`docs/architecture/ADR-0001-canopy-sensor-i2c-link.md`).
- Reconciliado `.claude/production/session-state/active.md` (rename commiteado, árbol
  limpio, `execution/` eliminado, pendientes reorganizados) y agregada esta entrada.
### Decisions
- **ADR-0001 = extensor I2C diferencial** (par PCA9615 / fallback P82B96): mantiene el
  SHT4X como esclavo I2C transparente en `0x44`, así el módulo Environmental Telemetry
  de Meshtastic lo lee **sin tocar firmware** (requisito duro del owner). Cable twisted
  pair (Cat5/6) con SDA±/SCL± + 3.3 V + GND; quitar el Grove I2C Hub; bus a 100 kHz.
- Rechazadas: I2C crudo/lento (frágil), MCU satélite (rompe la transparencia de
  firmware; electrónica alimentada en la sombra), RS-485/SDI-12 (no lo lee Meshtastic
  nativamente → firmware custom, diferido a Phase 2/3).
### Outcomes
- ADR-0001 en su lugar (primer ADR del repo). `active.md` y `session-log` al día.
  Nada commiteado esta sesión (posture working-tree hasta que el owner apruebe git-lead).
### Next steps / open questions
- doc-keeper: referenciar ADR-0001 desde project-overview.md § Known constraints.
- Owner: conseguir breakouts PCA9615/P82B96; confirmar pinout I2C del Grove del P1 Pro;
  bench-test al largo de cable objetivo antes del despliegue.
- Pendientes de fondo sin cambios: Dependabot (2 high, 2 moderate), node-label mismatch,
  license, scalability + power, RPi+5G.

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
