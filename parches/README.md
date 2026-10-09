# parches — cambios propuestos para otros repos

GoldenBridge y ai-engine tienen sus propios repositorios. Los cambios que salen de este análisis se preparan aquí como parches y se aplican allá; nunca se edita su código desde este repo.

## Convención

```
parches/
└── <fecha>_<repo>_<tema>/
    ├── README.md        qué corrige, evidencia, rama y commit base, cómo probarlo, cómo revertirlo
    └── 0001-*.patch     generado con `git format-patch` sobre el commit base
```

- `<repo>`: `goldenbridge` o `ai-engine`.
- El README dice **contra qué commit** se generó (para GoldenBridge, normalmente `api@08932a6`).
- Cada parche trae su prueba: qué ejecutar antes y después para demostrar el cambio, incluida la prueba negativa cuando aplique.

## Aplicar en el servidor

```bash
cd /ruta/a/GoldenBridge
git switch -c <rama-del-parche> <commit-base>
git am /ruta/a/goldenbridge-analisis/parches/<carpeta>/0001-*.patch
```

`git am` conserva autor y mensaje. Si el commit base avanzó y no aplica limpio, no se fuerza: se regenera el parche.
