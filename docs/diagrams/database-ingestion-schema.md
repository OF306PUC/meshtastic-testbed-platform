# Ingesta y esquema de la base

> Diagrama vivo. Editar el bloque Mermaid, no exportar a imagen.
>
> Refleja el estado posterior al 2026-08-06, cuando se reparó la ingesta de los
> tópicos `message` y `pdr` (publicados desde el 07-31, consumidos por nadie).

## Cómo llegan los datos

Desde el paquete en el aire hasta la fila en InfluxDB. El `portnum` decide qué
handler lo procesa, el handler decide a qué tópico va, y el bloque de Telegraf
decide en qué measurement aterriza.

```mermaid
flowchart LR
    subgraph malla["Malla LoRa"]
        A1["TELEMETRY_APP<br/>67"]
        A2["POSITION_APP<br/>3"]
        A3["PRIVATE_APP<br/>256"]
        A4["TEXT_MESSAGE_APP<br/>1"]
    end

    subgraph recv["mesh_receiver.py"]
        H1["_handle_device_telemetry"]
        H2["_handle_env_telemetry"]
        H3["_handle_position"]
        H4["_handle_text_message"]
        H5["_run_sweep<br/>sin paquete detrás"]
    end

    subgraph topics["MQTT topics"]
        T1["device"]
        T2["environment"]
        T3["position"]
        T4["message"]
        T5["pdr"]
    end

    subgraph tg["telegraf.conf"]
        B1["bloque 1<br/>sin name_override"]
        B2["bloque 2<br/>pbx_message"]
        B3["bloque 3<br/>pdr"]
    end

    subgraph db["InfluxDB"]
        M1[("telemetry")]
        M2[("pbx_message")]
        M3[("pdr")]
    end

    MON["monitor/<br/>Flask + SocketIO"]

    A1 --> H1
    A1 --> H2
    A2 --> H3
    A3 --> H4
    A4 --> H4

    H1 --> T1
    H2 --> T2
    H3 --> T3
    H4 --> T4
    H5 --> T5

    T1 --> B1
    T2 --> B1
    T3 --> B1
    T4 --> B2
    T5 --> B3

    B1 --> M1
    B2 --> M2
    B3 --> M3
    M1 --> MON
```

Dos cosas que el diagrama hace explícitas:

- **`_run_sweep` no tiene paquete de entrada.** Es el detector de silencio: sin
  él un nodo muerto congela su último PDR para siempre. Es la única rama que
  nace del reloj y no de la malla.
- **`monitor/` sólo lee `telemetry`.** Las otras dos measurements todavía no
  tienen consumidor en el dashboard.

## El esquema

```mermaid
erDiagram
    telemetry ||--o{ pdr : "node_label"
    telemetry ||--o{ pbx_message : "node_label"

    telemetry {
        tag node_id
        tag node_label
        tag topic
        field temperature "environment"
        field humidity "environment"
        field battery_level "device"
        field voltage "device"
        field channel_util "device"
        field air_util_tx "device"
        field uptime_seconds "device"
        field latitude "position"
        field longitude "position"
        field altitude "position"
        field rssi "los tres tópicos"
        field snr "los tres tópicos"
        field hop "los tres tópicos"
        field pdr "PDR embebido"
        field pdr_window "PDR embebido"
        field rx_count "PDR embebido"
        field missed_est "PDR embebido"
        field cadence_violated "PDR embebido"
    }

    pbx_message {
        tag node_id "relay, no originante"
        tag node_label
        tag portnum "PRIVATE_APP o TEXT_MESSAGE_APP"
        tag src_id "uint32 decimal, teléfono"
        tag dst_id "ausente en broadcast"
        field pkt_id "NUNCA tag"
        field fw_ver
        field malformed
        field payload_len
        field content_len
        field rssi
        field snr
        field hop
    }

    pdr {
        tag node_label "sin node_id"
        tag flow "device, environment o position"
        tag source "sweep"
        field pdr "null si slots menor a 2"
        field pdr_window
        field pdr_window_slots
        field pdr_window_filled
        field rx_count
        field missed_est
        field missed_now
        field early_count
        field cadence_violated
        field gap_s
    }
```

Las relaciones son por `node_label` — no hay claves foráneas en InfluxDB, pero
es el único tag que las tres comparten y por el que tiene sentido cruzarlas.

## Las cuatro reglas que sostienen el diseño

**1. `pbx_message` no puede vivir en `telemetry`.**
Trae `rssi`/`snr`/`hop` para el mismo `node_id` que la telemetría, pero a
cadencia dirigida por teléfonos. `monitor/utils.py get_recent()` filtra esos
gráficos sólo por `node_id`, sin mirar el tópico: compartir measurement
mezclaría dos poblaciones con muestreos distintos en el mismo chart, sin error
visible. Es contaminación, no una caída.

**2. `pkt_id` es field, nunca tag.**
Es único por paquete. Como tag, la cardinalidad de series crecería sin techo
hasta voltear la base. Como field no está indexado — de ahí que el join TX↔RX
tenga que ocurrir aguas arriba.

**3. Los valores de tag son la identidad de la serie.**
`src_id` y `dst_id` se publican como `uint32` decimal (el número telefónico que
loguea el PBX como `+56<uint32>`). Cambiar ese render más adelante no
actualiza nada: parte la historia en dos conjuntos de series disjuntos que ya no
se pueden unir. Por eso el formato del frame se verificó contra
`client-integration.md` **antes** de dejar escribir.

**4. Ausencias que son correctas, no bugs.**
`pdr` viene `null` mientras haya menos de 2 slots; `dst_id` no existe en los
frames broadcast; el grupo entero de PDR está ausente cuando el nodo no declara
cadencia para ese flujo en `mesh_config.json`. Telegraf saltea los `null`, así
que se traducen en campos ausentes y el frontend ya los ignora.

## Lo que todavía no está

La measurement que falta es la del lado TX (`P1`/`P2` en
[`data-flow-measurement-points.md`](./data-flow-measurement-points.md)). Sin
ella, `pbx_message` registra recepciones sin denominador y no hay PDR de
mensajería, sólo conteo.

Tampoco hay test de ingesta: los 94 tests cubren el publisher, no
`telegraf.conf`. Ese hueco es exactamente el que dejó `message` y `pdr` sin
consumir durante seis días sin que nada fallara.
