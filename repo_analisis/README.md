# Auditoría de repositorios de motores Media

Auditoría comparativa de motores de llamadas, runtimes de media y frameworks de agentes de voz, para decidir cómo separar el motor Media del proceso Conversación de GoldenBridge. Encargo: `../prompts/PROMPT_auditoria_motores_media_repos_20260928.md`. Ejecutada el 2026-10-09.

## Documentos finales

| documento | contenido |
|---|---|
| [MATRIZ_COMPARATIVA.md](MATRIZ_COMPARATIVA.md) | qué hace cada repo con el coordinador por trama; matriz de 18 dimensiones; siete correcciones al contrato |
| [PROPUESTA_APLICABLE_GOLDEN.md](PROPUESTA_APLICABLE_GOLDEN.md) | MediaWorker con dueño por hilo, frontera, barge-in, caídas, capacidad, PoC falsable |
| [DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md](DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md) | nada que adoptar como componente; once patrones a adaptar; cuatro piezas a construir |
| [BRIEF_GOLDEN.md](BRIEF_GOLDEN.md) | estado de GoldenBridge usado como término de comparación |
| [IDENTIDAD.tsv](IDENTIDAD.tsv) | commit, fecha y licencia de cada clon |

## Repositorios e informes

Clones superficiales del branch por defecto en `repos/`, sin modificar (`git status` limpio verificado al cierre).

| # | repositorio | branch @ commit | fecha | licencia observada | informe | estado |
|---|---|---|---|---|---|---|
| 01 | [mediasoup](https://github.com/versatica/mediasoup) | v3 @ `7d21d63d15ce` | 2026-10-09 | ISC | [informes/01_mediasoup.md](informes/01_mediasoup.md) | completo |
| 02 | [freeswitch](https://github.com/signalwire/freeswitch) | master @ `f4778e7f2c98` | 2026-10-09 | MPL-1.1 | [informes/02_freeswitch.md](informes/02_freeswitch.md) | completo |
| 03 | [jambonz-feature-server](https://github.com/jambonz/jambonz-feature-server) | main @ `85da181d7ab8` | 2026-10-04 | MIT | [informes/03_jambonz-feature-server.md](informes/03_jambonz-feature-server.md) | completo |
| 04 | [livekit-server](https://github.com/livekit/livekit) | master @ `5a897c239cde` | 2026-10-09 | Apache-2.0 (+ MIT ion-sfu) | [informes/04_livekit-server.md](informes/04_livekit-server.md) | completo |
| 05 | [livekit-sip](https://github.com/livekit/sip) | main @ `765790aeccfe` | 2026-10-09 | Apache-2.0 | [informes/05_livekit-sip.md](informes/05_livekit-sip.md) | completo |
| 06 | [livekit-agents](https://github.com/livekit/agents) | main @ `786a76622c8b` | 2026-10-09 | Apache-2.0; modelos LiveKit no portables | [informes/06_livekit-agents.md](informes/06_livekit-agents.md) | completo |
| 07 | [pipecat](https://github.com/pipecat-ai/pipecat) | main @ `7d3bd9f4575b` | 2026-10-09 | BSD-2-Clause | [informes/07_pipecat.md](informes/07_pipecat.md) | completo |
| 08 | [ten-framework](https://github.com/TEN-framework/ten-framework) | main @ `ad20a1735293` | 2026-10-03 | Apache-2.0 + condiciones Agora en el núcleo | [informes/08_ten-framework.md](informes/08_ten-framework.md) | completo; submódulo `core/ten_gn` sin inicializar; `agora_rtc` y `ten_ai_base` no están en el repo |

Dependencias descargadas por el auditor de LiveKit SIP porque su camino de media vive fuera del repo (pins exactos de `go.mod`, Apache-2.0, sin modificar):

| repositorio | commit | fecha |
|---|---|---|
| `repos/livekit-media-sdk` | `bc3eace31b2e` | 2026-09-25 |
| `repos/livekit-psrpc` | `da876f1472bd` (v0.8.0) | 2026-09-20 |
| `repos/livekit-protocol` | `8c8c356c106d` (sparse `rpc/`, `protobufs/rpc/`) | 2026-09-25 |

## Límites

- Ningún repositorio contiene un benchmark reproducible de capacidad: toda cifra de llamadas por worker es `[PENDIENTE]`.
- No se compiló ningún proyecto ni se ejecutaron sus tests.
- Partes del camino caliente de LiveKit server viven en pion y mediatransportutil, fuera del clon.
- GoldenBridge y ai-engine no se modificaron.
