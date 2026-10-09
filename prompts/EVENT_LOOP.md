# El event loop de GoldenBridge — contexto para decidir cómo modificarlo

**Escrito el 2026-09-01.** Todo lo medido hasta hoy sobre el hilo que coordina
el servidor, y la decisión que queda abierta.

---

## EL PROMPT

> Necesito decidir **cómo modificar el planificado del event loop de
> GoldenBridge** en producción (.143). La decisión de *si* hace falta ya está
> tomada y medida — lo que falta es elegir el mecanismo y su forma de aplicarlo.
>
> Lee todo este documento antes de opinar. Las tres opciones están en §4 y la
> decisión concreta que hay que cerrar está en §5. No hace falta volver a medir
> nada de lo que está en §2 y §3; sí conviene cuestionarlo.
>
> Lo que necesito de vos: una recomendación entre A1, A2 y B, con el criterio
> que la sostiene, y qué habría que medir para confirmarla. Ojo con §6, que es
> el orden en que se pueden aplicar las cosas sin perder la atribución.

---

## 1 · El problema, en una frase

Sin GIL, el hilo del event loop es **un hilo más entre 24 (antes 41)** peleando
por 16 hebras bajo el planificador del kernel, que no sabe que ese hilo es
especial.

Con GIL, el testigo circulaba cada 5 ms y el loop entraba en esa rotación —
había un mecanismo, malo pero existente, que le garantizaba turno. Al quitar el
GIL para ganar paralelismo, **el paralelismo que ganamos le come el turno al
hilo que coordina todo**.

Esto no es teoría: está medido en las dos secciones que siguen.

---

## 2 · El experimento del sandbox (26-ago-2026)

Caja `c7i.4xlarge`, **Xeon Platinum 8488C — la misma CPU que producción**.
Tres bursts con habla y barge-in, 50 sesiones, carga idéntica verificada:
675.991 / 676.021 / 676.046 frames.

| | A: 12 workers | B: 6 workers | C: 12w + **loop apartado** |
|---|---|---|---|
| espera en cola del loop | 2.073,5 ms | — | **132,9 ms** (−94 %) |
| expropiaciones / 5 s | 1.966 | — | **73** (−96 %) |
| `resume_vad` p50 | 8,90 ms | 2,00 | **0,40** (−96 %) |
| turnos completados | 1.076 | 897 | **1.081** |
| `inbox` p99 (frames) | 80 | 579 | **46** |
| CPU del proceso | 12,54 núcleos | — | **11,01** (−12 %) |

**Bajar workers de 12 a 6 es una trampa** (corrida B): `resume_vad` mejora 78 %
y el sistema empeora — 17 % menos turnos, `inbox` p99 siete veces peor (579
frames = 11,6 s). Cambia hambruna de loop por hambruna de pool. La mejora
aparente de `resume_vad` es artefacto: con menos workers cada ciclo carga más
frames (4,78 → 5,80), hay menos ciclos, y el tiempo se apila ANTES del ciclo.

**El 12 % de CPU ahorrado importa aparte:** confirma que parte de la regresión
de 3.14 es thrash de planificación, no trabajo real.

### Cómo se aplicó allá

`/srv/spike_c2/burst_workers/aparta_loop.sh <pid>` — reaplica en bucle porque
los hilos del pool se crean bajo demanda.

```
CPU 0          el hilo del loop, solo
CPU 8          libre a propósito — hermano SMT de 0, para que el loop
               se quede con el núcleo FÍSICO entero
CPU 1-6, 9-14  los otros hilos
CPU 7, 15      el cliente del burst
```

### Confusor pendiente de descontar

La corrida C tuvo 395 barge-in contra 452 de A (−13 %), parecido al −12 % de
CPU. Los turnos totales subieron igual (1.081 vs 1.076) y las caídas de espera
(−94 %) y expropiaciones (−96 %) no las explica una diferencia de trabajo del
13 %, pero hay que decirlo al informar.

---

## 3 · Lo que pasa en PRODUCCIÓN (medido el 1-sep sobre logs reales)

Esto es nuevo y **cambia la urgencia**. `resume_vad_p50` es el retraso del
event loop: mide cuánto tarda el loop en retomar después de que el worker
devolvió el resultado. Es el 98 % del campo `resume` del CYCLE-PROBE.

Pareado por concurrencia real (`active_sessions`), campañas reales:

| simultáneas | 27-ago (sin fase 1) | 31-ago (con fase 1) |
|---|---|---|
| 1–5 | 0,2 ms | 0,2 ms |
| 6–10 | 0,2 | 0,2 |
| 11–15 | 0,3 | 0,2 |
| 16–25 | 0,9 | **0,3** |
| 26–40 | 1,9 | **0,9** |
| **41–99** | **7,3** | sin datos (la campaña llegó a 37) |

**Dos lecturas, las dos importantes:**

1. **El loop NO se está muriendo de hambre con el tráfico de hoy.** Hasta 40
   llamadas simultáneas el retraso es de 0,9 ms. La afinidad es para el camino
   a las 100, no para el tráfico actual.

2. **Hay una rodilla alrededor de las 40 llamadas.** La curva va 0,2 → 0,3 →
   0,9 → **7,3**. No es lineal: se dispara. Y ese 7,3 ms de producción a 41-99
   sesiones **corrobora de forma independiente** los 8,90 ms del sandbox a 50
   sesiones. Dos cajas, dos cargas, dos métodos: el mismo número.

La fase 1 del arreglo de hilos (`GB_ORT_INTRA_OP=1`) ya cortó el retraso a la
mitad en las franjas altas (0,9 → 0,3 y 1,9 → 0,9). Parte del problema ya se
está resolviendo por otra vía.

### El ciclo completo, mismo corte (`cycle_p90`)

| simultáneas | 27-ago | 31-ago |
|---|---|---|
| 1–5 | 7,6 | 4,2 |
| 6–10 | 7,4 | 5,8 |
| 11–15 | 9,8 | 6,8 |
| 16–25 | 14,0 | 11,3 |
| 26–40 | 22,4 | 17,9 |
| 41–99 | 109,2 | — |

⚠ **Una cosa que empeoró y hay que mirar:** el p99 de `cycle_p90` subió con la
fase 1 en carga alta — 316,7 → 570,4 en la franja 16-25, y 494,6 → 621,8 en la
26-40. La mediana mejoró en todas. Es el mismo patrón que el p99 de
`turn_end_latency_ms`, que también subió. **Alguien debería explicar esa cola
antes de agregar otro cambio encima.**

---

## 4 · Las tres formas de cambiarlo

### A · Apartar el loop en un núcleo propio (afinidad)

Es la única medida. Producción tiene **la misma topología que el sandbox**:

```
16 núcleos lógicos · 8 físicos · 2 hilos por núcleo
hermano SMT de cpu0 = cpu8
```

Así que el mapa se traslada tal cual. Dos maneras de aplicarlo, y **acá está la
decisión**:

**A1 · Script al lado.** `taskset` funciona **sin sudo** (mismo uid,
verificado). Cero código, se revierte matando el proceso.

- *En contra:* tiene que correr en bucle, porque los hilos del pool se crean
  bajo demanda. Muere con cada reinicio del servicio y con el reinicio diario
  de las 14:00. Para que sea durable hace falta una unidad de systemd que lo
  levante — que ya es configuración de producción, así que el "cero código" es
  parcialmente falso.

**A2 · `os.sched_setaffinity` al arrancar, en el código.** Permanente, sobrevive
reinicios, sin proceso extra. La auto-afinidad no necesita privilegios.

- *En contra:* un commit y un despliegue. Y **el detalle que no estaba en el
  análisis viejo:** si el mapa se escribe fijo (`CPU 0` + `CPU 8`), **se rompe
  en el contenedor de Modal**, donde el cgroup da un subconjunto variable de
  CPUs y "el núcleo 0" no significa nada. A2 tendría que calcular el mapa en
  tiempo de ejecución desde `os.sched_getaffinity(0)` y la topología real. Eso
  lo vuelve más código del que parece.

**A3 · `systemd CPUAffinity=` — DESCARTADO.** Es a nivel de proceso, no de
hilo: le pondría el mismo corsé a los 24 hilos. No sirve para esto.

### B · Subirle la prioridad en vez de aislarlo

En lugar de darle un núcleo, decirle al kernel que ese hilo va primero: `nice
-20`, o política de tiempo real (`SCHED_FIFO` / `SCHED_RR`). Hoy corre en
`SCHED_OTHER` prioridad 0, el default.

**Sin medir.** Más barato que la afinidad —una llamada, sin reservar núcleos—
pero el riesgo es peor: con política de tiempo real, un loop que se cuelgue
puede dejar sin CPU a sus propios workers, y ahí no hay quien lo desatasque.
`nice` es seguro pero mucho más débil: sigue compitiendo, sólo que con ventaja.

**Es la alternativa a evaluar contra A**, y se mide igual.

### C · Varios event loops (split)

**Archivada.** El usuario la rechaza como forma final — la ve como
mini-instancias dentro de una instancia. Quedó como plan B en
`project_estrategia_100_llamadas`.

---

## 5 · La decisión que hay que cerrar

**¿A1, A2 o B?** Y si es A2, **¿mapa fijo o calculado?**

Lo que la inclina, en mi lectura, es que GoldenBridge va camino a un contenedor
en Modal. Un mecanismo que hay que reinventar allá vale menos que uno que
viaje con el código — pero sólo si el mapa se calcula, no se hardcodea.

**Lo que costaría la afinidad:** apartar el núcleo físico le saca **2 de 16 CPUs
lógicas** a los workers. En el sandbox salió positivo igual (más turnos, mejor
`inbox` p99), pero eso fue con la caja no saturada; con la caja llena el cálculo
puede cambiar y no está medido.

---

## 6 · Restricción de orden — leer antes de aplicar nada

**La fase 2 del ajuste de hilos y la afinidad atacan lo mismo:** el loop
peleando por CPU con los hilos del propio proceso.

Estado al 1-sep: la fase 1 (`GB_ORT_INTRA_OP=1`) está aplicada y medida. La
**fase 2** (`OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`) se aplicó el 31-ago,
se verificó que funciona —quince hilos de OpenMP desaparecieron, la referencia
siguió dando 12 verdes— y **se revirtió sin medirla con tráfico**.

Esos quince hilos quemaban ~1.400 s de CPU cada uno sin calcular nada: el 89 %
del CPU del proceso, 1,57 núcleos ardiendo de continuo. Son exactamente los que
le pelean el procesador al loop.

⇒ **Una variable por campaña.** Si se aplica la afinidad antes de que la fase 2
tenga su propia campaña, ninguna de las dos queda atribuible. Y es posible que
la fase 2 entregue buena parte de la mejora sin mecanismo nuevo.

---

## 7 · Datos técnicos de producción, verificados el 1-sep

| dato | valor |
|---|---|
| CPU | Intel Xeon Platinum 8488C, con `avx512_vnni` |
| núcleos | 16 lógicos, 8 físicos, SMT 2 · hermano de cpu0 = cpu8 |
| RAM | 30 GB |
| intérprete | Python 3.14.7 free-threaded, GIL apagado |
| event loop | **uvloop** (`src/main.py:405`) |
| el hilo del loop | el **principal**: `tid == pid`, confirmado en `ep_poll` |
| política actual | `SCHED_OTHER`, prioridad 0 |
| hilos del proceso | 24 en reposo con fase 1 · 9 con fase 2 · 41-42 con tráfico |
| ciclo de la caja | apaga 06:00, arranca 14:00 · `/tmp` se borra |

### Cómo medir, sin sudo y sin tocar nada

```bash
PID=$(systemctl show goldenbridge -p MainPID --value)
cat /proc/$PID/task/$PID/schedstat
#   campo 1: ns ejecutando   campo 2: ns ESPERANDO EN COLA   campo 3: turnos
grep ctxt_switches /proc/$PID/task/$PID/status
```

Es contabilidad del kernel, no muestreo. El campo 2 dividido por (campo1 +
campo2) es la fracción de tiempo que el loop pasa esperando CPU.

Y la herramienta para parear métricas por concurrencia:
`tools/medicion/parear_por_concurrencia.py` en el repo.

---

## 8 · Trampas de medición ya pagadas

- **`py-spy --gil` miente acá.** Dijo "GIL no saturado" y era falso; con uvloop
  no mide ocupación del loop. No usarlo para esto.
- **`pgrep` devuelve PIDs transitorios.** Hay que re-resolver el MainPID desde
  `systemctl` en cada muestra. Ya arruinó una corrida: `proc_w6.tsv` quedó
  vacío por engancharse a un PID que murió.
- **El `schedstat` se reinicia con el proceso.** Con el reinicio diario de las
  14:00 y cualquier `restart`, el contador vuelve a cero: hay que detectar el
  cambio de PID y descartar ese intervalo.
- **`resume_vad` sola se puede engañar.** Con menos workers mejora aunque el
  sistema empeore (la corrida B). Toda regla de decisión necesita **un número de
  punta a punta** además del diagnóstico por fase — turnos completados, `inbox`
  p99, `turn_end_latency_ms`.
- **Contar `session_id` distintos por minuto es rotación, no simultaneidad.**
  Da números 2-3 veces más altos. Usar `active_sessions`.

---

## 9 · Dónde está todo

| qué | dónde |
|---|---|
| experimento del sandbox | `/srv/spike_c2/burst_workers/` — `gb_{w12,w6,pin}.log`, `proc_{w12,pin}.tsv`, `td1_*.tsv`, `aparta_loop.sh`, `muestreo.sh` |
| ventanas de régimen | A `21:52:05-21:56:05` · B `22:04:16-22:08:17` · C `22:34:17-22:38:13` (descartados 60 s de rampa) |
| campaña del 27-ago | `/srv/memories/latency_incident/evidencia/informe_post_ficohsa_20260827.dump.log.gz` |
| campaña del 31-ago | `/srv/memories/latency_incident/evidencia/gb314_campana_20260831_completa.log.gz` (17 MB, 3.587 llamadas) |
| informe de la fase 1 | `docs/informe-fase1-hilos-31ago.md` en el repo |
| plan de la fase 2 | `/home/eraxiom/prompts/ajuste_hilos_pools.md` en .143 |
| acceso | `ssh -i ~/.ssh/ai_key_eraxiom eraxiom@16.58.43.143` |

**El informe del experimento de afinidad NUNCA se escribió** — los números de
§2 salen de la memoria del proyecto, no de un documento. Los datos crudos sí
están.
