# Verificación del segundo techo: la espera de TTS con int8 a 100 llamadas

En la prueba con mocks, con int8 a 100 llamadas, la espera de TTS (`LATENCY BREAKDOWN tts_latency_ms`) sube a p90
~474 ms; con fp32, 64 ms. Estos scripts buscan de dónde sale, **sólo leyendo los logs ya existentes** (2026-10-27).

Se corren desde `experimentos/2026-10-27_m1s-smartturn-int8-fp32-mocks/`, sobre los crudos de `.143` (no están en git):

```bash
B=resultados/crudo
DIRS="$B/20261027T012224Z/r1_n{050,075,100} $B/20261027T015959Z/r1_n{050,075,100} $B/20261027T023736Z/r1_n{050,075,100} $B/20261027T031510Z/r1_n{050,075,100}"
eval python3 verificacion_tts/tts_split.py        $DIRS > verificacion_tts/tts_split.tsv
eval python3 verificacion_tts/tts_concurrencia.py $DIRS > verificacion_tts/tts_concurrencia.tsv
eval python3 verificacion_tts/tts_semaforo.py     $DIRS > verificacion_tts/tts_semaforo.tsv
eval python3 verificacion_tts/tts_little.py       $DIRS > verificacion_tts/tts_little.tsv        # lee tts_semaforo.tsv
eval python3 verificacion_tts/tts_little_cotas.py $DIRS > verificacion_tts/tts_little_cotas.tsv  # lee tts_little.tsv
```

Todos usan la ventana estable [t0+122, t0+720] s de cada escalón.

| script | qué responde | resultado |
|---|---|---|
| `tts_split.py` | ¿La espera se concentra en las sesiones que interrumpen (l0, l3, l6…)? | No: int8 a 100, p90 471-474 ms en las que interrumpen y 474-475 en las que no |
| `tts_concurrencia.py` | ¿Pesa más en las respuestas que siguen a una interrupción? ¿Cuántas síntesis hay abiertas a la vez, contando las completas? | No cambia (469-472 contra 476 ms). Síntesis completas abiertas a la vez: máximo 44-45, nunca 50 |
| `tts_semaforo.py` | ¿La espera está antes o después del permiso de síntesis (`asyncio.Semaphore(50)`, `tts_processor.py` L769)? | Antes: entre el reloj de GB y el del cliente TTS hay 119-124 / 414 / 645-659 ms (p50/p90/p99) con int8 a 100; 1 ms en los otros 11 escalones. La columna de "abiertas" de este script no es válida (no todas las síntesis registran su inicio) |
| `tts_little.py` | Ley de Little sobre el permiso: λ × W con las síntesis completas | L = 35 con int8 a 100; 18-27 en los demás. Las canceladas no dejan registro (0 `cancelled by barge-in`) |
| `tts_little_cotas.py` | Cotas de L sumando las canceladas, estimadas con las interrupciones (`Cancelling TTS`) | int8 a 100: L medio 44, L alto 48; los demás ≤ 37 |

Conclusiones y límites (detalle en la conversación del 2026-10-27):
- Se descarta que sea un artefacto de la imitación de TTS: la espera ocurre en GB, antes de obtener el permiso.
- Entre el primer reloj (`websocket_server.py` L5656) y el permiso no hay otra espera activa: la reconexión del TTS
  (`tts_processor.py` L313-327) ocurrió 0 veces. El semáforo de asyncio es FIFO: con una fila formada, los nuevos esperan
  aunque haya un permiso libre.
- Sin resolver: Little da una ocupación media de 44-48, por debajo de 50, pero la mediana de espera es 120 ms. Una
  explicación posible, sin confirmar: síntesis canceladas que no sueltan el permiso por cierres fallidos de generadores
  asíncronos (`aclose(): asynchronous generator is already running`, 5.452-5.680 en int8 a 100). Para cerrarlo hace falta
  registrar la toma y la liberación del permiso.
- `tts_concurrencia.py` se reescribió el 2026-11-09 a partir del original (perdido con el scratchpad); su salida coincide
  línea por línea con la del 2026-10-27.
