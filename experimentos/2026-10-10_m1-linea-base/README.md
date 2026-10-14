# 2026-10-10 · M1 · línea base del monolito a 60 ms

## Paso de la estrategia

**M1 (M60)** — `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` §3 y §6-§7. Es la vara contra la que se miden P (M2), A y S1. Y decide algo por sí sola (§7, "beneficio"): **si el monolito ya cumple a N = 100 con 60 ms, no se reescribe por capacidad.**

## Qué se sabe antes de correr

- `[MEDIDO]` La rodilla es 16-25 sesiones, **a 20 ms por mensaje** (en `.143`, con co-tenencia). A 60 ms, que es la cadencia que va a entregar el coalescer del ai-engine, **nunca se midió**.
- `[CÓDIGO]` **La imagen `goldenbridge:314t` es el monolito `api` limpio.** `src/` y `config/` de `24f9c6b` (de donde sale la imagen) son idénticos a `api@08932a6`. Lo único que agrega `feat/modal-migration` es Docker y documentación. Medir la imagen es medir `api`, que además es lo que va a Modal.
- `[CÓDIGO]` Producción en `.143` corre otra cosa: `feat/f261-call-summary@fb8a3b9` = `api` + 24 commits, con 393 líneas más en `websocket_server.py`. **M1 no mide producción.** Si hace falta comparar, se corre la misma escalera con una imagen construida desde `fb8a3b9`, como otra campaña.
- `[CÓDIGO]` A 60 ms, `smart_turn.midspeech_check_every_n_chunks` cuenta **mensajes**, no tiempo: 50 mensajes pasan de 1 s a 3 s. Se fija en 17 (≈ 1 s), como recomienda el runbook. `inbox_batch_max: 10` también cuenta mensajes (600 ms de audio por viaje). Se deja como está, porque es lo que correría.

## Hipótesis

Con mensajes de 60 ms el loop paga un tercio de los viajes por segundo de audio. Si el cuello es el costo por viaje (submit + resume), la rodilla debería subir **hacia 50-75 sesiones** `[INFERIDO]`. Si no sube, el costo no está en el conteo de viajes y M2/A tienen que atacar otra cosa.

## Variable que cambia

Sólo **N**: 10 → 25 → 50 → 75 → 100 llamadas vivas. Imagen, config, CPU reservada, audios y cliente quedan fijos en toda la escalera. Cada escalón arranca un contenedor nuevo, para que no arrastre estado del anterior. Las repeticiones pares recorren la escalera al revés: así el calentamiento de la máquina o de los proveedores no se confunde con N.

## Diseño

| | |
|---|---|
| servicio | contenedor `m1lab` de `goldenbridge:314t`, puerto **8776** (producción sigue en 8765, intacta) |
| config | `active.yaml` de producción + `server.cycle_probe: true` + `smart_turn.midspeech_check_every_n_chunks: 17` + `smart_turn.model_path` forzado a la ruta dentro de la imagen (`active.yaml` puede traer una ruta de `/srv` que en el contenedor no existe). Se guarda en `resultados/<sello>/config_lab.yaml` |
| CPU | `--cpus 8` y, si se da, `--cpuset-cpus`; se lee de vuelta el cgroup de adentro (`cpu.max`, `cpuset.cpus.effective`, `nproc`) |
| cliente | `cliente/td1_client_m1.py` (T-D1 + tres métricas, ver `cliente/PROCEDENCIA.txt`), en núcleos aparte |
| carga | N sesiones de habla (audios `utterances/listos`, 5,2 s, pausa 8 s) + 3 de control en silencio a 5 fps; 1 de cada 3 interrumpe al bot a los 1.800 ms |
| tiempos | rampa 60 s · calentamiento 60 s (descartado) · **10 min estables** por escalón |
| proveedores | **reales** (Gotenks STT/LLM/TTS, Ursula), con las credenciales de `GB_ENV` |

## Métricas (todas en la ventana estable salvo muertes y errores)

| columna de `resumen.tsv` | qué es | de dónde |
|---|---|---|
| `cp_cycle_p90_med` / `_p90` | mediana y p90 de los p90 por ventana de 5 s del ciclo por lote (pre + submit + job + resume + post) | CYCLE-PROBE |
| `cp_resume_vad_p90_*`, `cp_submit_*`, `cp_job_*` | las fases que en la curva histórica crecen ×35 frente al trabajo ×2,9 | CYCLE-PROBE |
| `cp_fpc_med` | tramas por ciclo; a 60 ms el mínimo es 3 | CYCLE-PROBE |
| `ping_ctrl_p50/p99` | RTT de las sesiones de control: retraso del loop que **no** pasa por el pool | cliente |
| `ttfab_p50/p90/p99` | fin de la voz del usuario → primer audio del bot (E2E, incluye proveedores) | cliente |
| `ttfab_perdidos` | frases que terminaron sin respuesta antes de la siguiente | cliente |
| `corte_p50/p90/p99` | inicio de la voz que interrumpe → último audio del bot (`fin=cortado`) | cliente |
| `gen_lag_p99_max` | peor p99 de retraso del generador entre sesiones | cliente |
| `cpu_nucleos_med`, `cpu_por_llamada` | `docker stats` cada 5 s | docker |

## Criterios preregistrados

**Escalón válido** (si no, no cuenta): `gen_lag_p99_max ≤ 5 ms` · cero muertes y errores del cliente · todas las sesiones aplicadas · hay líneas CYCLE-PROBE · producción con 0 llamadas al empezar.

**Escalón sano**, para un N válido, si se cumple todo:
1. `cp_cycle_p90_med ≤ 5 ms` y `cp_cycle_p90_p90 ≤ 20 ms` (meta de §7 para la decisión VAD; la curva histórica a 20 ms da 4,2 ms en 6-10 sesiones y 7,1 ms en 11-15);
2. `ttfab_p50` no empeora más de máx(10 %, 20 ms) contra el escalón de 10 de la misma repetición (§7, E2E);
3. `ping_ctrl_p99 ≤ 50 ms`;
4. `corte_p90` no empeora más de máx(10 %, 20 ms) contra el de 10.

**Resultado**:
- **K₆₀ (en esta caja)** = mayor N sano en **todas** las repeticiones. Que 100 pase sólo establece K₆₀ ≥ 100, no un techo.
- **Rodilla₆₀** = primer N con `cp_cycle_p90_med` > 2 × el de 10.
- Si **K₆₀ ≥ 100** → se aplica la regla de "beneficio" de §7 y M2/A pasan a ser opcionales.
- Si K₆₀ < 100 → M2 (P) y A se miden contra esta misma escalera y config.

## Lo que esta versión NO cumple de la estrategia — `[ABIERTO]`

1. **Proveedores reales, no simuladores** (§6). Los simuladores de Steno, Ursula y LLM son parte de C4 y no existen todavía. Consecuencias: (a) `ttfab` mezcla la latencia de GB con la de Gotenks bajo carga; (b) hay costo; (c) se comparte flota con producción. Lo interno (CYCLE-PROBE, ping de control) no depende de los proveedores, porque `steno_p90` es 0,10 ms plano. Por eso es esa parte la que decide la rodilla. Esta versión es **M1a**. La M1 con simuladores se repite cuando exista C4, sobre la misma escalera.
2. **CYCLE-PROBE mide desde que el lote empieza, no desde la lectura del socket** (§3 pedía "desde la lectura del socket"). La espera en la cola de entrada antes del lote no está en `cp_*`; la ve sólo el `ping_ctrl` de manera indirecta. Instrumentar la lectura del socket es un parche a GB que no está hecho.
3. **`corte` supone que GB manda el audio del bot a ritmo real.** Si lo manda en ráfagas más rápidas que tiempo real, el último audio llega antes de la interrupción y `corte` sale chico aunque no haya corte. No está verificado en el código. Se lee junto con `fin=natural`: si llegó `AgentAudioDone`, el bot terminó su frase.
4. **`active.yaml` es de `f261`, el código es `api`.** Claves que `api` no conoce se ignoran o fallan al arrancar `[ABIERTO]`. El arranque se aborta si no hay "referencia OK" o si algún proveedor queda "not available". Los valores efectivos se leen en `gb.log` (líneas `Silero VAD initialized` y `Smart Turn VAD processor initialized`).

## Costo y duración `[INFERIDO]`

Por repetición: 5 escalones × ~13 min ≈ **65 min**. Son Σ N × ~690 s ≈ **180.000 s-sesión (≈ 50 h de llamadas)**, del orden de 13.000 turnos completos con STT + LLM + TTS. Equivale a ~16 % del volumen de las 6 campañas medidas. Tres repeticiones ≈ 3,3 h y el triple de costo.

**Empezar con un ensayo corto** (~5 min de reloj; 10 sesiones × ~208 s ≈ 2.100 s-sesión ≈ 35 min de llamada, ~160 turnos) y mirar el resumen antes de la escalera completa.

## Preparar (una vez, en el servidor)

```bash
cd ~/goldenbridge-analisis && git pull && git switch paso/m1-linea-base

# 1 · audios recortados a 5,2 s (si no existen ya)
ls /srv/GoldenBridge/tools/loadtest/utterances/listos/ \
  || (cd /srv/GoldenBridge/tools/loadtest && python procesa_audios.py utterances --largo 5.2)

# 2 · python del cliente: websockets>=13 y audioop (python <= 3.12)
/srv/GoldenBridge/.venv_golden/bin/python -c 'import audioop; from websockets.asyncio.client import connect' && echo CLIENTE OK

# 3 · topología: qué vCPU comparten núcleo físico
lscpu -e=CPU,CORE
```

**Núcleos.** Elegir `GB_CPUSET` (8 vCPU = 4 núcleos físicos completos) y `CLIENTE_CPUSET` (un núcleo físico, sus 2 vCPU) sin compartir núcleo físico entre sí, y lejos de lo que use producción. Ejemplo si `lscpu` empareja *i* con *i+8*: `GB_CPUSET=2-5,10-13`, `CLIENTE_CPUSET=6,14`. A N = 100 se esperan ~2,3 núcleos de trabajo + 1,57 de spin `[MEDIDO: E1]`, así que 8 vCPU no deberían ser el límite. Si `cpu_nucleos_max` se acerca a 8, el escalón está limitado por CPU y hay que leerlo así.

**Credenciales.** `GB_ENV` no tiene default a propósito. El traspaso pide un juego propio, no el de producción. Usar el de producción (`/srv/GoldenBridge/.env`) es decisión tuya: los turnos se cobran y van a la misma flota de Gotenks. Sólo se pasan `GOTENKS_API_KEY URSULA_API_KEY CEREBRAS_API_KEY`. Logfire **no** se pasa, para no ensuciar la telemetría de producción. Los valores nunca se imprimen.

## Correr

Fuera de campaña (`run.sh` aborta si producción tiene llamadas, y se vuelve a revisar antes de cada escalón):

```bash
cd experimentos/2026-10-10_m1-linea-base

# ensayo: un escalón de 10, 2 min estables (~5 min)
GB_ENV=/ruta/al/.env GB_CPUSET=2-5,10-13 CLIENTE_CPUSET=6,14 \
ESCALERA="10" ESTABLE_S=120 ./run.sh

# escalera completa, una repetición (~65 min)
GB_ENV=/ruta/al/.env GB_CPUSET=2-5,10-13 CLIENTE_CPUSET=6,14 ./run.sh

# las dos repeticiones restantes (la 2.ª va al revés)
GB_ENV=/ruta/al/.env GB_CPUSET=2-5,10-13 CLIENTE_CPUSET=6,14 REPS=3 ./run.sh
```

Resultado: `resultados/<sello>/` con `manifiesto.txt`, `parametros.txt`, `config_lab.yaml`, `topologia_cpu.txt` y `resumen.tsv`. Lo crudo (logs de GB, TSV del cliente, `docker stats`) queda en `resultados/crudo/<sello>/`, que no se versiona.

## Devolver el resultado

```bash
git add experimentos/2026-10-10_m1-linea-base/resultados/*/{manifiesto.txt,parametros.txt,config_lab.yaml,topologia_cpu.txt,resumen.tsv}
git commit -m "M1: resultado de la escalera a 60 ms"
git push
```

`config_lab.yaml` sale de `active.yaml`, que según el traspaso no tiene claves. **Revisar antes de commitear** que no tenga ninguna. Si hay dudas, devolver sólo `resumen.tsv` y el manifiesto.

## Validación local del cliente (2026-10-10, laptop)

Contra un servidor falso con VAD de energía (responde 300 ms después de 300 ms de silencio y corta con 600 ms de voz encima), 4 sesiones a 60 ms durante 70 s:

| métrica | esperado por diseño | medido |
|---|---|---|
| `ttfab` | ≈ 600-700 ms | 630-739 ms |
| `corte`, `fin=cortado` | 18 cortes del falso, ≈ 650-750 ms | **18**, 689-747 ms |
| `corte`, `fin=natural` | ≈ 1.200 ms (lo que le quedaba al bot) | 1, 1.125 ms |
| `gen_lag` p99 | — | 22-28 ms: granularidad del temporizador de Windows; en Linux tiene que salir ≤ 5 |

La prueba encontró dos defectos del cliente que ya están corregidos:
- Al interrumpir saltaba al inicio del **archivo**. Con 3,7 s de silencio inicial, "interrumpía" con silencio, y `corte` medía el final natural del bot.
- `ttfab` se anclaba al final del archivo y no al de la voz, así que podía arrastrarse al turno siguiente: daba 13,7 s.

`analiza.py` se probó sobre esa salida, con un `gb.log` sintético en el formato de docker + structlog con ANSI.
