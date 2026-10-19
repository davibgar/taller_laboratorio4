#!/usr/bin/env bash
# =====================================================================
# Taller de Laboratorio N.º 4 - Ejecuta la medición completa y guarda la evidencia
# Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya
#
#  1. Muestra la declaración de criterios (fijada antes de medir).
#  2. Levanta el entorno desde cero con un único comando y verifica los servicios.
#  3. Registra la ficha del equipo y los límites de los contenedores.
#  4. Deja el entorno en reposo 2 minutos (línea base, con el APM corriendo).
#  5. Ejecuta la prueba de estrés (corrida1) y la analiza.
#  6. Toma las capturas del APM (Grafana y Jaeger).
#  7. Repite el escalón del punto de saturación y el siguiente (corrida "repeticion").
#  8. Convierte las salidas de terminal en imágenes.
#
# Sólo usa Docker. Ejecutar desde la raíz del proyecto (Git Bash en Windows):
#     bash evidencias/generar_evidencias.sh
# ADVERTENCIA: reinicia el entorno (docker compose down -v) para partir de cero.
# Duración aproximada: 50 minutos. No use el equipo mientras corre.
# =====================================================================
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
OUT=evidencias/capturas
CORRIDA=${CORRIDA:-corrida1}
ESCALONES=${ESCALONES:-10,25,50,100,200,400,600,800}

paso() { echo; echo "===== $(date +%H:%M:%S) $* ====="; }

# Guarda la hora, el comando mostrado y la salida real en un .txt (luego se convierte en imagen)
cap() {
  local nombre=$1 mostrado=$2; shift 2
  { date "+%Y-%m-%d %H:%M:%S"; echo "$mostrado"; "$@" 2>&1; } > "$OUT/$nombre.txt"
  tail -n +3 "$OUT/$nombre.txt" | tail -n 40
}

esperar_api() {
  for _ in $(seq 1 90); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' lab04-api 2>/dev/null)" = "healthy" ] && return 0
    sleep 2
  done
  echo "La API no quedó saludable"; exit 1
}

rm -rf "$OUT" resultados/"$CORRIDA" resultados/repeticion
mkdir -p "$OUT" resultados

paso "1. Declaración de los criterios de saturación (antes de medir)"
cap 00_criterios_declarados "Get-Content CRITERIOS_SATURACION.md; (Get-Item CRITERIOS_SATURACION.md).LastWriteTime" \
  sh -c 'sed -n "1,40p" CRITERIOS_SATURACION.md; echo; echo "Última modificación del archivo:"; date -r CRITERIOS_SATURACION.md "+%Y-%m-%d %H:%M:%S"'

paso "2. Entorno desde cero con un único comando"
docker compose --profile prueba down -v --remove-orphans >/dev/null 2>&1
cap 01_docker_compose_up "docker compose up -d --build" docker compose up -d --build
esperar_api
sleep 10
cap 02_servicios_arriba "docker compose ps" \
  docker compose ps --format "table {{.Name}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
cap 03_apm_recibe_datos "docker compose exec -T api python - < evidencias/verificar_apm.py" \
  sh -c "docker compose exec -T api python - < evidencias/verificar_apm.py"

paso "3. Ficha del equipo y límites de los contenedores"
docker pull -q alpine:3.20 >/dev/null
cap 04_ficha_equipo "docker info; docker inspect (límites de recursos)" sh -c '
  docker info --format "Sistema operativo (Docker): {{.OperatingSystem}}
Kernel: {{.KernelVersion}}
Docker Engine: {{.ServerVersion}}
CPU disponibles para Docker: {{.NCPU}}
Memoria disponible para Docker: {{.MemTotal}} bytes"
  echo "Procesador: $(docker run --rm alpine:3.20 sh -c "grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2")"
  echo
  printf "%-28s %10s %12s\n" CONTENEDOR "CPU (núcleos)" "MEMORIA (MB)"
  for c in $(docker compose ps -q); do
    docker inspect -f "{{.Name}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}" "$c"
  done | awk "{ printf \"%-28s %10.2f %12.0f\n\", substr(\$1,2), \$2/1e9, \$3/1048576 }" | sort
  echo "k6 (generador de carga)            2.00         1024"'

paso "4. Línea base: 2 minutos en reposo (con el APM corriendo)"
sleep 120
cap 05_linea_base_docker_stats "docker stats --no-stream" \
  docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"

paso "5. Prueba de estrés: corrida $CORRIDA (escalones $ESCALONES)"
cap 10_prueba_estres_k6 "docker compose run --rm -e CORRIDA=$CORRIDA k6" \
  sh -c "docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=$ESCALONES k6 2>&1 | grep -v 'running (' | grep -v 'escalones   \[' "
sleep 10
cap 11_analisis "docker compose run --rm --build analisis --corrida $CORRIDA" \
  docker compose run --rm --build analisis --corrida "$CORRIDA"

paso "6. Capturas del APM"
docker compose run --rm --build captura apm --corrida "$CORRIDA"

paso "7. Repetición del escalón de saturación"
REPETIR=$(docker compose run --rm -T --entrypoint python analisis -c "
import json
r = json.load(open('/resultados/$CORRIDA/resumen.json'))
e = [x['usuarios'] for x in r['escalones']]
p = r['punto_saturacion']
i = e.index(p['usuarios']) if p else 0
print(','.join(str(u) for u in e[i:i + 2]))
" | tr -d '\r')
echo "Se repiten los escalones: $REPETIR"
cap 12_repeticion_k6 "docker compose run --rm -e CORRIDA=repeticion -e ESCALONES=$REPETIR k6" \
  sh -c "docker compose run --rm -e CORRIDA=repeticion -e ESCALONES=$REPETIR k6 2>&1 | grep -v 'running (' | grep -v 'escalones   \[' "
sleep 10
cap 13_repeticion_analisis "docker compose run --rm analisis --corrida repeticion" \
  docker compose run --rm analisis --corrida repeticion

paso "8. Imágenes de las salidas de terminal"
docker compose run --rm captura terminales /ev/capturas
paso "Listo: evidencias en $OUT y resultados en resultados/"
