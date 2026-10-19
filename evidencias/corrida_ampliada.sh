#!/usr/bin/env bash
# =====================================================================
# Taller de Laboratorio N.º 4 - Corrida ampliada (corrida2)
# Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya
#
# En la corrida1 (10 a 400 usuarios) el último escalón todavía cumplía los criterios.
# Como quedó declarado en CRITERIOS_SATURACION.md, se agregan escalones de 600 y 800
# usuarios SIN cambiar los criterios. Antes de medir se reinician los datos de la
# aplicación (MySQL y la cola de RabbitMQ) para partir del mismo estado que la corrida1;
# Prometheus conserva los datos de la corrida1.
#
#     bash evidencias/corrida_ampliada.sh
# Duración aproximada: 40 minutos.
# =====================================================================
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
OUT=evidencias/capturas
CORRIDA=corrida2
ESCALONES=10,25,50,100,200,400,600,800

paso() { echo; echo "===== $(date +%H:%M:%S) $* ====="; }
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

rm -rf "$OUT" resultados/"$CORRIDA" resultados/repeticion2
mkdir -p "$OUT"

paso "1. Datos de la aplicación desde cero (MySQL y RabbitMQ); Jaeger vacío"
docker compose rm -sf api consumer mysql rabbitmq mysqld-exporter jaeger >/dev/null 2>&1
docker volume rm lab04-pagos_mysql_data lab04-pagos_rabbitmq_data >/dev/null
cap 30_reinicio_datos "docker compose up -d --build" docker compose up -d --build
esperar_api
sleep 10
cap 31_servicios_arriba "docker compose ps" \
  docker compose ps --format "table {{.Name}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
cap 32_apm_recibe_datos "docker compose exec -T api python - < evidencias/verificar_apm.py" \
  sh -c "docker compose exec -T api python - < evidencias/verificar_apm.py"

paso "2. Línea base: 2 minutos en reposo"
sleep 120
cap 33_linea_base_docker_stats "docker stats --no-stream" \
  docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"

paso "3. Prueba de estrés: $CORRIDA (escalones $ESCALONES)"
cap 40_prueba_estres_k6 "docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=$ESCALONES k6" \
  sh -c "docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=$ESCALONES k6 2>&1 | grep -v 'running (' | grep -v 'escalones   \[' "
sleep 10
cap 41_analisis "docker compose run --rm analisis --corrida $CORRIDA" \
  docker compose run --rm analisis --corrida "$CORRIDA"

paso "4. Capturas del APM"
docker compose run --rm captura apm --corrida "$CORRIDA"

paso "5. Repetición del punto de saturación y del escalón siguiente"
REPETIR=$(docker compose run --rm -T --entrypoint python analisis -c "
import json
r = json.load(open('/resultados/$CORRIDA/resumen.json'))
e = [x['usuarios'] for x in r['escalones']]
p = r['punto_saturacion']
i = e.index(p['usuarios']) if p else 0
print(','.join(str(u) for u in e[i:i + 2]))
" | tr -d '\r')
echo "Se repiten los escalones: $REPETIR"
cap 42_repeticion_k6 "docker compose run --rm -e CORRIDA=repeticion2 -e ESCALONES=$REPETIR k6" \
  sh -c "docker compose run --rm -e CORRIDA=repeticion2 -e ESCALONES=$REPETIR k6 2>&1 | grep -v 'running (' | grep -v 'escalones   \[' "
sleep 10
cap 43_repeticion_analisis "docker compose run --rm analisis --corrida repeticion2" \
  docker compose run --rm analisis --corrida repeticion2

paso "6. Imágenes de las salidas de terminal"
docker compose run --rm captura terminales /ev/capturas
paso "Listo"
