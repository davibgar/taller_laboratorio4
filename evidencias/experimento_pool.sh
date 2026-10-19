#!/usr/bin/env bash
# =====================================================================
# Taller de Laboratorio N.º 4 - Experimento para las preguntas 4 y 5
# Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya
#
# Hipótesis a contrastar: ¿el límite lo impone el pool de 10 conexiones de la API
# o la CPU de la API (un proceso de uvicorn con 1 CPU)?
# Se repiten los escalones de 400 y 600 usuarios con el pool en 30 conexiones
# (DB_POOL_MAX=30), partiendo de datos limpios y tras 3 minutos de reposo, igual
# que la corrida de confirmación. Al terminar, la API vuelve al pool de 10.
#
#     bash evidencias/experimento_pool.sh
# Duración aproximada: 15 minutos.
#
# IMPORTANTE: DB_POOL_MAX se exporta para todo el script. Si sólo se pasara al
# "docker compose up", el "docker compose run k6" posterior vería otra configuración
# de la API (pool de 10) y la recrearía antes de la prueba.
# =====================================================================
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
OUT=evidencias/capturas
POOL=${POOL:-30}
CORRIDA=pool${POOL}_limpio   # "pool30" quedó usado por un intento inválido (la API se recreó con pool 10)
export DB_POOL_MAX=$POOL
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

echo "===== $(date +%H:%M:%S) Datos limpios y API con pool de $POOL conexiones ====="
docker compose rm -sf api consumer mysql rabbitmq mysqld-exporter >/dev/null 2>&1
docker volume rm lab04-pagos_mysql_data lab04-pagos_rabbitmq_data >/dev/null
docker compose up -d >/dev/null 2>&1
esperar_api
cap 50_experimento_pool_config "docker compose logs api | Select-String 'Pool de conexiones'" \
  sh -c "docker compose logs api --no-color | grep 'Pool de conexiones'"
echo "===== $(date +%H:%M:%S) Reposo de 3 minutos ====="
sleep 180

echo "===== $(date +%H:%M:%S) Escalones 400 y 600 con pool de $POOL ====="
cap 51_experimento_pool_k6 "\$env:DB_POOL_MAX=$POOL; docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=400,600 k6" \
  sh -c "docker compose run --rm -e CORRIDA=$CORRIDA -e ESCALONES=400,600 k6 2>&1 | grep -v 'running (' | grep -v 'escalones   \[' "
sleep 10
cap 52_experimento_pool_analisis "docker compose run --rm analisis --corrida $CORRIDA" \
  docker compose run --rm analisis --corrida "$CORRIDA"
cap 53_experimento_pool_config_final "docker compose logs api | Select-String 'Pool de conexiones'" \
  sh -c "docker compose logs api --no-color | grep 'Pool de conexiones'"

echo "===== $(date +%H:%M:%S) API de vuelta al pool de 10 ====="
unset DB_POOL_MAX
docker compose up -d api
esperar_api
docker compose run --rm captura terminales /ev/capturas
echo "===== $(date +%H:%M:%S) Listo ====="
