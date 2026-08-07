# Playbook de Despliegue y Experimentación — Meshtastic TestBed Platform (v2)

> Destino sugerido: `docs/operations/deployment-playbook.md`
> Aplica la metodología de *"How to Deploy a Reliable Meshtastic Network"* (V. Avramut, ene 2026) al testbed **San Joaquín**, extendida con la capa que al blog le falta: método experimental.
>
> **v2 — corrige v1.** La v1 asumió, a partir del README, que no existían métricas de enlace. Es falso: `mesh_receiver.py` publica `rssi`, `snr` y `hop` en los cuatro flujos, más `channel_util`, `air_util_tx`, `uptime_seconds`, `device_ts`/`received_at` y PDR por flujo vía `_pdr_fields()`. La brecha no está en la captura. Está en la **capa de consulta** y en el **contrato de experimento**.

---

## 0. Cómo leer la fuente

El artículo es un buen checklist de *priors* de ingeniería RF. Su tesis —"las mallas confiables se diseñan, no se tunean"— y el orden **RF primero, routing después** son correctos y transferibles.

Pero es un blog autopublicado, sin datos crudos ni metodología reproducible. Tres traducciones antes de aplicarlo:

| Lo que dice el blog | Problema | Traducción para este testbed |
|---|---|---|
| "SF9–SF10, BW 125 kHz" | Ese par no existe como preset Meshtastic. Los presets *Medium* usan **250 kHz** (`MEDIUM_FAST` = SF9/250, `MEDIUM_SLOW` = SF10/250) | Usar la tabla oficial de presets como espacio de diseño, no la recomendación literal |
| "Hop limit 2–3" | Genérico para redes regionales | Derivar el hop limit de la **matriz de adyacencia medida** |
| "Documenta todo / mide todo" | Es el Step 7, el penúltimo | Para un testbed es el bucle central, no un paso final |

**La diferencia de fondo:** el blog describe cómo operar una red. Tu output no es una red operando — es conocimiento transferible (a Rapa Nui, y eventualmente a un producto).

En este proyecto el Step 7 ya está en gran parte hecho. Eso cambia dónde está el cuello de botella: no en instrumentar, sino en poder **preguntarle cosas** a lo instrumentado.

---

## 1. Auditoría corregida

| Paso del blog | Estado real | Brecha | Prio |
|---|---|---|---|
| **1. Hardware** | SensCAP P1 Pro ×3 + LILYGO gw, solar, antena externa | Sin inventario RF por nodo | P2 |
| **2. Antenas** | Sin documentar | **Altura, ganancia, cable y conectores no registrados** — es la variable que más explicará la varianza del RSSI | **P0** |
| **3. Sitio / Fresnel** | `san-joaquin.md` casi todo *TBD* | Sin survey, sin LOS, sin piso de ruido | **P0** |
| **4. Topología** | Diagrama cadena node-1→2→3→gw, marcado *TBD* | **La topología es suposición, no medición.** Y ya tienes el dato para resolverlo: `hop` por paquete | **P0** |
| **5. Config** | `ANZ` ✅ + `LONG_FAST` + hop 3/3/2 + telemetría 60 s | Preset y hop limit no justificados por datos — pero ya son medibles | P1 |
| **6. Potencia** | `voltage`, `battery_level`, `uptime_seconds`, y re-anclaje de flujos por reboot antes de puntuar recepción | Falta razón de reset y alertas. El confound de downtime-como-pérdida ya está resuelto | P2 |
| **7. Validación** | `rssi`, `snr`, `hop`, PDR por flujo, latencia (`device_ts` vs `received_at`), detección de frames malformados | **La captura está bien. Lo capturado no es consultable ni comparable entre corridas** | **P0** |
| **8. Monitoreo** | InfluxDB + Telegraf + dashboard Flask | El dashboard muestra el sensor, no la salud de la red | P1 |

**Nota de conformidad:** `ANZ` es la región correcta para Chile según la tabla oficial de Meshtastic (915–928 MHz, referenciando normativa chilena). Coincide con la práctica de MeshChile. No es un error heredado.

---

## 2. Las brechas reales

### 2.1 `hop` es field, no tag → el corte fundamental está bloqueado

InfluxDB 1.x solo permite `GROUP BY` sobre tags y tiempo. Con `hop` como field, esta consulta —la más importante de un testbed de malla— hoy no se puede escribir:

```sql
SELECT mean(snr), count(snr) FROM mqtt_consumer GROUP BY node_id, hop
```

Cardinalidad de `hop`: 0–3. Costo de promoverlo a tag: nulo.

### 2.2 El RSSI está etiquetado por originador pero mide el último salto

En la API de Meshtastic, `packet['from']` es el emisor **original**; `rxRssi`/`rxSnr` describen el enlace **relay → gateway**. Para todo paquete con `hop > 0`, el tag `node_id` y la métrica RF se refieren a enlaces físicos distintos.

Consecuencia directa: `mean(rssi) GROUP BY node_id` promedia enlaces heterogéneos y no significa nada. **La calidad de enlace por nodo solo es válida con `hop = 0`.** Esto debe quedar como convención escrita, no como conocimiento tácito de quien escribió el receiver.

> Punto a confirmar en el caller: el comentario en `_handle_text_message` dice que `node_id` es *"the mesh node we heard it FROM (relay)"*. Si se está pasando `packet['from']`, es el originador y el comentario está invertido. Si se pasa `relay_node`, es solo el ID parcial de 1 byte, que no identifica unívocamente al relay. Cuál de los dos es cambia la interpretación de toda serie con `hop > 0`.

### 2.3 El flujo `message` se publica y se descarta

`publish_message()` emite el único dato con **numeración secuencial real** (`seq`, `src_id`, `fw_ver`) y con detección explícita de frames malformados. Es mejor estimador de pérdida que el PDR por cadencia, porque no depende de suponer que el nodo emitió cuando debía.

`telegraf.conf` no lo tiene suscrito. Llega al broker y muere ahí.

### 2.4 Colisión de timestamps a resolución de 1 segundo

La identidad de un punto en Influx es (measurement, tag set, timestamp). Con `json_time_format = "unix"`, dos paquetes del mismo `node_id` en el mismo tópico dentro del mismo segundo **se sobrescriben en silencio**. `pkt_id` no desambigua porque es field.

A 60 s de cadencia la tasa base es baja, pero el evento no es uniforme: se concentra en ráfagas post-reconexión y bajo congestión, que es exactamente el régimen que se quiere medir. La pérdida es sistemática, no aleatoria, y sesga a la baja justo los conteos de los momentos interesantes.

### 2.5 No hay `run_id`

Configuración en `radio_config.py` / `mesh_config.json`, datos en InfluxDB, nada que los una. Al cambiar el preset, todo el histórico queda ambiguo. Es lo único de la v1 que sobrevive intacto, y con la instrumentación buena que ya existe, es lo que separa "datos" de "experimentos comparables".

### 2.6 Una predicción para contrastar con tus propios datos

Time-on-air para `LONG_FAST` (SF11, BW 250 kHz, CR 4/5, preámbulo 16 símbolos, payload ~60 B):

```
Ts        = 2^11 / 250000     = 8.192 ms
Preámbulo = (16 + 4.25) × Ts  = 166 ms
Payload   = 63 símbolos × Ts  = 516 ms
ToA       ≈ 682 ms por paquete
```

Con device + environment cada 60 s y posición cada 600 s: ~130 paquetes originados/h/nodo, ×3 ≈ 390/h.

- **Si los tres nodos comparten dominio de colisión** (el gateway es `CLIENT_MUTE` y no retransmite; los otros dos sí): ~2 retransmisiones por paquete antes de que corte la supresión de duplicados → ~1170 tx/h × 0.682 s ≈ **796 s/h ≈ 22% de ocupación de canal**.
- **Si es una cadena real**, la retransmisión es necesaria y el número baja.

En la v1 esto era una estimación sin forma de verificarse. Ya no: **`channel_util` y `air_util_tx` están en tu telemetría de dispositivo**. La estimación es una predicción falsable, y contrastarla es una consulta, no un experimento.

```sql
SELECT mean(channel_util), mean(air_util_tx)
FROM mqtt_consumer WHERE time > now() - 24h GROUP BY node_label
```

Si `channel_util` ronda el 20%, la topología es dominio único y el hop limit está comprando congestión sin comprar alcance. Si está bajo 8%, la cadena es real y el hop limit está bien.

Comparación: `MEDIUM_FAST` (SF9/250) da ToA ≈ **201 ms** — 3.4× menos airtime — a cambio de 5 dB de link budget (153 → 148 dB). Con exponente de pérdida `n`, el factor de alcance es `10^(5/10n)`: 1.78× con n=2, 1.47× con n=3. Con el `n` ya ajustado del campus, es una decisión numérica.

---

## 3. Fase 0 — Cerrar la capa de análisis (días, no semanas)

### 3.1 Diff de `telegraf.conf`

```toml
[[inputs.mqtt_consumer]]
  servers = ["tcp://mosquitto:1883"]
  topics = [
    "meshtastic-testbed/+/position",
    "meshtastic-testbed/+/device",
    "meshtastic-testbed/+/environment",
    "meshtastic-testbed/+/message",        # (2.3) numeración secuencial real
  ]
  qos = 0
  connection_timeout = "30s"
  data_format = "json"
  json_time_key    = "received_at"
  json_time_format = "unix_ms"             # (2.4) requiere emitir ms en el receiver
  tag_keys = ["node_id", "node_label", "hop", "run_id"]   # (2.1) (2.5)
```

Notas:

- `topic` en `tag_keys` es no-op: Telegraf ya lo agrega vía `topic_tag`, cuyo default es `"topic"`.
- `unix_ms` exige que `received_at` se emita en milisegundos. Cambiar ambos lados a la vez; si no, los puntos aterrizan en 1970.
- **No promover `pkt_id`, `seq` ni `src_id` a tag.** Son de alta cardinalidad y harían explotar el índice de Influx 1.x. Como fields sirven para correlación puntual, que es su uso.
- Con `hop` y `run_id` como tags, la serie histórica previa queda con esos tags vacíos. Es esperable y es una razón más para marcar el corte con un `run_id` explícito desde el día uno.

### 3.2 Plomería del `run_id`

Fuente única en `src/common/radio_config.py` (junto al resto de la configuración de radio, que ya es la fuente de verdad), inyectado en cada payload de `mqtt_connector.py`. Un `run_id` describe una configuración estable: cambia cuando cambia cualquier factor bajo estudio, no cuando se reinicia el proceso.

### 3.3 Convención de análisis (escribirla en el repo)

```sql
-- Calidad de enlace: SOLO hop = 0. Con hop > 0 el tag node_id
-- y las métricas RF describen enlaces distintos (§2.2).
SELECT mean(snr), percentile(snr, 5), count(snr)
FROM mqtt_consumer
WHERE hop = '0' AND run_id = 'run-2026-08-A'
GROUP BY node_label, time(1h)

-- Topología efectiva: distribución de saltos por nodo
SELECT count(rssi) FROM mqtt_consumer
WHERE run_id = 'run-2026-08-A' GROUP BY node_label, hop
```

Esa segunda consulta es, sola, el Experimento 001: te dice si la cadena del diagrama existe.

### 3.4 Lo que sí falta capturar

Poco, y muy específico:

| Qué | Por qué | Dónde |
|---|---|---|
| **NeighborInfo** | Es lo único que da la **matriz de adyacencia completa** con SNR por enlace. `hop` da la topología *usada*; NeighborInfo da la *posible* | `src/node/configure_params.py`, intervalo ≥ 900 s (cuesta airtime — contabilizarlo) |
| **Sonda traceroute** | SNR por salto, ruta efectiva. La diferencia entre topología posible y efectiva es un resultado publicable | Job en el host del gw, cada 30 min: `meshtastic --port ... --traceroute '!xxxx'` |
| **Razón de reset** | `uptime_seconds` detecta el reboot; no dice por qué. Distingue brownout solar de watchdog | Extender `_note_uptime` |

### 3.5 Verificación

```sql
SELECT * FROM mqtt_consumer ORDER BY time DESC LIMIT 1
SHOW TAG KEYS FROM mqtt_consumer     -- debe listar hop y run_id
SHOW FIELD KEYS FROM mqtt_consumer   -- rssi, snr, pkt_id, seq deben estar aquí
```

---

## 4. El bucle de experimentación

```
   ┌──────────────────────────────────────────────────────┐
   │                                                      │
   ▼                                                      │
[1] HIPÓTESIS ──▶ [2] MANIFIESTO ──▶ [3] APLICAR CONFIG    │
  ¿Qué predice     experiments/       scripts + run_id     │
   el modelo?        NNN.yaml         tageado en Influx    │
                                            │             │
                                            ▼             │
                                     [4] CORRER           │
                                      ≥ 24 h, sin tocar   │
                                            │             │
   [6] DECISIÓN ◀── [5] ANÁLISIS ◀──────────┘             │
    adoptar /        predicho vs. medido                  │
    descartar             │                               │
        │                 ▼                               │
        │        [7] ACTUALIZAR MODELO ───────────────────┘
        │         (n, σ, umbral SNR) — el activo real
        ▼
   docs/testbeds/san-joaquin.md + CHANGELOG de sitio
```

### 4.1 Manifiesto de experimento

`experiments/002-preset-medium-vs-long.yaml`:

```yaml
run_id: run-2026-08-B
titulo: "MEDIUM_FAST vs LONG_FAST en topología de campus"
hipotesis: >
  Con la adyacencia medida en Exp-001, MEDIUM_FAST reduce channel_util >3x
  sin que el PDR caiga bajo 98%, porque el p05 de SNR medido excede los
  5 dB de link budget que se pierden.
prediccion_modelo:
  channel_util_pct: 6.5
  pdr_min_esperado: 0.98
factor_variado: lora.modem_preset
niveles: [LONG_FAST, MEDIUM_FAST]
diseno: crossover_diario_alternado
config_fija:
  region: ANZ
  hop_limit: <resultado Exp-001>
  telemetry_interval_s: 60
  rebroadcast_mode: LOCAL_ONLY
duracion_por_nivel_h: 96
kpis: [pdr_por_nodo, snr_p05_hop0, hops_p95, channel_util, latencia_p95]
criterio_decision: >
  Adoptar MEDIUM_FAST si PDR_min ≥ 0.98 Y channel_util baja ≥ 50%.
  Si el PDR cae bajo 0.95 en algún nodo, revertir y registrar ese nodo como
  limitado por enlace, no por congestión.
```

### 4.2 KPIs canónicos

Casi todos ya derivables de lo capturado:

| KPI | Fuente | Estado |
|---|---|---|
| `pdr_por_nodo` | `_pdr_fields()` por flujo | ✅ capturado |
| `snr_p05_hop0` | `percentile(snr,5) WHERE hop='0'` | ⚠️ requiere `hop` como tag |
| `hops_p95` | tag `hop` | ⚠️ requiere `hop` como tag |
| `channel_util` / `air_util_tx` | device metrics | ✅ capturado |
| `latencia_p95` | `received_at − device_ts` | ✅ capturado |
| `uptime_nodo` | `uptime_seconds` + re-anclaje | ✅ capturado |
| `pdr_secuencial` | `seq` del flujo `message` | ⚠️ requiere suscribir el tópico |
| `snr_por_enlace` | NeighborInfo | ❌ falta capturar |

El p05 importa más que la media: el margen del **peor caso** es lo que rompe enlaces. Por eso el receiver debe seguir publicando por paquete y no pre-agregar — cualquier agregación en origen destruye la cola de la distribución.

### 4.3 Disciplina estadística

1. **Confusión temporal.** El entorno RF del campus varía día/noche y semana/fin de semana. Comparar "semana 1 = A" vs. "semana 2 = B" mide el clima y la ocupación, no la configuración. **Crossover: alternar A/B día por día**, mínimo 4 días por nivel.

2. **Duración mínima.** A 60 s, 24 h dan 1440 muestras/nodo → resolución de PDR ~0.07%. 24 h es el piso para cubrir el ciclo diurno, relevante además porque los nodos son solares.

3. **Topologías virtuales.** La configuración LoRa de Meshtastic incluye el array **`ignore_incoming`**, documentado explícitamente para forzar que un nodo nunca escuche a otros, simulando que están fuera de alcance. Permite construir cadenas, estrellas y particiones por software sobre el mismo hardware, con la topología como variable controlada. Para estudiar escalabilidad en un campus compacto, es la herramienta más importante de esta lista.

### 4.4 Estimación insesgada de σ (esto lo habilita tener PDR)

El SNR observado está truncado por abajo en el umbral de demodulación θ: solo se ven los paquetes que sobrevivieron. Estimar σ del shadowing con la muestra observada lo subestima sistemáticamente.

Con PDR tienes el segundo observable, y el sistema se cierra. Sea `p` el PDR medido en un enlace y `m̄` la media del SNR observado en ese enlace (con `hop = 0`):

```
α̂ = Φ⁻¹(1 − p)                    # cuantil normal estándar
λ(α) = φ(α) / (1 − Φ(α))           # inverse Mills ratio
σ̂ = (m̄ − θ) / (λ(α̂) − α̂)
μ̂ = θ − σ̂·α̂
```

**Caveat que decide la validez:** esto supone que toda la pérdida viene de que el shadowing cruza el umbral. Las colisiones producen pérdida **independiente del SNR** e inflan la truncación aparente, sesgando σ̂ al alza. Por eso la corrida de calibración debe hacerse en régimen de baja carga (`channel_util` bajo), o separando pérdida por colisión de pérdida por enlace. Es otra razón para resolver §2.6 primero.

Esto es lo que alimenta el modelo a transferir a Rapa Nui.

### 4.5 Backlog de experimentos

| ID | Pregunta | Bloquea a |
|---|---|---|
| **001** | ¿Cuál es la topología efectiva y la adyacencia real? *(consulta + NeighborInfo)* | Todo |
| **002** | ¿Cuál es el preset óptimo dado el margen medido? | 004 |
| **003** | ¿Hop limit mínimo con PDR ≥ 98%? | 004 |
| **004** | ¿Cuánto sube `channel_util` por nodo agregado? *(con `ignore_incoming` para emular N > 3)* | Roadmap de producto |
| **005** | ¿Cuánto cambia el PDR entre día/noche y estación? | Modelo de sitio |
| **006** | ¿El modelo calibrado en San Joaquín predice los enlaces de Rapa Nui? | **Es la tesis** |
| **007** | Autonomía solar: días sin sol antes de caída | Spec de producto |

---

## 5. Cerrar las brechas P0 de RF

Antes de la primera corrida formal, completar `docs/testbeds/san-joaquin.md`:

- **Inventario RF por nodo:** modelo de antena, ganancia (dBi), tipo y largo de cable, conectores, **altura sobre el suelo y sobre la línea de techo**. El punto del blog sobre altura > ganancia es correcto, y es la variable que más va a explicar la varianza de tus datos de RSSI — que ya tienes, pero hoy no puedes correlacionar con nada geométrico.
- **Coordenadas y distancias inter-nodo** — extraíbles del GPS que ya se captura.
- **LOS y despeje de Fresnel:** `r = 17.32 × √(d / (4f))`, d en km, f en GHz. A 915 MHz y 300 m, el radio de la primera zona de Fresnel en el punto medio es ~4.8 m. En un campus con edificios eso rara vez se cumple sin altura de techo. Documentar el despeje estimado por enlace.
- **Piso de ruido:** con SDR o analizador, medir 915–928 MHz en cada punto. Un campus universitario tiene piso de ruido alto y desigual, y explica outliers que de otro modo parecen aleatorios.

---

## 6. De testbed a producto

El producto no es la malla — Meshtastic es abierto y la malla es commodity. Lo defendible son tres artefactos que solo un testbed instrumentado produce:

**(a) Un modelo de sitio calibrado.** Exponente de pérdida, σ de shadowing y umbral de SNR ajustados con datos reales (§4.4), que convierten un site survey en topología predicha con probabilidad de enlace. La versión simulada existe; falta la validación empírica y la prueba de transferencia (Exp-006). Un modelo que predice enlaces en dos entornos radicalmente distintos —campus urbano e isla— es un activo real.

**(b) Un nodo endurecido con números.** "PDR ≥ 98% a X m con antena a Y m, Z días de autonomía sin sol." Cada número sale de un experimento del backlog.

**(c) Un runbook de operación.** El Step 8 del blog, hecho ejecutable: auditorías de SNR, repuestos, versionado de firmware, alertas por SLO.

### Cambios de arquitectura que exige el salto

| Ítem | Hoy | Producto |
|---|---|---|
| Claves | Un PSK compartido en `.env` | PKC por nodo + admin key (Meshtastic ≥2.5); rotación sin visita a terreno |
| Sitios | Directorio `docs/testbeds/` | Abstracción de sitio en código: config, inventario y datos particionados |
| Provisioning | Scripts manuales por USB | Config declarativa versionada + verificación post-aplicación |
| Firmware | Drag-and-drop | OTA + rollback (Fase 3 del roadmap) |
| Backhaul | Desktop por USB | RPi + 5G HAT con buffer local ante caída de enlace |
| Datos | InfluxDB local | Contrato de datos, retención, multi-tenancy |
| Salud | Dashboard de sensores | Dashboard de red + SLOs con presupuesto de error |

La migración de PSK compartido a claves por nodo conviene planificarla antes, no después: es la más difícil de retrofitear sobre flota desplegada.

---

## 7. Plan de 90 días

| Semanas | Foco | Entregable verificable |
|---|---|---|
| 1 | §3.1–§3.3: tags, tópico `message`, `run_id`, timestamps ms | `SHOW TAG KEYS` lista `hop` y `run_id`; consultas de §3.3 corren |
| 1 | **Exp-001a**: distribución de saltos + `channel_util` real vs. predicción §2.6 | Se sabe si la topología es cadena o dominio único |
| 2 | §3.4: NeighborInfo + sonda traceroute | Matriz de adyacencia medida vs. Monte Carlo predicho |
| 3 | Site survey (§5) | `san-joaquin.md` sin campos *TBD* |
| 4–5 | Exp-003 (hop limit) → Exp-002 (preset) | Config base justificada por datos |
| 6–7 | Exp-004 con `ignore_incoming` | Curva `channel_util` vs. N → límite de escalabilidad |
| 8–9 | Calibración de σ (§4.4) en régimen de baja carga | Estimador insesgado; incertidumbre del modelo cuantificada |
| 10–11 | Exp-007 (energía) + fix I2C/SHT4X (Fase 2 roadmap) | Autonomía medida |
| 12 | Link budget Rapa Nui | Topología predicha para el segundo sitio con intervalos de confianza |

La v1 estimaba dos semanas de instrumentación. Con lo que ya está construido, la Fase 0 es una semana y en gran parte configuración, no código. El plan se corre hacia adelante.

**Regla de gobernanza:** todo experimento cerrado actualiza tres cosas — el `CHANGELOG` del sitio, los parámetros del modelo, y el manifiesto con el resultado real junto a la predicción. Esa tercera parte es la que hace que el modelo mejore en vez de solo acumular datos.

---

## Referencias

- V. Avramut, *How to Deploy a Reliable Meshtastic Network* (ene 2026) — fuente base
- Meshtastic, *Radio Settings* — presets, SF/BW/CR, link budget
- Meshtastic, *LoRa Region by Country* — confirma `ANZ` para Chile
- Meshtastic, *LoRa Configuration* — `ignore_incoming`, `tx_enabled`
- Meshtastic, *Mesh Algorithm* — flooding gestionado y supresión de duplicados
- Greene, *Econometric Analysis* — estimación en distribuciones truncadas (§4.4)
- MeshChile — práctica local en 915–928 MHz
