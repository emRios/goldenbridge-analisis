# goldenbridge-analisis

Análisis de capacidad y escalado de **GoldenBridge**, el motor de voz con IA para campañas salientes de banca, y laboratorio del **MediaWorker**. El contexto completo para cualquier persona o asistente está en [`CLAUDE.md`](CLAUDE.md); la estrategia vigente, en [`doctos/ESTRATEGIA_MEDIAWORKER_20261009.md`](doctos/ESTRATEGIA_MEDIAWORKER_20261009.md).

## Estructura

| carpeta | contenido |
|---|---|
| `context/` | arquitectura autoritativa |
| `doctos/` | auditorías, dictámenes, contrastes y la estrategia |
| `prompts/` | encargos para agentes (Codex, Claude) |
| `repo_analisis/` | auditoría de 8 motores de media; los clones (`repos/`) no se versionan |
| `mediaworker/` | código del laboratorio por componentes (C0-C4 de la estrategia) |
| `experimentos/` | una carpeta por experimento con su `run.sh`; plantilla en `_plantilla/` |
| `parches/` | cambios propuestos para GoldenBridge o ai-engine |
| `evidencia/` | corridas sintéticas anteriores |

No se versionan: `GoldenBridge/` y `Asterisk-AI-Voice-Agent-FX/` (repos propios), `files/` (dumps de producción) ni `repo_analisis/repos/`.

## Flujo de trabajo

```
laptop (Windows)                         servidor (Linux)
  documentos, código, scripts              git pull
  pruebas sin modelos ni tráfico           experimentos/<x>/run.sh
  git push  ─────────── GitHub ─────────▶  resultados/<fecha>/  (resumen + manifiesto)
            ◀───────────────────────────   git push de resúmenes (lo crudo no)
```

1. Cada paso de la estrategia vive en una rama corta: `paso/m0-gate`, `paso/m1-linea-base`, … Se integra a `main` al cerrar el paso.
2. En el servidor, primera vez:
   ```bash
   git clone https://github.com/emRios/goldenbridge-analisis.git
   cd goldenbridge-analisis
   git config core.autocrlf false
   ```
   Después, para cada paso: `git fetch && git switch paso/<nombre> && git pull`.
3. Un experimento se ejecuta **sólo** por su `run.sh`. El script graba un manifiesto antes de medir (commits, imagen, CPU, config) y se niega a correr si detecta llamadas activas en GoldenBridge, salvo `FORZAR=1`.
4. Los resultados que vuelven al repo son resúmenes pequeños (TSV, MD). Logs y dumps quedan en `resultados/crudo/`, que git ignora.
5. Ningún número entra a un documento como `[MEDIDO]` sin la ruta de su manifiesto.

## Acceso desde el servidor

El servidor necesita acceso al repo. Lo más seguro es una **deploy key** de GitHub para este repositorio: sólo lectura si los resultados se suben desde la laptop; con escritura si el servidor hace push de resúmenes.
