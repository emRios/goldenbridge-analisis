# Arquitectura del stack de voz

Recorrido de una llamada, de punta a punta: **Asterisk (proveedor) → túnel → ai-engine (Python) → GoldenBridge**, y hacia dónde va la pieza de GoldenBridge con **Modal**. Este documento describe **solo la arquitectura** (piezas, roles y cómo se conectan); no cubre incidentes ni modos de falla.

---

## Estado actual (todo co-ubicado en `.143`)

```
 Marcador (AWS Lambda del proveedor)
        │  origina las llamadas de campaña
        ▼
 ┌─────────────────────────┐                          ┌──── .143  (AWS VPC · priv 172.31.7.92) ────┐
 │  Asterisk (proveedor)   │        ═══ TÚNEL ═══      │                                            │
 │  10.132.112.148 : 8088  │      red proveedor ⇄ VPC  │   ai-engine (Python)      GoldenBridge (GB)│
 │                         │                          │   AudioSocket :9092       WS 127.0.0.1:8765 │
 │  · ARI / Stasis (ctrl)  │◄── engine es cliente ────│──────────►                /v1/agent/converse │
 │  · AudioSocket (media)  │── Asterisk es cliente ───│─►  slin16  ══ loopback ══►  pipeline de voz  │
 │    slin16 320B / 20 ms  │                          │    320B ⇄ µ-law 160B      (STT/LLM/TTS)      │
 └─────────────────────────┘                          │        ◄══ TTS (µ-law) ══                    │
                                                       │                                            │
                              GB ⇄ STT/LLM/TTS remotos │──────────────────────────────► internet   │
                              engine → marcador (HTTP)  │──────────────────────────────► internet   │
                                                       └────────────────────────────────────────────┘
```

**Regla de oro de hoy:** el ai-engine y GoldenBridge viven en la **misma caja (`.143`)** y hablan entre sí por **loopback**. El único salto de red hacia la telefonía es el **túnel** hasta el Asterisk del proveedor.

---

## 1. Asterisk (del proveedor)

- **No es nuestro.** Corre en el host del proveedor, alcanzable en `10.132.112.148` (ARI en `:8088`). No tenemos acceso a su CLI/CDR.
- Las llamadas de campaña **las origina el marcador del proveedor** (un AWS Lambda) sobre este Asterisk; nosotros no marcamos.
- Expone dos planos hacia nuestro lado:
  - **Control — ARI / Stasis:** el engine gobierna los canales (bridge de patas, originación de la pata de media, eventos de ciclo de vida de la llamada).
  - **Media — AudioSocket (TCP):** el audio crudo de la llamada. Códec **PCM16 / slin, 8 kHz, mono, 320 B cada 20 ms**.

## 2. El túnel

Existe porque **ambos extremos son direcciones privadas de redes distintas** — la red de telefonía del proveedor (`10.132.112.0…`) y nuestra VPC de AWS (`172.31.0.0/20`). Sin túnel no se verían.

- **Qué transporta — los dos planos, en direcciones de conexión opuestas:**
  - **ARI:** el **ai-engine es el cliente** → conecta a `10.132.112.148:8088`.
  - **AudioSocket:** el **Asterisk es el cliente** → conecta a `172.31.7.92:9092` (el engine escucha ahí, ligado a la IP privada de la VPC).
- **Dónde termina:** en el **borde de la VPC**, no en `.143`. Desde la caja, el tráfico hacia el Asterisk sale por el gateway de la subred (`172.31.0.1` en `ens5`), no por una interfaz de túnel local.
- **Qué NO cruza el túnel:** los servicios remotos de GoldenBridge (STT/LLM/TTS) y la notificación al marcador (API Gateway) **egresan de `.143` directo a internet**, no por este túnel.
- **Ancla arquitectónica:** el túnel está atado a `.143`. Por eso, cuando GoldenBridge se mueva a Modal, **el ai-engine y el túnel se quedan** donde están (ver §5).

## 3. ai-engine (Python)

Nuestro. Es el **puente** entre Asterisk y GoldenBridge. Un solo proceso `asyncio`, corre en `.143`.

- **Hacia Asterisk:**
  - Cliente **ARI/Stasis** para el plano de control (bridge de patas, originación de la pata de media AudioSocket, eventos de la llamada).
  - Servidor **AudioSocket** para el plano de media: escucha en `172.31.7.92:9092`, recibe el audio del caller y le devuelve el audio del bot.
- **Hacia GoldenBridge:**
  - Cliente **WebSocket** contra GB en **loopback** (`127.0.0.1:8765`).
  - **Conversión de códec:** traduce el `slin16` de Asterisk (320 B/20 ms) a **G.711 µ-law (160 B)**, que es lo que consume GB — y a la inversa para el audio TTS de vuelta.
- **Además:** publica eventos de negocio (p. ej. solicitud de transferencia a un agente humano) hacia el marcador del proveedor por **HTTP** (el mismo camino admite Redis/RabbitMQ, hoy configurado en HTTP).

## 4. GoldenBridge (GB)

Nuestro. Es el **cerebro de la conversación**. Corre como proceso Python en `.143`, co-ubicado con el engine.

- **Interfaz:** servidor **WebSocket** en `127.0.0.1:8765`, ruta `/v1/agent/converse`, con un protocolo de agente de voz (estilo Deepgram Voice Agent). Recibe µ-law del engine y le devuelve el audio TTS.
- **Pipeline de voz (por frame):** `mulaw → pcm16` → resample (`soxr`) → **VAD** (Silero) → **detección de fin de turno** (smart-turn, modelo ONNX) → **STT** → **LLM** → **TTS**. El STT/LLM/TTS son **servicios remotos** (p. ej. STT por WebSocket remoto); GB los orquesta.
- **Máquina de estados de la conversación:** modela el diálogo y sus estados terminales (incluido el de transferencia, que dispara el handoff vía el engine).

## 5. Modal (estado destino de GoldenBridge)

El objetivo es **escalar GoldenBridge horizontalmente**. GB es la pieza que se mueve; **Asterisk, el túnel y el ai-engine se quedan en `.143`**.

```
                              ┌──────────────── Modal ────────────────┐
   ai-engine (.143)           │   contenedor GB   (cap. ~50 llamadas)  │
   engine ⇄ GB ya NO          │   contenedor GB   (cap. ~50 llamadas)  │
   es loopback: pasa a  ─────►│   contenedor GB   (cap. ~50 llamadas)  │
   red pública, un            │   …  Modal autoescala la flota  …      │
   único endpoint             └────────────────────────────────────────┘
```

- **Modelo de escalado:** cada contenedor de GB atiende **~50 llamadas**; **Modal gestiona la flota** (autoescala) y expone **un único endpoint**. El engine deja de conocer un GB fijo y habla con ese endpoint.
- **Cambio de topología:** el tramo **engine ⇄ GB pasa de loopback a red pública** — cada trama de 20 ms cruza la red en ambos sentidos (antes era `localhost`).
- **Imagen:** GB ya está empaquetado como imagen de contenedor lista para la flota (`goldenbridge:314t`). La **configuración y las credenciales van por fuera de la imagen** (secretos de Modal / archivos montados), no horneadas.
- **Requisito de hardware de la flota:** las CPUs deben tener **`avx512_vnni`** — el modelo smart-turn está cuantizado y su comportamiento depende del set de instrucciones del procesador.

---

## Puertos y direcciones de un vistazo

| Pieza | Escucha en | Rol de red |
|---|---|---|
| Asterisk (proveedor) | `10.132.112.148:8088` (ARI) | El engine es cliente de ARI; Asterisk es cliente del AudioSocket |
| ai-engine (AudioSocket) | `172.31.7.92:9092` | Servidor de media hacia Asterisk (a través del túnel) |
| GoldenBridge (WS) | `127.0.0.1:8765` `/v1/agent/converse` | Servidor del engine, hoy solo por loopback |
| `.143` | `16.58.43.143` (púb) / `172.31.7.92` (priv, VPC) | Aloja engine + GB + el extremo del túnel |

**Códecs:** Asterisk ⇄ engine = `slin16` (PCM16, 8 kHz, 320 B/20 ms) · engine ⇄ GB = `G.711 µ-law` (160 B).
