# 2026-10-27_m1s-smartturn-int8-fp32-mocks

> Plan de adaptación de la prueba "¿el techo de smart-turn lo pone el tiempo de cada inferencia? (int8
> contra fp32)" (`/srv/memories/mocks_workers_gb/prueba_smartturn_int8_vs_fp32.md`) para correrla con
> los mocks locales de los workers en vez de Modal. Estado: **CORRIDO 2026-10-27 01:13-03:52 UTC. Veredicto preregistrado: EFECTO. Dictamen ciego de Codex: lo sostiene, con confusor de decisiones del modelo.**

## Paso de la estrategia

M1 (línea base del monolito) → pregunta de mecanismo sobre su techo. Prerequisito del paso 5 del
`AUDITORIA_20261019.md` §9 (quitar `_inference_lock`).

## Hipótesis

Si el `run` de ONNX bajo el candado global fija el techo, un modelo con `run` más corto (int8, 27 ms
aislado contra 48 del fp32) sube el techo: a 100 llamadas `inference_ms` p50 cae de ~900 ms a cientos,
el mínimo deja de estar en ~850 y los intentos/s superan la meseta de 12-13. Si no cambia, el techo
no es el tiempo del `run`.

## Variable que cambia

Exactamente una: el archivo del modelo vía `SMART_MODEL`, dentro de la misma imagen.
Control = `/app/smart-turn/smart-turn-v3.2-gpu.onnx` (fp32, sha `ab8dc64b…`).
Variante = `/app/smart-turn/smart-turn-v3.2-cpu.onnx` (int8, sha `2bb02631…`). Verificados en la imagen
por `preparar.sh` → `config/modelos_imagen.txt`.

## Lo que queda fijo (igual que M1 salvo lo marcado [MOCKS])

| Pieza | Valor | Lectura de vuelta |
|---|---|---|
| Imagen | `goldenbridge:remarcacion-v1-jef-fp32` (8485ac9f) | `parametros.txt`, `efectivo.txt` imagen= |
| Config base | `~/m1_base.yaml` sha `15507f5f…` → **[MOCKS]** `config/m1_mocks_base.yaml` (brazo A de `mock_cfg.py`: call_summary igual, URLs 127.0.0.1:1808x, `server.host` 127.0.0.1, RunPod neutralizado) | `preparar.sh` aborta si el sha base no es 15507f5f; `mock_cfg.py validate` sobre la base y sobre el `config_lab.yaml` FINAL antes del `docker run` |
| Umbral smart-turn | 0,35 | en la config validada |
| Mensajes | 60 ms, `MIDSPEECH_N=17` (≈1 s) | `parametros.txt` |
| CPU | GB `2-5,10-13` (`--cpus 8`), cliente `6,14`, **[MOCKS]** mocks `0-1,8-9` | cgroup `cpuset.cpus.effective`; `/proc/<pid>/status Cpus_allowed_list` de los 3 mocks (aborta si no coincide) |
| Hilos | `1 1 1` | `efectivo.txt` |
| Cliente | `td1_client_m1.py` de M1 (sha en parametros), modo espera, hueco 800 ms, barge-in 1800 ms en 1 de cada 3 sesiones, 3 sesiones de control | `cliente_sha256` |
| Red | **[MOCKS]** `--network host`, sin `-p`; GB escucha sólo en 127.0.0.1:8777 | `efectivo.txt escucha_host=`; aborta si aparece 0.0.0.0, `*` o `[::]` |
| Credenciales | **[MOCKS]** `.env.mocks` de relleno; se niega a usar `/srv/GoldenBridge/.env` | `parametros.txt credenciales=` |
| Proveedores | **[MOCKS]** STT 18081 (130 ms), LLM 18082 (router 450, TTFT 400, tpot 15, AMD 300), TTS 18083 (40 ms, 65 ms/char, rtf 1.0) sin cambios respecto a la calibración | `parametros.txt mocks_sha256_*`, `mocks_cmdline`, `mocks_pre/post.txt` por escalón |
| Gate de arranque | `referencia OK` + `Smart Turn processor created … model_path=$SMART_MODEL` | igual que M1 |
| Ventana | ramp 60 + warm-up 60 descartado + 600 estables (control corto: 300) | `parametros.txt` |

## Las imitaciones de los proveedores (mocks)

Tres servidores locales reemplazan a los workers de Modal. Están en `~/analisis_tmp/loopab/mocks/` (`mock_stt.py`,
`mock_llm.py`, `mock_tts.py`), escuchan en `127.0.0.1` y se arrancan con `start_mocks.sh`. **No transcriben, no razonan
ni sintetizan:** contestan con el mismo protocolo y el mismo tamaño de mensajes, con demoras fijas. GoldenBridge usa sus
clientes de siempre, sin cambios; sólo cambian las URLs de la config.

### Latencias

Las imitaciones usan demoras fijas, tomadas de `start_mocks.sh` L55-57 `[CÓDIGO]`. Lo real es la mediana de M1 a 100 llamadas,
`resultados/20261019T210000Z` `[MEDIDO]`.

| Proveedor | Qué se mide | Imitación | Real |
|---|---|---|---|
| STT | de la orden de cerrar (Finalize) al texto | 130 ms | 122 ms |
| LLM | primera palabra | 400 ms | 254 ms |
| LLM | resto de la respuesta | 16 palabras, una cada 15 ms: 225 ms | variable, 52 ms la mitad de las veces |
| TTS | primer bloque de voz | 40 ms | 44 ms, pero 166-201 ms en el 10 % más lento |
| TTS | ritmo del resto | tiempo real exacto | ~18 % más rápido que tiempo real |

No se usan en esta prueba porque el laboratorio no tiene campaña: el router de intención, a 450 ms, y el detector de
buzón, a 300 ms.

Las imitaciones no tienen colas largas. Lo real llega a 3 s en el STT y varía de una llamada a otra. Esa varianza es lo
que se quita a propósito, para que los dos brazos reciban el mismo estímulo.

### ¿Usan el mismo protocolo?

Sí, el que usa GoldenBridge con cada worker:

| Proveedor | Cómo se conecta | Qué intercambian |
|---|---|---|
| STT | WebSocket `/v1/listen?language&sample_rate=16000&encoding=linear16&vad=false&token` | al conectar, un mensaje `Metadata`; GB manda audio binario PCM de 16 bits a 16 kHz y `Finalize`, `KeepAlive` o `CloseStream`; la imitación responde un `Results` con `is_final`, `speech_final` y `from_finalize` |
| LLM | HTTP `POST /v1/chat/completions`, compatible con OpenAI | la misma forma de pedido y de respuesta en streaming (SSE) que espera el cliente de GB, terminando en `[DONE]` |
| TTS | WebSocket persistente `/v1/tts/ws/{voz}?api_key=`, más `GET /v1/voices` | GB manda `{"text", "flush", "request_id"}`; la imitación responde bloques binarios de voz, un bloque vacío y `{"status":"complete","request_id"}` |

Diferencias de transporte:
- Las conexiones son sin cifrado (`ws://` y `http://`), porque todo va por la red local del servidor.
- Las claves se aceptan sin validar.

Diferencias de contenido:
- **STT:** devuelve siempre una de cuatro frases fijas, "sí", "bueno", "no, gracias" y "sí, claro, dígame", y sólo si hubo
  voz en el audio. Lo decide midiendo la energía, sin reconocer palabras.
- **LLM:** responde siempre la misma frase, de 93 caracteres.

Los tres se probaron con los clientes reales de GoldenBridge (`test_mock_stt.py`, `test_mock_llm.py`, `test_mock_tts.py`)
`[MEDIDO 2026-10-24]`.

### Cómo entrega el audio la imitación de TTS

1. **Una fila por conexión.** Cada llamada tiene su WebSocket; los textos que llegan se atienden en orden, de a uno.
2. **Genera una señal que suena a voz**, sin palabras:
   - un tono de 180 Hz con sus armónicos y una leve variación;
   - una envolvente de unas 5 sílabas por segundo;
   - un poco de ruido.
3. **El largo depende del texto:** 65 ms de audio por carácter. La respuesta fija del LLM se manda en 2 pedidos, de
   67 y 23 caracteres: ~5,85 s de voz por turno.
4. **Formato igual al worker real:** PCM de 16 bits, mono, a 48 kHz, en bloques de 16.384 bytes. Cada bloque son 170,7 ms
   de audio.
5. **Ritmo:**
   - espera 40 ms antes del primer bloque;
   - después manda un bloque cada 170,7 ms, contados desde el primero, sin acumular atraso.
6. **Al terminar:** un bloque binario vacío y `{"status":"complete","request_id"}`. GoldenBridge usa el `request_id` para
   no confundir respuestas.
7. **Si la persona interrumpe:** la imitación sigue mandando el audio cancelado y GoldenBridge lo descarta, igual que con
   el worker real.

GoldenBridge convierte esa voz de 48 a 8 kHz y la envía a la llamada al ritmo en que se escucha.

Dos diferencias con el worker real, KDVoz:
- **El ritmo de entrega.** El real entrega ~18 % más rápido que tiempo real; el pacer de GB absorbe la diferencia.
- **El primer bloque.** El real a veces tarda ~160 ms en lugar de ~40 ms.

Detalle completo y la comparación con lo real: `/srv/memories/mocks_workers_gb/fidelidad_mocks_vs_reales.md` y
`LEEME_HANDOFF.md` en esa misma carpeta.

## Cambios respecto a `correr.sh` de M1 (todos marcados `[MOCKS]` en `correr.sh`)

1. **Script nuevo**, no edición del de M1: `correr.sh` de esta carpeta; cliente, `analiza.py` y extractor se toman de `M1_DIR` sin copiar.
2. **Cadena de config:** `preparar.sh` → `mock_cfg.py generate --src ~/m1_base.yaml --arm A` → `config/m1_mocks_base.yaml` (validado). `correr.sh` fusiona cycle_probe + midspeech + `SMART_MODEL` dentro de la imagen (igual que M1) y **vuelve a validar el archivo final** que se monta.
3. **Red del host** en vez de puerto publicado. Trade-off declarado: el cliente ya no pasa por el reenvío de puerto de docker; igual en los dos brazos.
4. **Credenciales de relleno** (`.env.mocks`, modo 600). Los mocks no las validan.
5. **Mocks fijados** con `taskset -c 0-1,8-9 bash start_mocks.sh start` (los hijos heredan la afinidad; no se edita `start_mocks.sh`). Leído de `/proc`.
6. **CPU de los mocks** muestreada cada 5 s por escalón (`mocks_cpu.tsv`, % de un núcleo por mock) y foto de `/health` y `/stats` antes y después (`mocks_pre/post.txt`).
7. **Lectura de vuelta del camino integrado** por escalón (`lectura.txt`): router=0, AMD=0, `not available`=0, TTFT del LLM ≈ Finalize con texto, `athrow`/`aclose` como telemetría, espera antes del TTS (`LATENCY BREAKDOWN tts_latency_ms`) contra primer chunk.
8. **Primarias:** `auditoria_20261019_extrae.py` por escalón → `resultados/<SELLO>/auditoria/<SELLO>_<paso>/resumen_auditoria.tsv` (intentos/s, cierres/s, `inference_ms` p50/p90/mín, intentos por cierre). `resumen.tsv` de `analiza.py` no las trae.
9. **`process_cpu_count_dentro`** leído del contenedor (esperado 8 → executor por defecto de 12 hilos); `nproc_dentro` queda anotado como artefacto de `OMP_NUM_THREADS=1`.
10. **Compuerta del control** (`compuerta_control.py`) antes de las 4 escaleras.
11. **Orquestador** `noche.sh` con tope horario (`TOPE_UTC` 05:45; DEV se apaga 06:00) y corridas desacopladas (`setsid nohup`).

## Criterios preregistrados

| criterio | aceptación | rechazo |
|---|---|---|
| escalón válido (de M1, `analiza.py`) | `valido=si`, `gen_lag_p99 ≤ 5 ms`, muertes 0, `errores_gb` 0, cliente rc 0, hilos `1 1 1` | cualquiera falla ⇒ el escalón no se interpreta |
| dato requerido | TODA clave de `resumen_auditoria.tsv`, `lectura.txt`, `efectivo.txt` y `resumen.tsv` que lee la compuerta existe y es numérica (B1) | un faltante ⇒ INTEGRACION_FALLA, nunca aprueba |
| camino integrado | router 0, AMD 0, `not available` 0 en todo el log; en la ventana estable: Finalize con texto > 0, LLM grande > 0 y ≥ 0,8 × Finalize con texto, muestras de TTS > 0 | si no ⇒ INTEGRACION_FALLA, parar |
| **control corto SATURADO** (fp32, 100+3, 300 s) | `infer_ms_p50 ≥ 500`, `infer_ms_min ≥ 300`, intentos/s ≥ 11 (M1 real n=100: 912 / 854 / 13,11; n=75: 231 / 77 / 11,2 ⇒ AMBIGUO) | `p50 < 200` y `min < 120` ⇒ NO_SATURADO; intermedio ⇒ AMBIGUO |
| sin cuello TTS | espera TOTAL del turno antes del primer audio (`LATENCY BREAKDOWN tts_latency_ms`, ventana estable): p50 ≤ 300 ms y p90 ≤ 1000 ms (M1 real n=100: 45 / 166) | si no ⇒ CUELLO_TTS: acortar la respuesta del mock, no subir N |
| **veredicto A/B** (`veredicto_ab.py`, 4 escaleras; los 12 escalones pasan la MISMA validación estricta que el control: `compuerta_control.evaluar`) | **EFECTO**: las dos corridas int8 por debajo del rango de los dos controles fp32 en `infer_ms_p50` Y `infer_ms_min` a 100. **SIN_EFECTO**: las dos dentro del rango, con ambos controles SATURADOS = ausencia de efecto detectado con esta demanda. | **INCONCLUSO**: cualquier escalón inválido/no integrado, control no SATURADO, cuello TTS a 100 en cualquier corrida, o las dos corridas int8 en desacuerdo (B5). EFECTO_INVERSO si quedan por encima. |
| telemetría comparada entre brazos, no criterio | `athrow` / `aclose(): already running` (M1 real n=100: 216 / 1804), `auto_flush_n`, `drained_stale_tts`, CPU de los mocks, intentos/s y cierres/s (la demanda los acota) | — |

Por qué el cuello TTS se mide con la espera total y no restando el primer chunk (B2, Codex ronda 2): restar
medianas de dos poblaciones distintas (breakdowns por turno, primeros chunks por petición) no estima la
espera mediana, y el primer chunk del mock NO es constante: su escritor es secuencial por conexión y sigue
mandando el audio cancelado por barge-in, así que una petición nueva puede esperar al audio viejo. Lo que
paga el turno es `tts_latency_ms` entero (desde que GB decide sintetizar hasta el primer audio: semáforo de
50, cola del mock y primer chunk), y eso es lo que se acota. El primer chunk se reporta aparte como contexto.

Qué hacer según la compuerta: SATURADO → 4 escaleras. NO_SATURADO / AMBIGUO → acortar `LLM_REPLY` del
mock a una oración de ~40-45 caracteres sin delimitadores internos y/o subir N (tope 117 = 120 − 3) y
repetir el control; cada ajuste queda registrado en `corridas.tsv` y `secuencias/`. CUELLO_TTS → acortar
la respuesta. INTEGRACION_FALLA → no seguir; diagnosticar con `gb.log`.

**Re-anclaje del preregistro:** las predicciones numéricas del diseño (p50 < 300 ms, demanda 14-15/s)
se calcularon con proveedores reales. La regla de efecto es la de arriba. Si la variante int8 queda sin
fila (mín ≈ 72-77) mientras los controles saturan con la misma demanda, eso ES el efecto predicho
(run más corto ⇒ sin cola); que intentos/s "no suba más" no se lee como techo ajeno, porque la
demanda la acota el cliente en modo espera (observación de Codex).

## Verificaciones hechas sin arrancar nada (2026-10-27)

| Qué | Resultado |
|---|---|
| `preparar.sh` | config generada y validada (1 URL neutralizada), sha base 15507f5f…, los dos modelos en la imagen |
| `compuerta_control.py` sobre crudos REALES de M1 (`20261019T210000Z`) | n100 → SATURADO rc 0; n010 → NO_SATURADO rc 3; TTS sintético +400 ms → CUELLO_TTS rc 5 |
| B1: 16 pruebas negativas (quitar cada archivo/clave, `valido=NO`, `cliente_rc=1`, router=3, `llm_ttft_n=0`, NaN) | 16/16 → rc 6 |
| `lectura.txt` con el snippet real de `correr.sh` (ventana [122, 720] s) sobre M1 n100 | 4915 turnos, LLM 4442 ≈ Finalize con texto 4435, TTS breakdown p50 45 / primer chunk 43 |
| `veredicto_ab.py --test` (10 casos sintéticos: efecto, sin efecto, control no saturado, inválida, desacuerdo, cuello TTS p50 y p90, falta corrida, inverso, mixto; cada uno también imprime sin lanzar, N1) | 10/10 |
| `veredicto_ab.py` con el layout real de M1 (sólo n100 y n010 presentes) | INCONCLUSO con motivos por escalón, sin excepción |
| `noche.sh` en seco (`NOCHE_DRY=1`): modo inválido, sin modo, `escaleras` sin/with CONTROL_SELLO, control no SATURADO, SOLO, compuerta rc 3/0, `todo` (5 corridas), tope a 5 min, flock, TERM | 13/13 con los rc esperados; `limpiar` corre con TERM (rc 143) |
| `cabe()` a las 00:12 UTC | 40 min sí, 400 min no |
| `bash -n` de los 4 scripts, `py_compile` de los 2 python | OK |

## Límites de la limpieza y escrituras fuera de la carpeta

- TERM/INT/HUP a `noche.sh` o a `correr.sh` corren los traps: borran `m1slab` y paran los mocks.
  **SIGKILL no.** Limpieza manual: `docker rm -f m1slab` y `bash ~/analisis_tmp/loopab/mocks/start_mocks.sh stop`.
- Un solo `noche.sh` a la vez (`noche.lock`). `start_mocks.sh` sólo serializa start/stop.
- `noche.sh` para los mocks al salir aunque ya estuvieran arriba antes.
- Escribe fuera de la carpeta: `~/analisis_tmp/loopab/mocks/{mocks.pids,.mocks.lock,mock_*.log}` (de `start_mocks.sh`)
  y el almacenamiento de Docker. En `/srv/GoldenBridge` sólo lee (python, audios, git); `PYTHONDONTWRITEBYTECODE=1`
  y `GIT_OPTIONAL_LOCKS=0` exportados.
- `cliente_cpuset_leido` repite lo pedido (herencia de M1); la afinidad real del cliente no se lee de `/proc`.

## Lo que esta corrida NO mide (declarado)

Cortes a mitad de frase, latencia y fallos del STT (fijos), primer audio absoluto, valor absoluto del
techo con Modal. Brechas de fidelidad conocidas y aceptadas: ver
`/srv/memories/mocks_workers_gb/fidelidad_mocks_vs_reales.md` §6.

## Cómo correrlo (servidor DEV, con go explícito)

```bash
cd ~/goldenbridge-analisis/experimentos/2026-10-27_m1s-smartturn-int8-fp32-mocks
bash preparar.sh                                              # config + sha + modelos; sin arrancar nada
setsid nohup bash noche.sh control   > noche.log 2>&1 &       # control corto + compuerta (≈12 min)
tail -f noche.log
# si la compuerta dice SATURADO (rc 0), con el sello del control:
CONTROL_SELLO=<sello> setsid nohup bash noche.sh escaleras > noche.log 2>&1 &   # 4 × ≈37 min, tope 05:45 UTC
# o todo seguido:
setsid nohup bash noche.sh todo      > noche.log 2>&1 &
# al terminar las 4:
python3 veredicto_ab.py corridas.tsv
```
`noche.sh` arranca los mocks fijados (o reutiliza los que ya estén arriba y fijados) y los para al salir.
Cada corrida va bajo `timeout` con el plazo absoluto hasta `TOPE_UTC`; entre 05:45 y 14:00 UTC no arranca
nada. Una escalera fallida detiene la secuencia. Si no caben las cuatro, las pendientes se corren al día
siguiente desde las 14:00 UTC con `SOLO="C_baja V_baja" CONTROL_SELLO=<sello de un control corto NUEVO>`.
`escaleras` exige que el control sea SATURADO y que config, mocks e imagen tengan los mismos sha256.
Registro por secuencia en `secuencias/<ts>_<modo>/secuencia.txt` y en `corridas.tsv`.

Presupuesto: control 12 min + 4 escaleras × 37 min ≈ 2 h 40. Arrancando antes de las 02:30 UTC termina antes del tope.

## Resultado

Control corto `20261027T011336Z`: SATURADO (p50 852, mín 818, 14,0 intentos/s). Escaleras: C_sube `20261027T012224Z`,
V_sube `20261027T015959Z`, C_baja `20261027T023736Z`, V_baja `20261027T031510Z`, todas rc 0 y los 12 escalones válidos.

| n=100 | p50 | mín | p90 | intentos/s | cierres/s | intentos/turno | espera TTS p50/p90 |
|---|---|---|---|---|---|---|---|
| C_sube fp32 | 858 | 820 | 885 | 13,92 | 7,23 | 1,68 | 57/64 |
| C_baja fp32 | 857 | 819 | 886 | 13,92 | 7,28 | 1,68 | 57/64 |
| V_sube int8 | 68 | 39 | 136 | 16,11 | 9,87 | 1,54 | 180/474 |
| V_baja int8 | 67 | 39 | 140 | 16,00 | 9,90 | 1,53 | 184/474 |

**Veredicto `veredicto_ab.py`: EFECTO.** Codex recalculó las primarias desde los 12 gb.log (ventana [122,720] s) y coinciden.

Lo que NO se concluye (dictamen ciego de Codex):
- No separa espera del candado, `run` y cola del executor (`inference_ms` = features + candado + run; excluye la cola del executor).
- **Confusor de decisión:** a n=100 el int8 hace menos mid-speech (382/355 contra 616/606) y 0 fallbacks (contra 99/95);
  intentos por turno −9 %. Explica parte de la mejora de caudal; no toda: a n=50, con la misma demanda (8,3/s), la mediana ya baja de ~90 a ~41 ms.
- 16 intentos/s no es el techo del int8: el cliente espera al bot y la demanda sube con el sistema más rápido.
- Cuellos secundarios en la variante a n=100: espera TTS p90 474 ms, `cycle_p90` 39-54 ms, `sano=NO` (válido).
- Nada absoluto sobre Modal ni sobre calidad de modelos. El int8 no es candidato de producción (portabilidad de CPU en Modal).

Siguiente prueba propuesta (fp32): factorial candado activado/desactivado × executor explícito 12/24 hilos, orden aleatorio,
con sellos por intento (cola del executor, features, espera del candado, `run`) y concurrencia real de `run`.
