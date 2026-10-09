# Implementación del coalescer 20/60 ms en ai-engine

Implementa el coalescer de audio en el repositorio real de ai-engine. Antes de
actuar, lee completa la estrategia normativa:

`prompts/ESTRATEGIA_post_p0_refactor_20260914.md`

Respétala; no la reescribas, no la contradigas y no amplíes el alcance. Este
prompt sólo concreta las reglas de ciclo de vida ya incorporadas en F1 de esa
estrategia. Si encuentras un punto incompatible con el código actual, detente,
muestra la evidencia y propón el cambio mínimo en vez de inventar una ruta nueva.

## Contexto de esta tarea

- Sólo se implementa F1: el coalescer en **ai-engine**.
- GoldenBridge completo sigue en Modal. No modificar GoldenBridge, Modal,
  Asterisk, túneles, proveedores ni la arquitectura.
- Asterisk continúa entregando audio cada 20 ms. El cambio agrupa tres frames
  µ-law consecutivos de 160 B y envía un único mensaje binario de 480 B cada
  60 ms hacia GoldenBridge.
- La salida TTS hacia Asterisk permanece en frames de 20 ms; no tocar su pacer.
- El P0 directo a GoldenBridge Modal ya confirmó compatibilidad de mensajes de
  480 B. Esta implementación no certifica K; la validación extremo a extremo
  posterior es F2.

## Restricciones obligatorias

1. La configuración inicial permite **sólo `20` y `60` ms**. Default `20`.
   El valor se fija al iniciar ai-engine: cambiarlo requiere reinicio. `/reload`
   no puede modificarlo para una llamada en curso ni mezclar ambos modos. No
   introducir 40/80/100 ms ni otras optimizaciones en este cambio.
2. Con `20 ms`, el camino debe ser un bypass: mismo envío inmediato y sin crear
   ni tocar estado del coalescer.
3. Con `60 ms`, enviar únicamente al llegar el tercer frame: bytes exactamente
   `f1 + f2 + f3`, sin relleno, cabeceras, reordenamiento ni envío anticipado.
4. El buffer es estrictamente por llamada usando la identidad canónica que ya
   emplea ai-engine para su ciclo de vida. Nunca usar estado global de audio,
   buffers compartidos, reutilización entre sesiones ni paralelismo que pueda
   contaminar llamadas.
5. Implementar un único helper idempotente de flush + limpieza, serializado con
   un lock por llamada que sólo existe en la rama µ-law/60 ms. Debe preservar el
   orden entre handlers concurrentes de una misma llamada.
6. En cleanup terminal, ejecutar el flush best-effort **antes** de retirar o
   detener el provider; usar timeout corto para no bloquear la terminación y
   limpiar el estado en `finally`. La desconexión AudioSocket es sólo respaldo:
   drena únicamente al desaparecer la última conexión de esa llamada. No dejar
   estado huérfano ni enviar a un provider inactivo.
   Para un stall, usar reloj monotónico: si el parcial supera 120 ms al llegar
   un frame nuevo, enviar ese parcial antes de iniciar el paquete siguiente.
7. No alterar el contrato validado de GoldenBridge Modal: Python 3.14.7
   free-threaded, ONNX/ORT, SmartTurn, gates y límites de threading quedan fuera
   de este cambio.
8. No incluir secretos, tokens, endpoints privados ni credenciales en código,
   pruebas, logs o respuesta.

## Método de trabajo

1. Inspecciona el código real y confirma, con archivos, símbolos y callers:
   - entrada AudioSocket;
   - conversión a µ-law;
   - envío al provider/GoldenBridge;
   - ciclo de vida y desconexión de la llamada;
   - configuración existente y patrón de tests.
2. Antes de editar, entrega un plan breve de archivos y cambios. Si los nombres
   o rutas de la estrategia no coinciden con el checkout, usa los reales y
   explica la diferencia.
3. Implementa el cambio mínimo, con configuración explícita y rollback a 20 ms.
4. Añade métricas/logs mínimos definidos por la estrategia, sin loguear audio ni
   datos de clientes.
5. Implementa y ejecuta los tests de caracterización exigidos por la estrategia:
   paridad de bytes, orden, aislamiento entre llamadas, no adelantar audio,
   bypass 20 ms, drenaje/cleanup terminal, timeout/error de flush, desconexión
   no terminal, dos conexiones concurrentes de una llamada, provider inactivo,
   firma real `send_audio(payload)` y rama no aplicable intacta.
6. Ejecuta la suite de pruebas afectada y cualquier chequeo estático disponible.
7. Entrega: resumen de archivos modificados, resultados exactos de pruebas,
   configuración para activar 60 ms, rollback a 20 ms y riesgos que quedan para
   F2. No declares éxito de K.

## Criterio de calidad

Prioriza corrección de audio y reversibilidad sobre reducir líneas. No asumas
que un nombre, un comentario o una configuración es verdad: verifica el caller
y el ciclo de vida antes de modificarlo. No hagas un refactor mayor ni cambies
la topología; si se requiere uno, repórtalo como bloqueo fuera de alcance.
