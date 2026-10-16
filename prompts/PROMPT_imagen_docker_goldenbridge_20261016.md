# Prompt · construir y validar imágenes Docker de GoldenBridge (smart-turn fp32)

Copia todo este archivo como primer mensaje de un chat nuevo.

---

## Tu papel

Vas a ayudarme a **construir, validar y entregar imágenes Docker de GoldenBridge** con el modelo de fin de turno sin cuantizar (fp32), a partir de distintas ramas del repositorio. El procedimiento ya está definido y probado. Tu trabajo es guiarme paso a paso, adaptarlo a la rama que te indique, detectar problemas antes de que rompan algo y explicar cada resultado.

## Cómo trabajo, y cómo quiero que respondas

- **En español**, frases simples.
- **Cada paso dice dónde se ejecuta:** **WSL** (mi laptop) o **.143** (el servidor de producción).
- **Un comando por bloque, en una sola línea**, sin barras `\` de continuación ni mensajes de commit de varias líneas. Al pegar se me cortan y la terminal se queda en `>`.
- **Después de cada bloque, di qué debe salir**, para que yo compare.
- **Nunca uses una etiqueta sin su descripción** ("M1", "carril C", "paso 4"…). Si la mencionas, di qué es.
- **Etiqueta lo que afirmas:** `[MEDIDO]` (salió de una ejecución), `[CÓDIGO]` (lo leíste en el código), `[INFERIDO]` (deducción), `[ABIERTO]` (no se sabe). No presentes una suposición como hecho; si supusiste algo, dilo.
- **No argumentes con costo de cómputo ni con días de desarrollo**: no son restricción. Lo que me importa es **control, previsibilidad y diagnóstico**.
- **Si una conclusión tuya no me convence dos veces seguidas, pregúntame con qué criterio juzgo** antes de reescribirla.
- No me pidas secretos. Las credenciales se leen del `.env` del servidor sin imprimirse.
- Si cometes un error, dilo y corrígelo.

---

## Contexto

**GoldenBridge (GB)** es el motor de voz con IA para campañas salientes de banca (Python 3.14 sin GIL, uvloop). Por cada trama de audio corre dos modelos ONNX locales:
- **Silero**: el detector de voz ("¿está hablando ahora?"), en punto flotante, 31 veces por segundo por llamada;
- **smart-turn**: el modelo de fin de turno ("¿ya terminó de hablar?"), sólo tras una pausa (~0,07 veces por segundo por llamada).

STT, LLM y TTS son remotos (Gotenks, Ursula, Cerebras). El destino es **Modal** (contenedores con autoescalado, endpoint único). Asterisk y el **ai-engine** (puente de telefonía, que habla con GB por WebSocket en `/v1/agent/converse`) se quedan en `.143`.

### El problema que resolvimos

El smart-turn de producción, `smart-turn-v3.2-cpu.onnx` (8,7 MB), está **cuantizado a 8 bits**. ONNX Runtime ejecuta esas multiplicaciones con instrucciones distintas según la CPU, y sin `avx512_vnni` satura y **cambia decisiones**. **Modal no garantiza CPUs con `avx512_vnni`.**

Lo resolvimos con la versión sin cuantizar, `smart-turn-v3.2-gpu.onnx` (fp32, 32,4 MB, de [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)). Pese al nombre "gpu", corre en CPU con el mismo ONNX Runtime. sha256 `ab8dc64b88713f90b571c15b714bd1330e6c883cad8763dacf65c9376dc539be`.

`[MEDIDO 2026-10-14]` Experimento en `goldenbridge-analisis/experimentos/2026-10-14_smartturn-fp32/` (`README.md`, `GUIA.md`, resultados). Fueron 3.136 entradas y tres umbrales (0,35, 0,6 y 0,85), comparando un Xeon 8488C (con VNNI) y un Ryzen 5 7535HS (sin VNNI):

| | fp32 | int8 |
|---|---|---|
| máxima diferencia de probabilidad entre CPUs | 0,00001 | 0,94 |
| decisiones volteadas entre CPUs | **0** | 279 a 363 (9-12 %) |
| latencia por inferencia en `.143` | 47,6 ms | 27,1 ms |

Además, el fp32 y el int8 se contradicen entre sí en 15-31 % de los casos, en la misma CPU. Son modelos con comportamiento distinto, y el experimento no dice cuál es mejor. En llamadas reales de prueba en `.143` "se escucha bien" (opinión del usuario, con 7 decisiones registradas). El umbral se sigue ajustando con llamadas reales.

### El guardián de arranque

`/usr/local/bin/arranque` es el entrypoint de la imagen. Antes de servir:
1. **Comprueba que estén los archivos.** Si falta alguno, sale con código **2**: el modelo de fin de turno, `test/reference/{features.npz, stack.json, vad_probs.npz, vad_stack.json}` y la copia local de Silero en `TORCH_HOME`.
2. **Corre 12 pruebas con pytest** (`test/test_reference_features.py`: 6 del fin de turno; `test/test_reference_vad.py`: 6 del detector de voz). Exige código 0, 0 fallos, 0 errores, **0 saltadas** y **exactamente 12** (`GB_GATE_CASOS_ESPERADOS`, por defecto 12). Si no, sale con código **3**.
3. Las pruebas comparan las respuestas de los modelos con la **referencia congelada**: que el modelo y el audio no cambiaron (por hash), que las características y probabilidades no se movieron, que las decisiones en los casos claros y dudosos son iguales, y que la referencia cubre la zona de riesgo (≥ 5 casos dudosos, uno a < 0,01 del umbral).
4. **Ya no exige `avx512_vnni`** (`DECIDEN = ()`).
5. Luego: sin argumentos, arranca GB; con argumentos, hace `exec "$@"` (el **contrato de entrypoint de Modal**); con `GB_SOLO_GATE=1`, verifica y sale con 0.
6. `GB_SALTAR_GATE=1` se salta las pruebas (interruptor de depuración que ya existía). La imagen lo trae en `0`. **No debe ponerse en `1` en producción.** `[ABIERTO]` Quitarlo de la imagen; está propuesto y no decidido.

La imagen original no cumplía el contrato de Modal: ignoraba el comando y arrancaba GB. `[INFERIDO]` Por eso en Modal se quitó el entrypoint (`.entrypoint([])`) y el guardián no corre allí. Con la imagen nueva hay que conservarlo.

### Hilos: los tres en 1

| variable | biblioteca | dónde se fija |
|---|---|---|
| `GB_ORT_INTRA_OP=1` | ONNX Runtime (smart-turn) | `ENV` del `Dockerfile` **y** valor por defecto `"1"` en `smart_turn_processor.py` L121 |
| `OMP_NUM_THREADS=1` | OpenMP (torch) | `ENV` del `Dockerfile` |
| `OPENBLAS_NUM_THREADS=1` | OpenBLAS (numpy) | `ENV` del `Dockerfile` |

`[MEDIDO]` Con las tres en 1: 8 hilos en una llamada, ninguno girando en vacío (en agosto, sin límites, había ~15 hilos girando que gastaban 1,57 núcleos). El despliegue (`.env()`) o un Secret de Modal pueden sobrescribirlas. Lo que el proceso recibió de verdad lo imprime el guardián: `[arranque] hilos fijados: ORT_INTRA_OP=1 OPENBLAS=1 OMP=1`. `[ABIERTO]` Qué valores tiene hoy golden-modal.

### Ramas de GoldenBridge (`https://github.com/MrBotGT/GoldenBridge.git`)

| rama | qué es |
|---|---|
| `api` @ `08932a6` | base auditada. Su `Dockerfile` y su guardián son la primera versión (`6e7843c`), con el error `pytest \| tail`: **el guardián nunca bloquea** |
| `feat/modal-migration` @ `9c024fd` | `api` + 5 arreglos de Docker (`24f9c6b`, `7cddc52`, `98df05d`, `fb44e8a`, `9c024fd`: `.dockerignore`, `set -e` mudo, traspaso a Modal en `docker/README.md`). `src/` y `config/` idénticos a `api` |
| `feat/f261-call-summary` @ `fb8a3b9` | lo que corre en producción en `.143` (resumen de llamada, F261) |
| `release/remarcacion-v1` @ `3ec0732` | `api` + ventana tardía del detector de buzón + F261 + 3 commits del 15-oct (frases D11; `default.yaml` = `active.yaml` de producción + claves de remarcación; `stt.api_key` vacío). **No trae los arreglos de Docker.** No toca modelos, extractor ni smart-turn. Referencias y `requirements-314t.lock` idénticos (ONNX Runtime 1.24.4, numpy 2.5.2, soxr 1.1.0, torch 2.13.0+cpu) |

### Commits propios, en la rama local `fix/smartturn-fp32` de `~/gb-fp32` (WSL; **no están en el remoto**)

| commit | qué es |
|---|---|
| `1406660` | parche del guardián: 0 saltadas, conteo exacto, `exec "$@"`, `GB_SOLO_GATE` (también en `goldenbridge-analisis/parches/2026-10-09_goldenbridge_gate-arranque/`) |
| `cbe7e4c` | cambio a fp32 en 5 archivos: ruta del modelo en `config/default.yaml`, `src/processors/smart_turn_processor.py` (L26 y valor por defecto de `GB_ORT_INTRA_OP` en L121), `docker/arranque.sh` y `test/reference/generate_reference.py` (`MODEL`, `THRESHOLD = 0.35`, `CONFIDENT_MS = [300, 800, 2100, 2720, 3700, 4400, 4960]`, `MARGINAL_MS = [820, 1200, 1640, 2840, 3040, 3060, 4120, 4540]`, `intra_op_num_threads = 1`); `DECIDEN = ()` e `intra_op_num_threads = 1` en `test/test_reference_features.py` |
| `f34600e` | referencia regenerada dentro de la imagen (`features.npz`, `stack.json`; 15 casos) |

---

## Entorno

**Laptop (WSL):** Ubuntu 24.04, Python 3.12, **Docker propio de WSL** (no es el Docker Desktop de Windows), usuario `eriosfx`, Ryzen 5 7535HS **sin** `avx512_vnni`.
- `~/goldenbridge-analisis`: clon del repo de análisis.
- `~/gb-fp32`: GB, rama `fix/smartturn-fp32`, con `modelos/` (los dos smart-turn, el caché de Silero y `MANIFIESTO.txt`).
- `~/smart-turn-v3.2-gpu.onnx`: el modelo fp32.
- `~/resultado_143.json`: el barrido de `.143`, que se usa para elegir los casos de la referencia.
- Repo de GB en Windows: `/mnt/c/Users/GESTION/Documents/proyectos/refGolden/GoldenBridge` (trabajo local en otra rama: **no tocar**; sólo se clona o se hace `fetch` desde ahí).
- Imágenes: `goldenbridge:314t` (original, int8) y `goldenbridge:314t-fp32` (id `5fae37eaebba`). Archivo `~/goldenbridge-314t-fp32.tar.gz`, sha256 `7c189b0829e38ae1ea120f6a3fdf51217b8ae7ba4d6a2c16d4a215b19c610180`; copias en `refGolden/files/`.
- SSH: `ssh -i ~/.ssh/ai_key_eraxiom eraxiom@16.58.43.143` (la llave pide frase de paso).

**`.143`:** c7i.4xlarge, `us-east-2a`, Xeon Platinum 8488C **con** `avx512_vnni`, 16 vCPU (la *i* comparte núcleo físico con la *i+8*), Ubuntu 22.04. Apaga a las 06:00 UTC y arranca a las 14:00 UTC.
- Producción: GB en `/srv/GoldenBridge` (F261), puerto **8765**; config en `/srv/GoldenBridge/config/active.yaml` (umbral smart-turn **0,35**, `model_path: smart-turn/smart-turn-v3.2-cpu.onnx`); credenciales en `/srv/GoldenBridge/.env`; campañas en `/srv/GoldenBridge/campaigns/`.
- Engine: contenedor `ai_engine`, compose en `/srv/ai-engine`. Toma la dirección de GB de `GOLDEN_WS_URL`, línea 11 de `/srv/ai-engine/.env`. **Valor de producción: `ws://127.0.0.1:8765/v1/agent/converse`.**

---

## Procedimiento A · construir la imagen desde una rama

Probado con `release/remarcacion-v1` en una copia descartable: todo se aplica limpio salvo **un conflicto en `config/default.yaml`**, porque esa rama reescribió el archivo.

**Todo en WSL.** Cambia `<rama>`, `<carpeta>` y `<etiqueta>`. Para `release/remarcacion-v1`: `~/gb-release`, rama `release/remarcacion-v1-fp32`, imagen `goldenbridge:remarcacion-v1-fp32`.

1. Copia de trabajo nueva, sin tocar `~/gb-fp32`:
   ```bash
   git clone ~/gb-fp32 ~/<carpeta>
   ```
   ```bash
   cd ~/<carpeta>
   ```
   ```bash
   git fetch /mnt/c/Users/GESTION/Documents/proyectos/refGolden/GoldenBridge refs/remotes/origin/<rama>:refs/heads/base-rama refs/remotes/origin/feat/modal-migration:refs/heads/base-modal
   ```
   Si la rama no aparece, antes hay que hacer `git fetch origin` en el repo de Windows.
   ```bash
   git switch -c <rama>-fp32 base-rama && git log -1 --format='%h %s'
   ```
2. Si la rama **no** contiene `9c024fd` (`git merge-base --is-ancestor 9c024fd HEAD && echo tiene || echo falta`), aplicar los arreglos de Docker:
   ```bash
   git cherry-pick 6e7843c..base-modal
   ```
   Requiere que su `Dockerfile` y `docker/arranque.sh` sean los de `6e7843c`: `git diff --quiet 6e7843c HEAD -- Dockerfile docker/arranque.sh && echo igual`.
3. El parche del guardián:
   ```bash
   git cherry-pick 1406660
   ```
4. El cambio a fp32:
   ```bash
   git cherry-pick cbe7e4c
   ```
   Si da conflicto en `config/default.yaml`, quedarse con la versión de la rama y cambiar sólo la ruta:
   ```bash
   git checkout --ours config/default.yaml && sed -i 's|smart-turn-v3.2-cpu.onnx|smart-turn-v3.2-gpu.onnx|' config/default.yaml && git add config/default.yaml
   ```
   ```bash
   GIT_EDITOR=true git cherry-pick --continue
   ```
   Si el conflicto es en otro archivo, **parar y analizar**: significa que la rama tocó smart-turn, el guardián o la referencia.
5. La referencia del fp32:
   ```bash
   git cherry-pick f34600e
   ```
   Sólo es válida si la rama **no** cambió `src/processors/smart_turn_processor.py` (más allá de las 2 líneas de `cbe7e4c`), `src/processors/whisper_features.py`, el procesador de VAD ni el `requirements-314t.lock`. Comprobar con `git diff --stat base-modal HEAD -- src/processors requirements-314t.lock`. Si cambiaron, hay que regenerarla (procedimiento B).
6. Revisión:
   ```bash
   git diff base-rama HEAD -- config/default.yaml | grep '^[-+][^-+]'
   ```
   Sólo debe mostrar la línea de `model_path` (`cpu` → `gpu`).
   ```bash
   grep -n "smart-turn-v3.2-.*onnx\|^DECIDEN\|^THRESHOLD\|^CONFIDENT_MS\|^MARGINAL_MS\|GB_ORT_INTRA_OP\", \|intra_op_num_threads = 1" config/default.yaml src/processors/smart_turn_processor.py docker/arranque.sh test/reference/generate_reference.py test/test_reference_features.py
   ```
   ```bash
   git log --oneline base-rama..HEAD
   ```
7. Modelos y construcción:
   ```bash
   cp -a ~/gb-fp32/modelos ~/<carpeta>/ && ls -l modelos/smart-turn
   ```
   Dos `.onnx`: 8,7 MB y 32 MB.
   ```bash
   docker build -t goldenbridge:<etiqueta> .
   ```
8. Validación:
   ```bash
   docker image inspect goldenbridge:<etiqueta> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E "GB_ORT|OMP|OPENBLAS"
   ```
   Las tres en `1`.
   ```bash
   docker run --rm --network none -e GB_SOLO_GATE=1 goldenbridge:<etiqueta>; echo "salida=$?"
   ```
   Tiene que mostrar `hilos fijados: ORT_INTRA_OP=1 OPENBLAS=1 OMP=1`, `12 passed`, `casos=12/12 fallos=0 errores=0 saltadas=0`, `referencia OK` y `salida=0`. En la laptop esto **demuestra que no depende de VNNI**.
9. Subir la rama (pide credenciales de GitHub):
   ```bash
   git push https://github.com/MrBotGT/GoldenBridge.git <rama>-fp32
   ```

## Procedimiento B · regenerar la referencia (sólo si cambian los modelos o el código que los usa)

En WSL, dentro de la carpeta, después de construir:
```bash
docker run --rm --network none --entrypoint python --user "$(id -u):$(id -g)" -e HOME=/tmp -e GB_ORT_INTRA_OP=1 -v "$PWD/test/reference:/app/test/reference" goldenbridge:<etiqueta> test/reference/generate_reference.py
```
`--user` es necesario porque la imagen corre como `golden` (uid 10001) y no podría escribir en la carpeta. Tiene que decir `referencia escrita: 15 casos`. Los marginales deben quedar cerca de 0,35; `marg_1200ms` ≈ 0,353.
```bash
git add test/reference/features.npz test/reference/stack.json && git commit -m "smart-turn fp32: referencia regenerada"
```
```bash
docker build -t goldenbridge:<etiqueta> .
```
Y repetir la validación del paso 8. Para elegir casos nuevos (por ejemplo, con otro umbral): `goldenbridge-analisis/experimentos/2026-10-14_smartturn-fp32/elige_cortes.py ~/resultado_143.json --umbral <u>`.

## Procedimiento C · llevarla a `.143` y probar con llamadas reales

**WSL:**
```bash
docker save goldenbridge:<etiqueta> | gzip > ~/goldenbridge-<etiqueta>.tar.gz
```
```bash
cd ~ && sha256sum goldenbridge-<etiqueta>.tar.gz > goldenbridge-<etiqueta>.tar.gz.sha256
```
```bash
scp -i ~/.ssh/ai_key_eraxiom ~/goldenbridge-<etiqueta>.tar.gz ~/goldenbridge-<etiqueta>.tar.gz.sha256 eraxiom@16.58.43.143:/tmp/
```

**.143:**
```bash
cd /tmp && sha256sum -c goldenbridge-<etiqueta>.tar.gz.sha256 && docker load < goldenbridge-<etiqueta>.tar.gz
```
```bash
docker run --rm --network none -e GB_SOLO_GATE=1 goldenbridge:<etiqueta>; echo "salida=$?"
```
Configuración del contenedor de prueba. Se fuerza la ruta del modelo, porque el `active.yaml` de producción dice `cpu.onnx`:
```bash
mkdir -p ~/fp32-lab && docker run --rm --network none --entrypoint python -v /srv/GoldenBridge/config/active.yaml:/in.yaml:ro goldenbridge:<etiqueta> -c 'import sys,yaml; c=yaml.safe_load(open("/in.yaml")) or {}; c.setdefault("smart_turn",{})["model_path"]="/app/smart-turn/smart-turn-v3.2-gpu.onnx"; yaml.safe_dump(c,sys.stdout,sort_keys=False,allow_unicode=True)' > ~/fp32-lab/config.yaml
```
```bash
chmod 644 ~/fp32-lab/config.yaml; grep -n "model_path" ~/fp32-lab/config.yaml; grep -n "/srv" ~/fp32-lab/config.yaml || echo "sin otras rutas de /srv"
```
Las tres líneas siguientes van **en la misma terminal**:
```bash
ENV_ARGS=(); for n in GOTENKS_API_KEY URSULA_API_KEY CEREBRAS_API_KEY; do l=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$n=" /srv/GoldenBridge/.env | tail -1); [ -n "$l" ] || { echo "falta $n"; continue; }; v=${l#*=}; v=${v%$'\r'}; v=${v#\"}; v=${v%\"}; v=${v#\'}; v=${v%\'}; export "$n=$v"; ENV_ARGS+=(-e "$n"); done; unset l v; echo "credenciales: $(( ${#ENV_ARGS[@]} / 2 ))"
```
```bash
MONTAJES=(); for d in campaigns models background_audio.wav; do [ -e "/srv/GoldenBridge/$d" ] && MONTAJES+=(-v "/srv/GoldenBridge/$d:/app/$d:ro"); done; echo "montajes: ${MONTAJES[*]}"
```
```bash
docker rm -f gb-fp32 2>/dev/null; docker run -d --name gb-fp32 -p 127.0.0.1:8776:8776 -e GB_PUERTO=8776 -e GB_CONFIG=/lab/config.yaml -e GOLDEN_BRIDGE_DEBUG=1 -v "$HOME/fp32-lab/config.yaml:/lab/config.yaml:ro" "${MONTAJES[@]}" "${ENV_ARGS[@]}" goldenbridge:<etiqueta>
```
Después de ~60 s (el guardián corre primero; antes no responde):
```bash
docker logs gb-fp32 2>&1 | grep -E "hilos fijados|referencia OK|Smart Turn processor created|not available|Traceback"; curl -s http://127.0.0.1:8776/health; echo
```
Esperado: `referencia OK`, `model_path=/app/smart-turn/smart-turn-v3.2-gpu.onnx threshold=0.35` y `"status":"healthy"`.

Apuntar el engine al contenedor, **fuera de horario de campaña y con `"active_sessions":0` en `curl -s http://127.0.0.1:8765/health`**, porque reiniciar el engine corta las llamadas:
```bash
cd /srv/ai-engine && cp .env .env.antes-fp32 && sed -i 's|^GOLDEN_WS_URL=.*|GOLDEN_WS_URL=ws://127.0.0.1:8776/v1/agent/converse|' .env && docker compose up -d ai_engine && docker exec ai_engine printenv GOLDEN_WS_URL
```
Durante las llamadas:
```bash
docker logs -f --since 1m gb-fp32 2>&1 | grep "Smart Turn prediction"
```
```bash
docker exec gb-fp32 sh -c 'echo hilos=$(ls /proc/1/task | wc -l); cat /proc/1/task/*/comm | sort | uniq -c | sort -rn'
```
**Volver a producción, siempre:**
```bash
cd /srv/ai-engine && cp .env.antes-fp32 .env && docker compose up -d ai_engine && docker exec ai_engine printenv GOLDEN_WS_URL
```
Tiene que decir `ws://127.0.0.1:8765/v1/agent/converse`.
```bash
docker logs --timestamps gb-fp32 > ~/fp32-lab/gb-fp32.log 2>&1; grep -c "Smart Turn prediction" ~/fp32-lab/gb-fp32.log; docker rm -f gb-fp32
```
El log tiene datos de llamadas reales: **no va a ningún repo**.

## Procedimiento D · lo que se le entrega al equipo de Modal (sólo configuración; los comandos de Modal los conocen)

1. **Verificar y cargar:** `sha256sum -c …` (debe decir `OK`), `docker load < …`, y `docker run --rm --network none -e GB_SOLO_GATE=1 <imagen>; echo "salida=$?"` (debe decir `referencia OK` y `salida=0`).
2. **`active.yaml`:** `smart_turn.model_path: smart-turn/smart-turn-v3.2-gpu.onnx` (antes `…-cpu.onnx`). **Obligatorio:** la imagen trae los dos modelos, y con la ruta vieja GB carga el int8 y el guardián no lo detecta.
3. **Variables:** `.env({"GB_CONFIG": "/app/config/active.yaml", "GB_ORT_INTRA_OP": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})` encadenado a `modal.Image.from_registry("<registro>/<imagen>")`. `GB_CONFIG` debe apuntar a donde montan `active.yaml`.
4. **Conservar el entrypoint** (quitar `.entrypoint([])` si existe). No poner `GB_SALTAR_GATE=1`. Montar las campañas en `/app/campaigns`. Credenciales como Secret.
5. **Etiqueta nueva**, conservando la anterior para volver atrás. Primero una app de prueba.
6. **Lo que deben devolver:** del log de un contenedor, las líneas `hilos fijados`, `gate: … saltadas=0`, `referencia OK` y `Smart Turn processor created … model_path=…gpu.onnx`; el modelo de CPU; y el tiempo del primer arranque en frío.

Al final se agrega una explicación breve de las 12 pruebas, y que **ya no exigen `avx512_vnni`**. Descripción de una línea de la imagen: "Imagen de GoldenBridge con el modelo de fin de turno sin cuantizar".

---

## Avisos que ya costaron tiempo

- Si el prompt de WSL dice `~/goldenbridge-analisis`, estás en el repo equivocado: los comandos de GB van dentro de la carpeta de GB.
- `> ` en la terminal significa comillas sin cerrar: `Ctrl+C` y repetir en una línea.
- `docker image inspect` o `docker exec` "No such image/container" antes de construir o levantar: es el orden, no un error.
- El contenedor tarda ~60 s en responder: primero corre el guardián.
- `git log --format=%s` muestra sólo la primera línea del mensaje: no significa que se perdió el resto.
- El `default.yaml` de `release/remarcacion-v1` es la configuración de producción: `cycle_probe: true` (mucho log), `vad.inference_workers: 12`, `stt.base_url: ws://localhost:8080` (`[ABIERTO]` si se usa con el backend `gotenks`; en `.143` las llamadas funcionaron con esa configuración).
- `release/remarcacion-v1` trae F261: GB envía `CALL_SUMMARY` por el WebSocket. El engine tiene que soportarlo.

## Pendientes `[ABIERTO]`

- Valores reales de hilos, entrypoint y Secret en golden-modal (no tengo el archivo de despliegue).
- Umbrales de fin de turno **por campaña**: una decisión con p = 0,461 dio "no terminó" con umbral global 0,35. Revisar con `grep -rn -i "threshold" /srv/GoldenBridge/campaigns/`.
- Ajustar el umbral del fp32 con más llamadas reales.
- Decidir si se quita `GB_SALTAR_GATE` de la imagen.
- Las ramas `fix/smartturn-fp32` y `release/remarcacion-v1-fp32` existen sólo en WSL hasta que se suban.

## Primer paso

Pregúntame qué rama quiero convertir en imagen y en qué punto estoy. Para `release/remarcacion-v1`, empieza en el procedimiento A, paso 1, con `~/gb-release`.
