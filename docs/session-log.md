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

## 2026-08-06 — Ingesta de `message`/`pdr` + verificación del frame del proxy
**Time:** (aprox)
**User request:** `/start`, luego higiene del working tree, luego diseño de dónde
almacenar la mensajería de texto y cómo medir su PDR. El owner pidió implementar
él mismo con directrices, y que la revisión, los tests y la documentación las
hiciera el orquestador.

### Actions taken
- **Higiene:** `psks.txt` al `.gitignore` (verificado: nunca estuvo en la
  historia); eliminada `TELEMETRY_DEV_UPDATE_INTERVAL` de
  `src/proxy/configure_params.py` (constante muerta que violaba
  `pdr-cadence-single-source`); `.uf2` de firmware 2.7.26 staged.
- **Defecto encontrado:** `telegraf.conf` sólo se suscribía a
  `position`/`device`/`environment`. Los tópicos `message` y `pdr`, agregados el
  07-31, se publicaban a Mosquitto y **nadie los consumía** — seis días de datos
  perdidos, incluida la visibilidad de nodos muertos que es el propósito del
  tópico `pdr`.
- **Verificación contra el firmware:** se leyó
  `../meshtastic-ble-proxy/{src/proxy_protocol.{c,h}, src/ble_gatt.c,
  docs/readings/client-integration.md}` en vez de seguir asumiendo el formato.
- `telegraf.conf`: dos bloques nuevos con `name_override` (`proxy_message`,
  `pdr`), tags definidos, `pkt_id` explícitamente como field.
- `mesh_receiver.py`: parser con rama por portnum, validación del byte VERSION,
  render decimal de los ids, eliminado todo el aparato de `seq`.
- `tests/test_mesh_receiver.py`: helpers para ambos formatos, esperados nuevos,
  clase `TestBroadcastMessageFrame` (5 casos), test de rechazo por versión y test
  de regresión `src_id != dst_id`. **88 → 94 tests, todos en verde.**
- Nueva memoria `proxy-frame-wire-format`; `data-contract-gateway-web` extendida
  con las tres measurements.

### Decisions
- **Tres measurements en vez de una.** `proxy_message` no puede caer en
  `mqtt_consumer`: trae `rssi`/`snr`/`hop` para el mismo `node_id` que la
  telemetría pero a cadencia dirigida por teléfonos, y `monitor/utils.py` filtra
  esos charts sólo por `node_id`. Compartir measurement mezclaría dos poblaciones
  sin error visible.
- **`pkt_id` como field, nunca tag** — único por paquete. Y como InfluxQL no
  puede hacer join entre measurements por valor de field, el PDR de mensajería se
  calcula aguas arriba y la base sólo guarda el resultado; mismo patrón que ya usa
  `CadencePdrTracker`.
- **Dos estimadores de PDR distintos, a propósito.** La telemetría es periódica →
  el silencio es información. La mensajería es aperiódica → hace falta ground
  truth de TX. No hay estimador único mejor; la disciplina que los mantiene
  coherentes es que ambos emitan la misma forma de métrica, distinguidos por
  `flow`/`source`. No son comparables entre sí: uno es inferencia con ±1 paquete
  por hueco, el otro sería exacto.
- **`proxy_id` en decimal.** Es un `uint32` little-endian (número telefónico sin
  código de país) que el proxy loguea como `+56<uint32>`. El render anterior
  `f"!{src:08x}"` lo hacía parecer un node id de Meshtastic y no coincidía con
  ninguna otra fuente — y es valor de tag, así que cambiarlo después habría
  partido todas las series históricas.
- **Validación del byte VERSION en lugar de un flag `FRAME_HAS_SEQ`.** El v2 con
  `seq` está sólo *propuesto* en el repo del proxy; el byte de versión ya existe
  para señalar cambios de formato, así que un v2 se reporta como `malformed` en
  vez de misparsearse.
- **Rechazado:** guardar el contenido de los mensajes en InfluxDB. Metadata sí,
  contenido no — es tráfico real de personas, la base se exporta y comparte, y no
  hay historia de retención ni redacción para eso. `capture_content` sigue en
  `False`.
- **RPi en el nodo proxy: sí, pero no por la energía.** Un hub USB con fuente
  resuelve los dos puertos por 15 dólares. La justifican el ground truth de TX y
  la referencia de masa común que exige el enlace UART nordic↔LiLyGO.

### Outcomes
- Ingesta reparada; el pipeline vuelve a persistir los cinco tópicos.
- Formato del frame verificado, no asumido. Se cerró el ítem "endianness
  unverified" abierto desde el 07-31: **no había desajuste** — el parser original
  era correcto y el diagnóstico intermedio del orquestador estuvo equivocado.
- Soporte para frames broadcast (`TEXT_MESSAGE_APP`, header de 5 bytes), que
  antes se descartaban en el dispatch.
- 94 tests en verde con `.venv/bin/python -m unittest discover -s tests -t .`
  (el `pytest` global no ve el módulo `meshtastic`; hay que usar el venv).

### Diseño de la RPi del proxy (mismo día, tras los diagramas)
- Pregunta del owner: nunca se había definido **qué corre** en la Pi. Al buscar la
  respuesta se revisó el log del firmware del proxy y apareció el hallazgo que
  cambia la prioridad: **P1 sirve hoy aunque los ids estén rotos.** El bug de
  `proxy_id_to_str` sólo arruina `src`/`dst`; el resto del log es operativo y hoy
  es invisible — `TX queue full — ToRadio dropped` (pérdida *medida*, no inferida,
  justo en el tramo proxy→nodo), `RX overrun`, censo de conexiones BLE, `No free
  connection slot`, reboots del nodo, `bad header — broadcast fallback`.
- Owner definió el backhaul: **WiFi 5 GHz de la red del edificio (MIDE)**, sin
  celular. Desacopla los proxies de `gateway-rpi-5g.md`, que es sólo el gateway.
- **ADR-0002 escrito** (`docs/architecture/ADR-0002-proxy-site-edge-collector.md`):
  la Pi es un colector de borde, no un segundo pipeline. Corre `proxy-logd`
  (desbloqueado), `node-tap` (condicionado a test de banco), Mosquitto local en
  bridge para store-and-forward, y NTP. **El reconciliador TX↔RX NO va en la Pi**
  — el RX, el broker y la base están en el gateway; la Pi queda como publicador
  reemplazable.
- Elevado a precondición, no a follow-up: **habilitar auth en el broker**. Hoy es
  `allow_anonymous` con `1883` en todas las interfaces; publicar desde la WiFi del
  edificio lo convierte en un broker MQTT abierto en red compartida. Para un
  testbed de medición el riesgo es integridad de datos antes que acceso —
  `_is_valid()` filtra por un `node_id` que viaja dentro del payload que el emisor
  controla.
- Rechazado y anotado en el ADR: hub USB alimentado (resuelve el problema
  declarado y ningún otro), dos cargadores separados (masas flotantes en un enlace
  UART), reconciliador en la Pi, pipeline completo en el borde, y esperar al `seq`
  v2 en vez de usar `pkt_id`.

## 2026-08-10 — Rename a meshpbx + `telemetry`, y prueba con hardware real
**Time:** (aprox)
**User request:** probar el colector contra hardware, y renombrar todo lo que
diga "proxy" — el proyecto pasa a llamarse **meshpbx**, porque lo que hace es
una *private branch exchange*.

### Prueba con hardware — dos defectos que ningún test podía encontrar
- LiLyGO en `/dev/ttyACM0` (WCH/CH34x) y nordic en `/dev/ttyACM2` (SEGGER
  J-Link). Nota lateral: `/dev/ttyACM1` era un teléfono Samsung — exactamente el
  escenario que corre la numeración y por el que hay que usar `by-id`.
- **La consola en vivo emite ANSI de color.** `\x1b[32mINFO \x1b[0m| …`, así que
  la línea no empieza con el nivel y **52 de 52 líneas se rechazaban**: el
  colector no habría publicado nada, pareciendo sano. Los escapes no están en
  `docs/log-parsing.txt` porque copiar del panel del SerialMonitor los elimina —
  leer el dispositivo es la única forma de verlos. `lines_rejected` delató la
  deriva en veinte segundos, que es para lo que existe.
- **La placa se resetea en el ciclo cerrar/abrir.** Medido: con el puerto
  sostenido el uptime sube monotónico sin caídas; al cerrar y reabrir arranca en
  0 con `rst:0x1 (POWERON_RESET)` del bootloader ROM. **HUPCL ya estaba en
  `False`**, así que mi hipótesis era errónea; el culpable es el circuito de
  auto-programación reaccionando a transiciones que hace el driver, y
  `dtr=False` antes de `open()` no lo evita.
- Bug real encontrado en el camino: `_disable_hupcl()` leía `self._ser`, que
  todavía es `None` mientras corre `_open()`, así que no hacía nada y sin avisar.
  Corregido, aunque no era la causa del síntoma.
- **Consecuencia operativa:** abrir una vez y no cerrar nunca. Y para instalación
  permanente, cablear el UART del GPIO de la Pi al UART0 del LiLyGO con tres
  hilos — sin DTR ni RTS en el camino, y de paso elimina la renumeración.
  Riesgo a vigilar: un colector en crash-loop rebootea el nodo en cada intento.

### `mqtt_consumer` → `telemetry`
- Era el nombre que Telegraf asigna por defecto: describe cómo llegó el dato, no
  qué es, y convivía con `pdr` y `pbx_message`, ambos nombrados a propósito. La
  inconsistencia estaba al revés: el nombre accidental tenía los datos importantes.
- Se hizo ahora porque es una **migración**, no un cambio de config: no existe
  rename en InfluxDB 1.x. `SELECT * INTO "telemetry" FROM "mqtt_consumer" GROUP BY *`
  — el `GROUP BY *` no es opcional, sin él los tags se aplanan a fields y se
  destruye la estructura de series en silencio. 57.489 puntos, 8 series, conteos
  verificados iguales y tags confirmados como tags. A este volumen fueron
  segundos; con datos de campo acumulados no lo habría sido.
- Tres consumidores habrían quedado en blanco sin aviso: `monitor/utils.py` (como
  default del constructor), `src/tools/plot_history.py` (dos consultas) y las
  consultas documentadas del README y del playbook. Las tres formas se
  re-ejecutaron contra `telemetry`.
- `mqtt_consumer` se dejó en pie; dropearlo es una línea cuando se ejercite el
  dashboard con el stack arriba.

### Rename proxy → pbx
- `src/proxy/` → `src/pbx/`, ADR-0002 renombrado, `pbx_message`, `pbx_health`,
  tópico `.../pbx`, `_parse_pbx_frame`, `pbx_logd`, memoria
  `pbx-frame-wire-format`, y las referencias a `../meshtastic-ble-proxy` →
  `../meshpbx`. 273 líneas en 33 archivos.
- **Convención adoptada:** `meshpbx` para el proyecto y el repo, `pbx` para
  identificadores de código y datos. `meshpbx_message` sería redundante.
- **NO se renombró, a propósito:** las etiquetas `p1`/`p2` (son sitios, y además
  son las cuentas del broker y las reglas del ACL — renombrarlas obligaría a
  regenerar `pwfile` en tres máquinas); el prefijo `meshtastic-testbed/`; y los
  nombres de flujo (`device`, `environment`, `position`, `message`, `pdr`).
- **Tampoco los símbolos del firmware**: `proxy_id`, `proxy_id_to_str()`,
  `proxy_protocol.c`, `PROXY_VERSION` se citan literales porque son
  identificadores externos. Anotado en la memoria para que nadie los "corrija".
- Las entradas viejas de esta bitácora se dejaron sin tocar: son registro
  histórico y reescribirlas sería falsificar la cronología.
- **Dos errores encontrados en las memorias al pasar:** decían "decimal **LE**
  uint32" cuando habíamos establecido big-endian, y quedaban rutas
  `src/proxy/configure.py`. El primero es serio: esa memoria existe justamente
  para evitar ese error.

### Outcomes
- 118 tests en verde, todos los módulos compilan, los 7 bloques Mermaid de los
  tres diagramas siguen renderizando.

### Next steps / open questions
- Falta el bridge Mosquitto local antes de instalar en terreno.
- `pbx-logd` desbloqueado; el fix de `proxy_id_to_str()` ya está aguas arriba.
- Cuando exista el repo `meshpbx`, revisar si sus símbolos internos se renombran
  y actualizar las citas literales de este repo.

---

## 2026-08-10 — `node-logd`: el parser de la consola del nodo (P2)
**Time:** (aprox)
**User request:** diseñar y desplegar el parser de logs en la Pi del proxy.

### Actions taken
- `src/collector/`: `serial_lines.py` (lector con supresión de DTR/RTS y de
  `hupcl`, reconexión, y tick de inactividad), `node_logd.py` (7 patrones +
  máquina de estados por `pkt_id` + `main()`), `config.py`, `requirements.txt`
  (sin `meshtastic`: el colector lee texto, no habla el protocolo), `Dockerfile`.
- `compose.collector.yaml`: despliegue separado, para la Pi del sitio de proxy.
- Cuarto bloque de Telegraf → measurement `proxy_health`.
- `tests/test_node_logd.py`: 18 casos, la mayoría contra `docs/log-parsing.txt`.
  **100 → 118 tests.**

### Decisions
- **Lista blanca de 7 patrones, todo lo demás se ignora.** Hay ~85 formas de
  línea en la captura y van a cambiar entre releases de Meshtastic. Y los
  patrones van anclados: con líneas truncadas, un patrón laxo hace *media*
  coincidencia y devuelve basura silenciosa. Mejor perder una línea que inventar
  un dato. `lines_rejected` se publica como métrica: si el formato deriva, se ve.
- **Un registro por paquete, no eventos crudos.** El ciclo de vida completo es
  conocimiento local, así que se ensambla en la Pi. Al reconciliador central le
  queda sólo el cruce que no es local: este registro contra el RX del gateway.
- **Cinco desenlaces**, y el que importa es `dropped_before_tx`: el proxy
  entregó el frame y el nodo nunca lo puso al aire. Separa pérdida del handoff
  UART de pérdida de radio — distinción imposible desde un solo punto.
- **`tx_attempts` contando repeticiones del mismo id**, no la línea
  `Setting next retransmission` (que lleva su id en la línea *siguiente*).
- Sólo se rastrean paquetes con `PACKET FROM PHONE` propio. La telemetría del
  propio nodo también transmite, pero tiene cadencia conocida y el gateway ya la
  mide; contarla acá sería doble reporte. Va a `tx_other`.
- La expiración corre también en el tick de inactividad, no sólo al llegar
  líneas: un nodo que se calla justo después de transmitir dejaría el paquete
  colgado para siempre.

### Outcomes
- Verificado end-to-end contra broker y base reales: los registros llegan a
  `proxy_health` con la credencial de `p1`, tags `kind/portnum/channel/outcome`
  y `pkt_id` como field. **`pkt_id` sale como el mismo entero que guarda el
  gateway** (435365562 = `0x19F326BA`), así que la llave de cruce coincide —
  que es el punto de todo el diseño. float64 representa exacto cualquier uint32.
- El compose falla con mensaje claro si faltan `NODE_SERIAL_PORT` o
  `BROKER_ADDRESS`, en vez de arrancar y no escuchar nada.

### Correcciones a cosas que había afirmado mal
- **La captura son 4 logs de 2 dispositivos concatenados**, no un stream. El
  segundo `Started Tx` de `0x3a5f05e6` está en la sección *receptora*: es otro
  nodo rebroadcasteando, no un reintento del emisor. Mi evidencia de
  retransmisiones era un artefacto de leer dos logs como uno. El mecanismo de
  conteo sobrevive; la evidencia no. Anotado en el docstring de los tests.
- **La truncación a ~188 caracteres es del copy-paste con mouse** desde el panel
  del SerialMonitor, no del stream. Y resultó inofensiva: medí por patrón y
  `PACKET FROM PHONE` (158) nunca se corta, `Lora RX` (188) está completa 12/12,
  y de las dos que sí se cortan sólo necesito el campo `id=`, que es el primero.
- **Las líneas vacías contaban como rechazos** — corregido: `lines_rejected`
  existe para señalar deriva de formato y los blancos ahogaban la señal.
- **`Use channel N (hash 0x..)` sólo lo emite un nodo receptor**, que necesita
  resolver el hash para elegir la clave. El colector aprende la tabla sólo del
  tráfico que su nodo recibe, y un hash desconocido queda desconocido.
- El test que documentaba `PRIVATE_APP` en canal 0 decía que fallaría cuando
  arreglaran la app. Falso: corre contra un fixture congelado, así que va a
  pasar siempre. Reescrito el comentario; el detector real es la consulta a
  InfluxDB sobre datos vivos.

### Next steps / open questions
- **Falta el bridge Mosquitto local antes de instalar en terreno.** Hoy publica
  directo al broker del gateway: correcto en banco, incorrecto en campo, donde
  cada corte de WiFi se come el ground truth de TX.
- `ConsoleReader` no está probado contra hardware. La supresión de DTR sólo se
  valida con una placa real, y es lo que evita rebootear el nodo en cada
  reconexión.
- `proxy-logd` desbloqueado por el fix de `proxy_id_to_str` aguas arriba.
- `PRIVATE_APP` en canal 1: arreglado del lado app (owner).

---

### Continuación 2026-08-07 — el log del nodo destraba el PDR de mensajería
- **Constraint del firmware:** el owner confirmó que **no se admiten dos
  instancias del Stream API**. Eso mató el `node-tap` original (un cliente
  Meshtastic por USB) y con él la vía al `pkt_id` — el PDR de mensajería quedó
  sin denominador y dependiendo de un cambio de firmware ajeno.
- **Pregunta del owner que lo resolvió:** ¿sirven los logs propios de Meshtastic
  en el LiLyGO? Sí, y el constraint no aplica: la consola USB emite **texto
  plano** mientras nadie hable protobuf por ahí. Es lectura pasiva, no una
  segunda instancia. Captura guardada en `docs/log-parsing.txt`.
- Lo que trae esa consola: ciclo de vida completo de TX con el `id` de paquete
  (`PACKET FROM PHONE` → `enqueue for send` → `Started Tx` → `Completed sending`),
  que es el mismo `pkt_id` que el gateway ya registra. Tres etapas, así que
  "entregado pero no transmitido" queda separado de "transmitido y perdido".
- **F3 cancelado:** Meshtastic ya emite `txGood=…,txRelay=…,rxGood=…,rxBad=…`,
  contadores monotónicos auto-reparables ante líneas de log perdidas. Era
  exactamente el diseño que se le iba a pedir al firmware del proxy.
- **`WantAck=1` en mensajería** (la telemetría es 0): el nodo retransmite hasta
  el ACK, así que hay que contar **ids únicos** y existen dos ratios distintos
  (primer intento vs entrega final). Segunda razón por la que el PDR de
  mensajería y el de telemetría no son comparables entre sí.
- **Orden de bytes — corrección de una corrección.** Diagnostiqué desajuste,
  después me retracté citando `client-integration.md` (que dice little-endian), y
  el owner desconfió. Tenía razón: los cinco sitios del firmware que interpretan
  el id usan `sys_get_be32()`, incluido el handler de NODE_REG. Confirmado
  empíricamente con `+56352953967` = `0x1509A66F`, y corroborado por el header de
  5 bytes visible como `msg=####otest_all` (termina en `0x6F`='o', el byte bajo
  al final). **En ese repo el código manda sobre los docs** — nos desviaron tres
  veces: el `seq` inexistente, los 16 bytes de NODE_REG y el endianness.
- Aplicado: `>BII`/`>BI` en `mesh_receiver.py`, tests a big-endian con el handset
  real, y `TestProxyIdByteOrder` fijando los bytes exactos contra la captura.
  **95 tests en verde.**
- **`PRIVATE_APP` viaja hoy por canal 0**, no por el 1. El owner lo identificó
  como bug de la app de celular (ahí se construye el MeshPacket). Consecuencia
  mientras tanto: la mensajería se cifra con la PSK del canal de telemetría.
  Capturar `channel` sirve además como detector de si el fix llegó.
- Infraestructura decidida: **tres Pis** (una por sitio de proxy + gateway), la
  del gateway **corre todo el stack** y por lo tanto **debe bootear desde SSD
  USB**, no microSD.
- Documentación actualizada: ADR-0002 (revisión completa), el diagrama de flujo,
  `gateway-rpi-5g.md` (pregunta abierta resuelta), `project-overview.md` y la
  memoria `proxy-frame-wire-format`.

### Implementación: captura de `channel` + auth del broker (2026-08-07)
- **`channel` capturado** con `packet.get("channel", 0)`, propagado a los cuatro
  handlers. Tag en `proxy_message`, field en `mqtt_consumer` (ahí siempre vale 0).
  El default explícito no es cosmético: protobuf3 omite los valores por defecto,
  así que la clave **no viene** para el canal 0 y un `.get()` pelado daría `None`,
  que Telegraf descarta — el tag desaparecería del grueso del tráfico. Hay un
  test que verifica primero la ausencia de la clave y después que el payload
  reporta 0.
- **Auth del broker** (precondición de ADR-0002): `allow_anonymous false`,
  `password_file`, `acl_file` con cinco cuentas por rol — `gateway` escribe todo,
  `p1`/`p2` sólo su propio subárbol, `telegraf` y `monitor` sólo leen. Script
  `mqtt/init-credentials.sh` que genera el pwfile usando la imagen del broker
  (sin instalar nada en el host). Credenciales plomeadas por gateway, monitor y
  los tres bloques de Telegraf.
- **Bug evitado en el camino:** primero puse `- MQTT_USERNAME=${MQTT_USERNAME_GATEWAY}`
  en el bloque `environment:` del compose. Compose interpola `${VAR}` desde el
  `.env` de la raíz o el shell, **nunca desde `env_file:`** — habría pasado
  strings vacíos y todo habría fallado con rc=5. Solución: cada app lee su
  variable por rol, y queda comentado en el compose para que nadie lo revierta.
- `.gitignore` decía `config/pwfile`, una ruta que no existe — el pwfile nunca
  estuvo realmente protegido. Corregido a `mqtt/pwfile`.
- `mqtt/` era propiedad del uid 1883 (el contenedor se la apropió al montar el
  bind mount), así que ni el owner podía editar `mosquitto.conf` desde el editor.
  Resuelto con `chown`; Mosquitto sólo lee de `/mosquitto/config`.
- **98 tests en verde**, los siete archivos Python compilan, `docker compose
  config` válido.
- **Credenciales generadas y verificadas** (a pedido del owner). Se corrió el
  script redirigiendo su salida a un archivo temporal y se insertaron los pares
  en `configuration.env` de forma programática, para que las contraseñas nunca
  pasaran por la conversación; el temporal se borró después. `mqtt/pwfile` tiene
  las 5 cuentas con hash `$7$` (sha512-pbkdf2).
- **Tres cosas que salieron mal al ejecutarlo, las tres ya corregidas en el script:**
  1. `mosquitto_passwd` **no acepta opciones cortas combinadas** — `-cb` se
     rechaza con un volcado de uso. Tienen que ir `-c -b` por separado. `set -e`
     evitó que quedara un pwfile a medias.
  2. La imagen `eclipse-mosquitto` hace `chown -R mosquitto:mosquitto /mosquitto`
     en su entrypoint, así que **cada arranque del contenedor se apropia del
     directorio `mqtt/` del host** (uid 1883) y el owner deja de poder editar
     `mosquitto.conf` desde el editor. Eso explica por qué estaba así desde
     abril. El script ahora invoca el binario con `--entrypoint` para no
     disparar ese chown.
  3. Mosquitto 2.x advierte sobre archivos de credenciales world-readable y
     versiones futuras se negarán a cargarlos. El script ahora deja `pwfile` y
     `aclfile` en 0600 propiedad de 1883.
- **Verificación con el broker levantado, 9 casos, todos correctos:** anónimo,
  contraseña incorrecta y usuario inexistente → conexión rechazada; `gateway` →
  publica en cualquier tópico; `p1` → publica en su subárbol pero **es denegado
  en el de p2** (y `p2` en el de `p1`); `monitor` y `telegraf` → denegados al
  publicar, son sólo lectura. El aislamiento entre sitios de proxy que motivaba
  el diseño está efectivamente aplicado.
- Detalle de la verificación: en MQTT v5 un CONNECT rechazado **también** dice
  `Not authorized`, igual que una denegación de ACL, y `mosquitto_pub` sale con
  código 0 cuando el ACL bloquea un publish QoS 1. Hay que mirar el código de
  salida primero y el texto después, o se confunden los dos casos.
- El stack se dejó **abajo**, como estaba. Al levantarlo, ojo con
  `GATEWAY_SERIAL_PORT`: el default del compose es `/dev/ttyACM0` pero según la
  sesión del 07-13 el gateway está en `/dev/ttyACM1`.

### Verificación end-to-end del stack (2026-08-07, previa al despliegue en Jetson)
Se levantó todo menos `gateway-receiver` (no requiere hardware) y se publicaron
payloads sintéticos con la credencial `gateway`. **Dos defectos encontrados, los
dos corregidos y re-verificados contra la base real:**

1. **`docker compose up -d` no reconstruye la imagen al cambiar el código.**
   `web` y `gateway-receiver` hornean el Python con `COPY . .`, así que el
   monitor corría el código anterior a las credenciales y el broker lo rechazaba
   con `not authorised` — pese a que las variables **sí** llegaban al contenedor.
   Diagnosticado comparando `grep -c MQTT_USERNAME_MONITOR` dentro de la imagen
   contra el repo. **Tras cualquier cambio de Python hace falta `--build`.**
   Telegraf no lo sufre porque monta su config como volumen.
2. **Telegraf descarta los campos booleanos**, igual que descarta los strings.
   `malformed` y `cadence_violated` no aparecían en `SHOW FIELD KEYS` y nadie
   reportaba error. `cadence_violated` era recuperable vía `early_count`, pero
   `malformed` es la única marca de un frame corrupto. Ahora se publican como
   enteros `0/1`; re-verificado: `malformed` aparece como field `float`.
   Dos tests nuevos fijan que sean `int` y explícitamente **no** `bool`.

Confirmado en la base real, no en la intención: las tres measurements con sus
tags exactos, `src_id`/`dst_id` ausentes de los field keys (o sea, son tags),
`pkt_id` como field, y `channel` presente en `mqtt_consumer`. Los datos de prueba
(`node-9`) se borraron después.

### `mesh_config.json` — dos cambios en p1/p2, ambos intencionales (owner)
1. **Cadencia `device`: 900 s → 600 s.** Se necesitaba información del
   dispositivo Meshtastic con más frecuencia. (Estuvo brevemente en 300 s durante
   la sesión.) Consecuencia al analizar: las series de PDR de p1/p2 anteriores y
   posteriores **no son comparables** — cambia el denominador del estimador, y la
   resolución de la ventana rodante pasa de 4 a 6 slots para una ventana de 1 h.
2. **`hop_limit` nivelado: p1 de 1→2, p2 de 3→2.** Elimina la asimetría
   deliberada entre ambos proxies. A favor: con la configuración uniforme, una
   diferencia medida entre p1 y p2 se atribuye a la **ubicación** y no a los
   parámetros — que es lo que hace falta para un experimento controlado. En
   contra: se pierde el contraste de alcance que daban 1 vs 3 saltos.
   **Requiere re-provisionar**: `configure.py --node-id p1|p2 --port ...` lee el
   `hop_limit` de este archivo; hasta que se corra, las radios siguen en 1 y 3 y
   el archivo miente respecto del hardware.

### Next steps / open questions
- **Bug de firmware, repo separado:** `proxy_id_to_str()` lee 16 bytes de un
  arreglo de 4 y lo alcanza `proxy_header_to_str()`, que genera las líneas de log
  legibles. Los src/dst del log VCOM del nordic son basura, y ese log es el
  ground truth de TX que necesita el PDR de mensajería. El owner decidió no
  abrir issue desde acá.
- Falta un test end-to-end de la ingesta: los 94 cubren el publisher, no
  `telegraf.conf` — por eso el defecto pasó seis días inadvertido.
- **Diagramas: HECHO.** Tres archivos nuevos en `docs/diagrams/`, en Mermaid
  (texto versionable que cambia con el código, en vez de PNG que envejecen —
  `gateway.drawio.png` es de abril): `data-flow-measurement-points.md`,
  `database-ingestion-schema.md` y `container-topology.md`. Los cuatro bloques
  validados renderizándolos con `mermaid-cli`, no leyéndolos: el de ingesta salió
  mal en el primer intento porque Mermaid ignora el `direction` de un subgraph
  cuando hay aristas cruzando entre subgraphs, y las conexiones colapsaron en
  haces anónimos; se rehizo como `flowchart LR` sin `direction` interno.
- Hallazgos al diagramar los contenedores (ninguno actuado): `web` se suscribe a
  MQTT con wildcard `+/+` además de consultar InfluxDB — por eso el defecto de
  ingesta no se veía en el dashboard, que sí recibía `message`/`pdr` en vivo; el
  broker es anónimo (`allow_anonymous true`), lo que deja de ser aceptable cuando
  la RPi del proxy publique desde otra ubicación; `telegraf` declara un
  `extra_hosts: host.docker.internal` que ya no usa; y la persistencia está
  partida entre un volumen nombrado (InfluxDB) y bind mounts en el árbol del repo
  (Mosquitto).
- Sigue sin justificar el cambio `900→300 s` de la cadencia device de p1/p2 en el
  working tree; bloquea el checkpoint de git.
- Arrastres sin tocar: containerización y `field_testing/` ausentes del
  project-overview, Dependabot, node-label mismatch, licencia.

---

## 2026-07-31 — Cadence-based PDR tracking + proxy message capture
**Time:** (aprox)
**User request:** Cómo implementar `_handle_text_message()` en
`src/gateway/mesh_receiver.py` aprovechando `src_id`/`dst_id` del frame del
proxy, con el objetivo de capturar la mayor cantidad de paquetes posible para
análisis de PDR. Tras la propuesta inicial el owner acotó el alcance: **el PDR
se calcula sólo sobre mensajes de cadencia conocida** (telemetría y posición),
el `seq` en el payload queda como integración futura del firmware del proxy;
ventana rodante de 30 min–1 h; intervalos en `mesh_config.json`; sin pipeline.

### Actions taken
- `src/common/mesh_config.py` (nuevo): lector compartido de `mesh_config.json`
  — `load`, `node_cfg`, `intervals_for`, `pdr_window_sec`, `sweep_interval_sec`.
- `mesh_config.json`: nuevo bloque `pdr_cfg` (`window_sec` por tipo de flujo,
  `sweep_interval_sec`) e `intervals` por nodo (nodos 1-3: device/env 120 s,
  position 600 s; p1/p2: device 900 s, position 1800 s, sin environment).
- `src/gateway/mesh_receiver.py`: clase `CadencePdrTracker` + `_handle_text_message`
  (parseo del frame `[fw_ver:1][src_id:4][dst_id:4][seq:2]?[content]` con
  `struct`, little-endian) + sweep periódico en `listen()` + detección de
  reboot vía `uptimeSeconds`.
- `src/gateway/mqtt_connector.py`: `publish_message` / `publish_pdr` y topics
  `.../message` y `.../pdr`.
- `src/gateway/receiver.py`: dividido en `load_mesh_config` (I/O) +
  `load_known_nodes` (mapeo) + `load_intervals` (cadencias).
- `src/node/configure.py` + `src/proxy/configure.py`: leen las cadencias de
  `mesh_config.json` y fijan `position.position_broadcast_smart_enabled false`.
- Tests: `tests/test_pdr_tracker.py` (nuevo, 26 casos), casos nuevos en
  `test_mesh_receiver.py` (frames de proxy + integración PDR) y
  `test_load_nodes.py` (mapa de cadencias). 88 tests, todos pasando.

### Decisions
- **Estimador por huecos entre llegadas**, no conteo por ventana:
  `missed = max(0, round(dt/T) - 1)`. Atribuye cada pérdida a un instante
  concreto (correlacionable con RSSI), da la distribución de rachas gratis y
  permite excluir un hueco específico cuando no fue el radio. `round()` y no
  `floor()` porque el jitter del firmware es ~simétrico y `floor` inventaría
  una pérdida en cada paquete atrasado.
- **`sweep()` es detector de silencio, no la ventana.** Sin él el estimador
  sólo se actualiza al recibir y un nodo muerto congela su PDR para siempre
  (sesgo de supervivencia). La ventana es el `deque`: como los slots perdidos
  se empujan como `0`, `maxlen = window_sec / T` **es** una ventana temporal
  sin guardar timestamps.
- **Reloj monotónico** para los `dt` (`time.monotonic()`); `received_at` sigue
  wall-clock porque Telegraf lo usa como time key de InfluxDB. Un salto NTP en
  el host habría fabricado pérdidas en todos los flujos a la vez.
- **`pdr > 1` es imposible** con este estimador (`missed >= 0`), así que la
  señal de cadencia violada es `early_count`: recepción dentro de un intervalo
  nominal. Corrige una propuesta previa errónea de esta misma sesión.
- **Intervalos en `mesh_config.json`, autoritativos y completos por nodo** — un
  tipo ausente significa "este nodo no lo emite" y no se le mide PDR, en vez de
  rellenarse con defaults (los proxies no emiten environment). Los constantes
  duplicados salieron de `node/` y `proxy/configure_params.py`.
- **Contenido de mensajes NO se publica por defecto** (`capture_content=False`):
  son mensajes reales de teléfonos y el stream MQTT se persiste en InfluxDB.
- Rechazado: bitmap de ventana deslizante por flujo para tolerar reordenamiento
  (innecesario a esta tasa; anotado en el docstring si aparece en campo).

### Outcomes
- PDR por flujo `(nodo, tipo)` viaja dentro de los payloads existentes de
  `device`/`environment`/`position` (aditivo, contrato con `monitor/` intacto);
  las pérdidas inferidas en silencio van al topic nuevo `.../pdr`.
- Bug encontrado y corregido durante los tests: `reanchor()` dejaba `last = now`
  y la recepción que reportaba el reboot medía `dt = 0`, clasificándose como
  *early*. Resuelto con un flag `restart` explícito; test de regresión añadido.
- Detectado que `position_broadcast_smart_enabled` venía en `true` por defecto
  (nunca se fijaba), lo que invalidaba la hipótesis de cadencia fija en nodos
  móviles. Ahora se apaga explícitamente en node y proxy.

### Next steps / open questions
- **Verificar el endianness del frame** contra el firmware de
  `meshtastic-ble-proxy`: está asumido little-endian (`<BII`). Un byte-order
  equivocado no lanza error, sólo produce `src_id`/`dst_id` espejados.
- **Cifrado de DMs:** si el proxy manda PRIVATE_APP como DM a un nodo
  específico, en firmware reciente va con PKI del destinatario y el gateway no
  podrá decodificarlo. El tráfico de medición debe ir a broadcast en el canal
  compartido, o el gateway ser el `dst`.
- Ventana de posición: 3600 s sobre cadencia de 600 s son sólo 6 slots
  (resolución 16.7%). Subir a 21600 si se quiere un rolling útil.
- El rol del gateway sesga lo que oye (ROUTER escucha más que CLIENT): hay que
  registrarlo junto a cualquier PDR reportado o las corridas no son comparables.
- Pendiente (no hecho): captura cruda a JSONL de *todos* los paquetes, incluidos
  los no decodificables, para línea base de recepción offline.

---

## 2026-07-24 — Containerise the gateway receiver (single `docker compose up`)
**Time:** (aprox)
**User request:** Cómo se corre el proyecto a nivel de gateway y si se puede
dejar todo en contenedores para levantarlo con un solo `docker compose`. Then:
"sí" (build it). Also asked to stop using Argentine Spanish → saved as memory
`user-language-neutral-spanish`.
### Actions taken
- Audited the run model: infra (mosquitto/influxdb/telegraf/web) was already
  containerised; the **gateway receiver** (`src/gateway/receiver.py`) was the
  only runtime piece still on the host (manual `.venv` or systemd, reading USB
  serial). Config/flashing scripts are also host-only.
- Added `gateway-receiver` as a 5th compose service with USB device passthrough
  (`devices: ${GATEWAY_SERIAL_PORT:-/dev/ttyACM0}`), `BROKER_ADDRESS=mosquitto`,
  `mesh_config.json` mounted read-only, `restart: unless-stopped`.
- New `src/gateway/Dockerfile` (python:3.12-slim, build context = repo root) and
  slim `src/gateway/requirements.txt` (meshtastic + paho-mqtt + pinned direct
  imports; excludes the heavy matplotlib/pandas from the root reqs).
- Parametrised `src/gateway/config.py` (`BROKER_ADDRESS`/`BROKER_PORT`/
  `CLIENT_ID`/`MESH_CONFIG_PATH` via env, defaults unchanged) and made
  `receiver.py --port` fall back to `GATEWAY_SERIAL_PORT` (still errors if
  neither is set).
- Updated README (first pass): Stage 2 (five services) + Stage 3 Option C.
- **README rewrite (second pass, at owner's request):** collapsed the whole
  run flow into one clean path. Discovery: `install_service.sh` does NOT exist
  in the repo — the entire systemd "Option B" section documented a phantom
  script. Removed Options A/B/C, the systemd section, and all serial-port-
  routing prose (owner: "it just needs to work"). New structure: Getting
  Started (Docker + venv-for-config-scripts) → Hardware Setup (one-time) →
  Running the Platform (single `docker compose up -d`, service table, verify).
  Fixed repo-structure listing (added gateway Dockerfile/requirements, corrected
  receiver `--port` note and compose comment). Owner added `GATEWAY_SERIAL_PORT`
  to `.env.example` themselves (env files are permission-blocked for the agent).
- Verified: `docker compose config` OK; `docker compose build gateway-receiver`
  OK; container smoke test — imports resolve, `MESH_CONFIG_PATH` →
  `/app/mesh_config.json`, no-port error path exits 2, `BROKER_ADDRESS` env
  override works.
### Decisions
- **Went direct, not `/team-new-feature`** — contained, low-risk infra change.
- **Config scripts stay on the host** (not in the always-on stack): setup-time,
  run one device at a time, would contend for the serial port. Container option
  is Linux-only in practice (Docker Desktop USB passthrough on macOS/Windows is
  unsupported/painful) — documented, host Options A/B remain.
- Host defaults left intact (`localhost`, `--port` required) so systemd/manual
  deployments are byte-for-byte unaffected. Data contract preserved
  (see [[data-contract-gateway-web]] — no field/topic changes).
### Outcomes
- `docker compose up -d` now brings up the full runtime pipeline including the
  gateway. Working tree has uncommitted changes (not committed — no instruction).
### Later this session — integrate `platform-testing.zip` (field-install toolkit)
- Owner dropped `platform-testing.zip` (9 files, dated April) — an offline
  node-install validation toolkit (serial → per-node CSV + gateway GPS track,
  plus a session plotter). Distinct from the production MQTT/InfluxDB pipeline.
- Inspected all files; flagged 3 conflicts. Owner decided via 4-question prompt:
  (1) location → `src/tools/field_testing/`; (2) radio config → **reuse
  `src/common/radio_config.py`** (the zip's `param_receiver.py` was stale:
  single channel `TB CPS-RTC` + `LONG_FAST` + **plaintext PSK**, vs production's
  dual-channel `telCPS_RTC`/`msgPUC_NET` + `LONG_TURBO` + env PSKs — a test
  receiver on the old config would NOT hear the nodes); (3) secrets → sanitise +
  `.example`, gitignore the real; (4) BLE → documented as Meshtastic-app manual
  procedure, no code.
- Created `src/tools/field_testing/`: `__init__.py`, `mesh_receiver.py` (CSV
  receiver, verbatim), `receiver.py` (reuses root `mesh_config.json`, default
  out `field-testing-data/`), `configure_device.py` (reuses `common.radio_config`
  + `common.meshtastic_cli`; CLIENT_MUTE + GPS on for walk-tests), `plot_data.py`
  (default dir points at `field-testing-data/`), `example_config.yaml.example`
  (sanitised — privateKey/channel_url/mqtt creds redacted, public-MQTT block
  dropped), and a `README.md` documenting both validation methods.
- Dropped from the zip: `param_receiver.py` (→ common), its `mesh_config.json`
  (→ root), `requirements.txt` (→ root reqs).
- `.gitignore`: added `field-testing-data/`, `*_plot.png`, the real
  `example_config.yaml`, and `platform-testing.zip` (has plaintext secrets).
- README: added `field_testing/` to the structure + a toolkit subsection.
- Verified: all 5 scripts byte-compile; `receiver.py`/`plot_data.py`/
  `configure_device.py --help` run; `plot_data.py` on empty dir reports cleanly;
  `configure_device.py` import path (`common.radio_config`) resolves.

### Next steps / open questions
- Owner can delete `platform-testing.zip` now that it's integrated (gitignored
  meanwhile). The stale plaintext PSK in it is a rotation candidate if that
  channel was ever used outside the lab.
- Optional: expose the config/flashing scripts as a `docker compose run` profile
  (opt-in) if fully-dockerised setup is ever wanted.
- `project-overview.md` pipeline diagram still shows the receiver as host-only —
  update to note the container option (flagged for doc-keeper).
- Commit when the owner instructs (git-lead / `/team-git-checkpoint`).

---

## 2026-07-21 — Git checkpoint — ADR-0001 + BLE-proxy integration committed
**Time:** (aprox)
**User request:** Ran `/start`; producer + doc-keeper synthesized a briefing
(doc-keeper flagged `project-overview.md` as needing an update — ADR-0001 and
the proxy integration both unreflected). Owner chose "git checkpoint" as this
session's task over the doc update or continuing hardware setup.
### Actions taken
- git-lead analyzed the working tree (2 independent uncommitted work packages
  sitting since 2026-07-10 and 2026-07-15) and proposed a 2-commit strategy,
  staying on `main` (repo's established direct-to-main convention, no CI/CD or
  PR workflow present).
- Owner approved the strategy as proposed via AskUserQuestion.
- git-lead executed exactly 2 commits (no `git add -A`, explicit file lists):
  - `3795e08` — `docs(architecture): add ADR-0001 for canopy sensor I2C link`
    — files: `docs/architecture/ADR-0001-canopy-sensor-i2c-link.md` (new) +
    `docs/session-log.md` (whole file, both the 07-10 and 07-15 entries riding
    together since they couldn't be cleanly split via `git add -p` — no
    unchanged line between the two appended blocks).
  - `7dbe451` — `feat(proxy): integrate BLE-proxy config into common
    radio_config schema` — files: `src/proxy/{__init__,configure,
    configure_params,fetch_node_config}.py` (new) + `src/common/radio_config.py`,
    `src/gateway/{configure,configure_params}.py`,
    `src/node/{configure,configure_params}.py`, `mesh_config.json`,
    `.env.example`.
- Working tree confirmed clean after both commits; branch is 2 commits ahead
  of `origin/main` (not pushed — no push/PR was part of the approved plan).
### Decisions
- Stayed on `main` for both commits rather than branching, matching the
  repo's all-direct-to-main history (4 prior commits all on main, no PR
  workflow exists).
- `docs/session-log.md`'s modifications went wholesale into Commit 1 (paired
  with the other pure-docs change) rather than split — the two dated entries
  had no clean hunk boundary.
- No push, no PR, no release tag — none were recommended (no PR workflow in
  this repo; neither change is release-triggering).
### Outcomes
- Both work packages committed; working tree clean; 2 commits ahead of
  `origin/main` awaiting a future push decision.
### Next steps / open questions
- Owner: decide when to push `3795e08`/`7dbe451` to `origin/main`.
- Still pending (carried over, unchanged by this checkpoint):
  `project-overview.md` still needs doc-keeper's update (ADR-0001 + proxy
  integration unreflected) — flagged this session but not actioned, since
  owner picked the git checkpoint path instead.
- Still pending, hardware-dependent (owner): real node IDs for p1/p2 (replace
  `!CHANGE_ME_P1/P2` in `mesh_config.json`), set `LORA_MSG_CHANNEL_PSK` in
  `.env` (note: `.env` var also renamed `LORA_CHANNEL_PSK` →
  `LORA_TELEMETRY_CHANNEL_PSK` in this commit — any live `.env` needs updating
  before next `configure.py` run), then re-run `configure.py` on nodes 1-3 +
  gateway (new channel 1) and on p1/p2.
- Backlog unchanged: Dependabot alerts (2 high, 2 moderate) untriaged;
  node-label mismatch (monitor/app.py vs mesh_config.json); license decision
  pending; deferred scalability/power study + RPi+5G exploration.

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
