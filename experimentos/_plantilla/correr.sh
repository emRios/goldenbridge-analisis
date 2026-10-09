#!/usr/bin/env bash
# La medición del experimento. La invoca run.sh con el directorio de salida
# como único argumento, DESPUÉS de grabar el manifiesto.
#
# Contrato:
#   - escribir resúmenes pequeños en "$1" (TSV, MD): esos se versionan;
#   - lo voluminoso, en "$(dirname "$1")/crudo/": no se versiona;
#   - salir con código != 0 si la medición no es válida.
set -euo pipefail

SALIDA="$1"
CRUDO="$(dirname "$SALIDA")/crudo"

echo "plantilla: reemplazar este correr.sh por la medición real"
printf 'metrica\tvalor\n' > "$SALIDA/resumen.tsv"
exit 1
