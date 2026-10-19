#!/usr/bin/env bash
# =====================================================================
# Taller de Laboratorio N.º 4 - Confirmación del punto de saturación en limpio
# Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya
#
# Las repeticiones hechas justo después de una corrida larga empeoraron (p95 > 500 ms
# con 400 usuarios). Para separar el efecto del estado acumulado (≈80 000 mensajes en
# la cola de RabbitMQ, base de datos más grande) del efecto del equipo, se repiten los
# escalones 200 y 400 partiendo otra vez de datos limpios y tras 3 minutos de reposo.
#
#     bash evidencias/confirmacion_limpia.sh
# Duración aproximada: 15 minutos.
# =====================================================================
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
OUT=evidencias/capturas
CORRIDA=confirmacion
mkdir -p "$OUT"

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

echo "===== $(date +%H:%M:%S) Cola y datos antes de limpiar ====="
cap 60_cola_antes_de_limpiar "docker compose exec rabbitmq rabbitmqctl list_queues name messages" \
  docker compose exec -T rabbitmq rabbitmqctl list_queues -q name messages
docker compose rm -sf api consumer mysql rabbitmq mysqld-exporter >/dev/null 2>&1
docker volume rm lab04-pagos_mysql_data lab04-pagos_rabbitmq_data >/dev/null
docker compose up -d >/dev/null 2>&1
esperar_api
echo "===== $(date +%H:%M:%S) Reposo de 3 minutos ====="
sleep 180

echo "===== $(date +%H:%M:%S) Escalones 200 y 400 en limpio ====="
cap 61_confirmacion_k6 "docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=200,400 k6" \
  sh -c "docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=200,400 k6 2>&1 | grep -v 'running (' | grep -v 'escalones   \[' "
sleep 10
cap 62_confirmacion_analisis "docker compose run --rm analisis --corrida $CORRIDA" \
  docker compose run --rm analisis --corrida "$CORRIDA"
cap 63_cola_despues "docker compose exec rabbitmq rabbitmqctl list_queues name messages" \
  docker compose exec -T rabbitmq rabbitmqctl list_queues -q name messages
docker compose run --rm captura terminales /ev/capturas
echo "===== $(date +%H:%M:%S) Listo ====="
