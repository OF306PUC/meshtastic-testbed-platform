# Estimador de PDR por cadencia (`CadencePdrTracker`)

> **Nota de idioma:** este documento está deliberadamente en español (el resto
> de los artefactos del repo está en inglés por portabilidad). Es la explicación
> de referencia de la lógica de medición; el código vive en
> `src/gateway/mesh_receiver.py` y la configuración en `mesh_config.json`.

---

## 1. Qué problema resuelve

Queremos el **PDR (packet delivery ratio)** de cada flujo de la malla: qué
fracción de los paquetes que un nodo *debió* emitir efectivamente llegó al
gateway. El problema es que **Meshtastic no expone un número de secuencia de
aplicación** en telemetría ni posición: el `packet.id` es aleatorio por paquete,
no un contador, así que no se puede detectar un hueco comparando secuencias
consecutivas.

Lo que sí tenemos es que **la cadencia de emisión es conocida y la fijamos
nosotros**: `telemetry.device_update_interval`,
`telemetry.environment_update_interval` y `position.position_broadcast_secs` se
provisionan desde `mesh_config.json` (`src/node/configure.py`,
`src/pbx/configure.py`). Si un nodo está configurado para emitir cada `T`
segundos y pasan `3T` entre dos recepciones, faltaron ~2 paquetes.

Eso es todo el estimador: **las pérdidas se infieren de los huecos entre
llegadas contra la cadencia nominal `T`**.

---

## 2. Definiciones

- **Flujo (`flow`)**: par `(node_label, kind)` con
  `kind ∈ {"device", "environment", "position"}`. Cada flujo tiene su propio
  `T`, su propio estado y su propio PDR. `node-1/position` y `node-1/device` son
  mediciones independientes.
- **`T` (`interval`)**: cadencia nominal del flujo, en segundos, tomada de
  `mesh_config.json → nodes_cfg[node].intervals[kind]`.
- **Slot**: una ranura de tiempo de largo `T`. Cada slot vale `1` si llegó el
  paquete y `0` si se infirió pérdida.
- **`dt`**: tiempo entre la recepción actual y la anterior del *mismo* flujo,
  medido con `time.monotonic()`.

---

## 3. El algoritmo, en una línea

En cada recepción "en horario":

```python
slots  = round(dt / T)          # cuántos intervalos nominales abarca el hueco
missed = max(0, slots - 1)      # uno de esos slots es el paquete que sí llegó
```

### ¿Por qué `round()` y no `floor()`?

El jitter del firmware es aproximadamente **simétrico alrededor de `T`**: un
paquete puede salir a `T - 3 s` o a `T + 4 s` según congestión del canal y
scheduling interno. Con `floor()`, cualquier paquete que llegue un segundo tarde
(`dt = 1.01·T`) daría `slots = 1 → missed = 0`… pero `dt = 1.99·T`, que también
es *un solo* intervalo con retardo, daría `missed = 0` igual, y en cambio
`dt = 2.01·T` cobraría 1. `round()` parte el hueco por la mitad: se cobra
pérdida cuando el retardo pasa de `1.5·T`, que es el punto de decisión correcto
si el ruido está centrado en `T`.

Consecuencia asumida: **el estimador tiene ±1 paquete de error por hueco**. `T`
es nominal, no exacto — el firmware difiere emisiones bajo congestión.

---

## 4. Los tres caminos de `observe()`

`observe(flow, interval, window_sec, now)` es el único punto de entrada por
recepción. Distingue tres casos:

### 4.1 Primer paquete del flujo (`_new_flow`)

Se crea el estado, se ancla la grilla en `now` y se devuelve `pdr = None`.
**Un solo paquete no contiene información de entrega**: reportar `1.0` ahí sería
mentir. `pdr` sigue siendo `None` hasta que exista al menos un hueco observado
(`rx + missed >= 2`).

### 4.2 Llegada dentro de un mismo intervalo (`slots == 0`)

El paquete llegó *antes* de que la grilla lo esperara. El modelo de cadencia no
predice esto, así que:

- se cuenta en `early_count` **aparte**, no como recepción normal,
- **no** ocupa slot en la ventana,
- y, crítico: **no se mueve el ancla `last`**.

Lo último es lo que evita que la grilla derive. Si adelantáramos `last` a este
paquete fuera de horario, el siguiente paquete *en horario* también se vería
"temprano", y el error se propagaría indefinidamente.

`early_count > 0` (expuesto como `cadence_violated`) es **la señal de que la
hipótesis de cadencia fija está rota**. La causa habitual es
`position_broadcast_smart_enabled = true`, que agrega emisiones de posición
disparadas por movimiento encima del temporizador periódico — por eso las
rutinas de provisioning ahora lo apagan explícitamente.

> Nota importante: con este estimador **`pdr > 1` es imposible** por
> construcción (`missed >= 0`). Por eso la señal de cadencia violada tiene que
> ser `early_count` y no un PDR mayor que 1.

### 4.3 Llegada en horario (`slots >= 1`)

```python
missed_now   = max(0, slots - 1 - f["charged"])
f["rx"]     += 1
f["missed"] += missed_now
f["last"]    = now
f["charged"] = 0
f["recent"].extend([0] * missed_now)
f["recent"].append(1)
```

Se restan las pérdidas que el `sweep()` ya cobró para este mismo hueco
(`charged`) — ver §5 — y se anota la secuencia en la ventana: primero los ceros
de los slots vacíos, después el `1` del paquete que llegó. Ese orden importa:
mantiene la ventana como una **traza temporal ordenada**, no un simple contador.

---

## 5. `sweep()` — el detector de silencio

Un estimador puramente *reception-driven* tiene un sesgo grave: **un nodo que se
muere congela su PDR en el último valor bueno** y nunca vuelve a actualizarse,
porque nunca más llega el paquete que dispararía el cálculo. Eso es sesgo de
supervivencia — exactamente lo que la medición existe para detectar.

`sweep(now)` se ejecuta periódicamente desde el loop de `listen()` (cada
`sweep_interval_sec`, ver §7) y recorre todos los flujos:

```python
due = max(0, round((now - f["last"]) / f["interval"]) - 1)
if due > f["charged"]:
    delta        = due - f["charged"]
    f["missed"] += delta
    f["charged"] = due          # queda registrado lo ya facturado
    f["recent"].extend([0] * delta)
```

`charged` es el mecanismo anti-doble-conteo: registra cuántas pérdidas se
facturaron **provisionalmente** para el hueco en curso, de modo que cuando el
paquete finalmente llegue, `observe()` sólo cobre la diferencia (§4.3) y no todo
el hueco otra vez.

Los snapshots que `sweep()` devuelve se publican al topic `.../pdr` con
`"source": "sweep"` — marcados así porque **no hay un paquete detrás**: son
pérdidas inferidas en silencio, y conviene poder distinguirlas en el análisis
posterior de las que acompañan a una recepción real.

---

## 6. `reanchor()` — reboots no son pérdidas de radio

Un nodo apagado o reiniciado infla `missed`: el hueco existió, pero **no fue
culpa del enlace**. Cobrarlo como pérdida de radio contamina la métrica.

La detección se hace desde `deviceMetrics.uptimeSeconds`: si el uptime
**decrece** respecto al anterior, hubo reboot (`_note_uptime`). Como
`uptimeSeconds` sólo viaja en telemetría de dispositivo pero la caída afectó a
*todos* los flujos del nodo, el re-anclaje se aplica **a nivel nodo**, a todos
sus `kinds`.

`reanchor(flow, now)` hace tres cosas:

1. **Devuelve** las pérdidas provisionales del hueco actual
   (`missed -= charged`) y quita los ceros que `sweep()` había puesto en la cola
   de la ventana.
2. Mueve `last = now`, para que `sweep()` deje de facturar el downtime.
3. Levanta el flag `restart = True`.

### El flag `restart` (bug real encontrado en tests)

Sin el flag, `reanchor()` dejaba `last = now` y **la propia recepción que
reportaba el reboot** medía `dt ≈ 0`, cayendo en el camino `slots == 0` y
clasificándose como *early* — es decir, un reboot se reportaba como violación de
cadencia. Con `restart = True`, la primera recepción posterior al reboot se
trata como **inicio de una grilla nueva**: cuenta como recepción, ancla la
grilla y no se juzga ni temprana ni tardía, sea cual sea su `dt`. Hay test de
regresión para esto en `tests/test_pdr_tracker.py`.

---

## 7. Los dos parámetros de `pdr_cfg` (no confundirlos)

```json
"pdr_cfg": {
    "sweep_interval_sec": 30,
    "window_sec": {"device": 3600, "environment": 3600, "position": 3600}
}
```

### `window_sec` — resolución **estadística**

Largo de la ventana rodante, en segundos, por tipo de flujo. Se traduce a slots
al crear el flujo:

```python
slots = max(MIN_WINDOW_SLOTS, round(window_sec / interval))   # MIN_WINDOW_SLOTS = 3
```

El truco central: la ventana es un `collections.deque(maxlen=slots)` y **no
guarda un solo timestamp**. Como las pérdidas se empujan como `0` y las
recepciones como `1`, y hay exactamente un slot por intervalo nominal,
`window_sec / T` slots **es** una ventana temporal. Sin bookkeeping de tiempos,
sin purga por antigüedad: el `maxlen` del deque hace el trabajo.

De ahí salen `pdr_window` (el PDR reciente), `pdr_window_slots` (el largo
nominal) y `pdr_window_filled` (cuántos slots hay realmente) — este último está
expuesto a propósito para que **un denominador flaco se vea en los datos** en
vez de quedar escondido detrás de un porcentaje.

Densidad con la config actual:

| flujo | `T` | slots = 3600/`T` |
|---|---|---|
| nodos 1-3 · device / environment | 120 s | 30 |
| nodos 1-3 · position | 600 s | 6 |
| p1 / p2 · device | 900 s | 4 |
| p1 / p2 · position | 1800 s | 2 → **clampeado a 3** |

Los flujos de los proxies están en el piso de la ventana: `pdr_window` ahí salta
en escalones de 1/3, o sea es prácticamente ruido. `window_sec` es por *tipo* y
no por nodo, así que la palanca disponible es subir la ventana de `position`
(p. ej. a 21600 s = 6 h → 36 slots para los nodos, 12 para los proxies).

### `sweep_interval_sec` — resolución **temporal de detección**

Cada cuánto el loop de `listen()` llama a `sweep()`. **No es la ventana** y no
afecta ningún número reportado; determina *cuándo se enteran* de una pérdida
mientras el flujo está callado, y cuándo se publica al topic `.../pdr`.

Debe ser **bastante menor que el `T` más chico** (30 s vs 120 s hoy) para que
una pérdida se detecte a una fracción de cadencia. Si fuera mayor que `T`, la
latencia de detección la fijaría el sweep en vez de la radio.

### Cómo se relacionan

Son ortogonales en unidades, y se tocan sólo a través de `T`: **`sweep()` es lo
que hace avanzar la ventana cuando no llega nada**. Sin sweep, el deque de un
nodo caído nunca recibe ceros nuevos y `pdr_window` se queda clavado.
`window_sec` define el largo de la ventana; `sweep_interval_sec` define quién la
empuja en ausencia de tráfico.

Regla mnemotécnica: **`window_sec` = cuánto pasado mido; `sweep_interval_sec` =
cada cuánto reviso el silencio.**

---

## 8. Qué se publica

Cada snapshot (`_snapshot`) contiene:

| campo | significado |
|---|---|
| `pdr` | PDR acumulado del flujo: `rx / (rx + missed)`. `None` con menos de 2 slots. |
| `pdr_window` | PDR de la ventana rodante: `sum(deque) / len(deque)`. |
| `pdr_window_slots` | Largo nominal de la ventana en slots. |
| `pdr_window_filled` | Slots realmente presentes (para juzgar el denominador). |
| `rx_count` | Recepciones en horario. |
| `missed_est` | Pérdidas inferidas acumuladas. |
| `missed_now` | Pérdidas cobradas en este evento (para correlacionar con RSSI/SNR). |
| `early_count` | Recepciones fuera de cadencia. |
| `cadence_violated` | `early_count > 0`. |
| `gap_s` | `dt` observado, en segundos. |

**Ruta de salida:** los snapshots que acompañan a una recepción viajan *dentro*
de los payloads existentes de `device` / `environment` / `position` — es aditivo,
el contrato de datos con `monitor/` queda intacto. Los cobros hechos en silencio
por `sweep()` van al topic nuevo `.../pdr` con `"source": "sweep"`.

---

## 9. Decisiones de diseño y sus razones

- **Huecos entre llegadas, no conteo por ventana fija.** Un conteo tipo
  "recibí 8 de los 10 que esperaba en la última hora" da el mismo promedio pero
  pierde información: el enfoque por huecos **atribuye cada pérdida a un
  instante concreto**, lo que permite correlacionarla con el RSSI/SNR de los
  paquetes vecinos, da la distribución de rachas de pérdida gratis, y permite
  excluir un hueco específico cuando se sabe que no fue el radio (§6).

- **Reloj monotónico para todos los `dt`.** `time.monotonic()`, nunca
  `time.time()`. Un salto de NTP en el host habría fabricado pérdidas en todos
  los flujos simultáneamente. `received_at` sí queda en wall-clock, porque
  Telegraf lo usa como *time key* de InfluxDB.

- **Dedup de `packet.id` es parte de la medición, no un detalle.** Un
  rebroadcast contado como segunda recepción infla el ratio. El deque
  `seen_ids` (`SEEN_MAX = 200`) cubre ~20 min a las cadencias del testbed, mucho
  más que los segundos que tarda en llegar un rebroadcast.

- **Cadencias autoritativas y completas por nodo.** Si un nodo no declara un
  `kind` en `intervals`, significa "este nodo no lo emite" y **no se le mide
  PDR** para ese flujo — no se rellena con defaults. Los nodos con PBX no
  emiten `environment`; medirlos contra una cadencia que nunca tuvieron
  fabricaría pérdidas del 100 %.

- **Rechazado:** bitmap de ventana deslizante por flujo para tolerar
  reordenamiento de paquetes. Innecesario a estas tasas de emisión; anotado en
  el docstring por si aparece en campo.

- **Los mensajes del PBX no aportan PDR.** No tienen cadencia contra la cual
  medir. Se capturan y publican (metadata; el contenido sólo con
  `capture_content=True`), y el PDR a nivel mensaje queda pendiente de que el
  firmware del PBX emita el contador `seq` — el parser ya lo soporta detrás del
  flag `FRAME_HAS_SEQ`.

---

## 10. Limitaciones, dichas a propósito

1. **±1 paquete por hueco.** `T` es nominal; el firmware difiere emisiones bajo
   congestión del canal.
2. **`pdr` nunca puede exceder 1.0.** La señal de modelo roto es `early_count`,
   no un PDR > 1.
3. **Un nodo apagado infla `missed`** hasta que se detecta el reboot vía
   `uptimeSeconds` y se re-ancla. Un nodo que se apaga y **no vuelve** queda
   correctamente contado como pérdida total — eso es intencional.
4. **`pdr` es `None` con un solo paquete.** No hay información de entrega en una
   sola muestra.
5. **La ventana de los flujos de PBX es demasiado corta** con `window_sec`
   uniforme de 3600 s (§7). Está visible en `pdr_window_slots`; la corrección es
   un cambio de configuración, no de código.

---

## Referencias

- Implementación: `src/gateway/mesh_receiver.py` (`CadencePdrTracker`,
  `MeshReceiver._pdr_fields`, `_note_uptime`, `_run_sweep`)
- Configuración y lector: `mesh_config.json`, `src/common/mesh_config.py`
- Tests: `tests/test_pdr_tracker.py`, `tests/test_mesh_receiver.py`
- Bitácora de la sesión de diseño: `docs/session-log.md` → entrada 2026-07-31
