# Ejecución controlada de calidad del coalescer 20/60 ms en el servidor

Actúa como ingeniero de despliegue y pruebas en el servidor de ai-engine.
Ejecuta la certificación de calidad del coalescer sin modificar GoldenBridge
en Modal, Asterisk, `.env`, URLs, credenciales ni el despliegue de Modal.

## Objetivo y límite

La rama a desplegar es:

```text
feat/goldenbridge-modal-coalescer-60ms @ c4d65bc
```

La prueba compara el camino real:

```text
Asterisk -> AudioSocket -> ai-engine -> GoldenBridge Modal -> TTS -> llamada
```

Se debe demostrar que 60 ms no empeora la llamada frente a 20 ms. No basta con
que la llamada conecte ni con que el contador del coalescer crezca.

**Restricción absoluta:** no editar, montar, redeplegar, reiniciar ni cambiar
configuración de GoldenBridge/Modal. No intentar agregar
`audio_integrity_probe` o modificar `cycle_probe` allí. Si Golden ya emite
`CYCLE-PROBE` y sus logs están disponibles, sólo leerlos; no son requisito para
esta ejecución.

## Reglas de seguridad

- No iniciar llamadas mientras haya una campaña activa. Usar sólo el dialplan,
  destinos y audios de prueba aprobados por el operador.
- Antes de originar llamadas, pedir confirmación explícita si el permiso no fue
  dado para esa ventana.
- No imprimir ni copiar valores de `MODAL_KEY`, `MODAL_SECRET`, URLs firmadas,
  tokens ni contenido de `.env`.
- No usar `git reset --hard`, `git clean`, `checkout -f` ni borrar los
  untracked existentes (`scripts/monitor_streaming_perf.py`, `{c[cnt]}`).
- No usar `/reload`: `frame_ms` se fija al arrancar ai-engine.
- Todo cambio a `config/ai-agent.yaml` es temporal, se documenta y se restaura
  al final.

## 1. Preflight sin cambios

En `/srv/ai-engine`:

```bash
git status --short
git fetch origin
git switch feat/goldenbridge-modal-coalescer-60ms
git pull --ff-only
git rev-parse --short HEAD
docker compose config --services
docker compose ps
grep -n -A8 '^audiosocket:' config/ai-agent.yaml
```

El SHA debe ser `c4d65bc` o un descendiente conocido y revisado. Si hay cambios
tracked que impiden el cambio de rama, detenerse y reportarlos; no descartarlos.

Confirmar que `ai_engine` es el servicio que tiene los bind-mounts de `src/` y
`config/`. No operar `ai_engine_bench` como sustituto de producción.

Copiar la configuración sólo como respaldo recuperable y registrar su hash:

```bash
cp config/ai-agent.yaml /tmp/ai-agent.yaml.before-coalescer-quality
sha256sum config/ai-agent.yaml
```

## 2. Único cambio permitido: configuración temporal de ai-engine

En el bloque existente `audiosocket:` conservar formato, host, puerto y todos
los demás valores. Añadir o ajustar solamente:

```yaml
audiosocket:
  frame_ms: 20
  integrity_probe: true
```

`integrity_probe` es local a ai-engine: deja un resumen BLAKE2s de bytes antes
y después del coalescer. No requiere ni modifica Golden-Modal. Con 20 ms debe
ser bypass; con 60 ms debe mostrar que los mismos bytes salen en menos mensajes.

Reiniciar solamente ai-engine y comprobar que ARI y Modal vuelven a conectar:

```bash
docker compose restart ai_engine
docker compose logs --tail=150 ai_engine
curl -fsS http://127.0.0.1:15000/metrics | grep -E \
  'avb_coalesce_msgs_out_total|avb_coalesce_partial_flush_total'
```

Si aparece error de ARI, error de configuración o el destino deja de ser Modal,
restaurar el respaldo, reiniciar `ai_engine` y detener la prueba.

## 3. Tres corridas comparables: 20 -> 60 -> 20

Mantener idénticos campaña de prueba, diez audios, Asterisk, rampa, destino,
contenedor Modal caliente y condiciones de red. Hacer una llamada de descarte
para calentar Modal; excluirla de evidencia.

Para cada corrida crear un intervalo de logs local, por ejemplo:

```bash
mkdir -p evidencia/coalescer-quality/run20a
date -u +%Y-%m-%dT%H:%M:%SZ
# ejecutar las 10 llamadas mediante el mecanismo de prueba aprobado
docker compose logs --since '<INICIO_UTC>' ai_engine \
  > evidencia/coalescer-quality/run20a/ai_engine.log
```

No inventar un mecanismo de originación ni llamar a contactos de campaña. Si no
existe un generador aprobado de diez llamadas/Audiosocket, detenerse y pedir la
orden de ejecución concreta.

### Corrida A: 20 ms

- Ejecutar diez llamadas completas con los mismos audios/guiones.
- Registrar `SettingsApplied`, transcripción, primer audio del agente,
  `audio_done`, errores, hangup y resolución de turno.
- Debe haber `COALESCE_INTEGRITY_ENGINE` por llamada: hashes iguales,
  `pending_bytes=0` y mensajes emitidos aproximadamente iguales a mensajes de
  origen.

### Corrida B: 60 ms

- Cambiar sólo `audiosocket.frame_ms: 60` en el mismo YAML temporal.
- Reiniciar `ai_engine`; no usar `/reload`.
- Hacer otra llamada de descarte y luego las mismas diez llamadas.
- Verificar en logs `AudioSocket provider coalescer first send` con
  `frame_bytes=480` y `frames_per_msg=3`.
- Verificar `avb_coalesce_msgs_out_total` creciente, cerca de 16--17 mensajes
  por segundo de audio entrante.
- Cada ledger `COALESCE_INTEGRITY_ENGINE` debe dar mismo hash y bytes de origen
  y salida, `pending_bytes=0`, y mensajes emitidos cercanos a un tercio.

### Corrida C: retorno a 20 ms

- Restaurar sólo `frame_ms: 20`, conservar `integrity_probe: true`, reiniciar
  `ai_engine` y repetir las diez llamadas.
- Si A y C difieren materialmente, tratar B como no concluyente: puede haber
  deriva por calentamiento, red o servicios remotos.

## 4. Calidad conversacional: gate obligatorio dentro de las mismas corridas

Para cada cadencia incluir tres repeticiones de cada guion mientras TTS está
activo y ya lleva al menos dos segundos:

1. interrupción sostenida de 1,5 s;
2. interrupción breve de 400 ms;
3. interrupción débil, aproximadamente -20 dB respecto al caso sostenido.

Para cada caso guardar llamada, tipo, hora del primer byte de voz del cliente,
hora de cancelación TTS, si el TTS cesó, transcript y resultado. Usar grabación
de la llamada si está disponible; si no, usar los timestamps de entrada
AudioSocket y de cancelación de TTS como aproximación y marcar la limitación.

No aprobar 60 ms si sucede cualquiera de estos casos:

- falla una interrupción sostenida que funcionó en 20 ms;
- la tasa de cortes de breve/débil cae >=20 % de forma reproducible;
- latencia p50 de cancelación aumenta más de 60 ms o p90 más de 150 ms;
- se pierde/duplica audio, transcript o TTS;
- aumentan materialmente cierres `via=watchdog`, errores o desconexiones;
- la llamada tiene peor detección de inicio/final de voz, cierre de turno o
  primer audio de respuesta fuera de la variación de A y C.

No cambiar `midspeech_check_every_n_chunks` en Golden: no hay autorización ni
acceso. Esta prueba evalúa el comportamiento real vigente de Golden-Modal. Si
la detección a 60 ms regresa, el resultado correcto es mantener 20 ms y
reportar que una corrección del lado Golden sería necesaria antes de reintentar.

## 5. Evidencia y reporte

Entregar una tabla por A/B/C con:

| Métrica | 20-A | 60-B | 20-C |
|---|---:|---:|---:|
| llamadas iniciadas / completas | | | |
| `SettingsApplied` | | | |
| primer audio del agente p50/p90 | | | |
| cierre de turno p50/p90 | | | |
| `via=watchdog` | | | |
| errores / desconexiones | | | |
| bytes origen / emitidos (ledger) | | | |
| mensajes origen / emitidos (ledger) | | | |
| cortes sostenido / breve / débil | | | |
| cancelación TTS p50/p90 | | | |
| transcript esperado encontrado | | | |

Incluir sólo líneas de log necesarias, sin credenciales ni audio. Declarar con
claridad que el ledger certifica ai-engine hasta su envío WebSocket, no el
interior de Golden-Modal.

## 6. Rollback obligatorio

Incluso si la prueba pasa, restaurar el YAML original y reiniciar:

```bash
cp /tmp/ai-agent.yaml.before-coalescer-quality config/ai-agent.yaml
docker compose restart ai_engine
sha256sum config/ai-agent.yaml
```

Confirmar ARI conectado y una llamada de verificación si fue autorizada. No
dejar `integrity_probe: true` ni una configuración temporal activa.
