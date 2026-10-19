"""
Genera observabilidad/grafana/tableros/apm-pagos.json (tablero de Grafana del Laboratorio 4).
Se versiona el JSON generado; este script sólo facilita modificarlo:
    docker run --rm -v "${PWD}/observabilidad/grafana:/g" -w /g python:3.12-slim python generar_tablero.py
"""
import json

PROM = {"type": "prometheus", "uid": "prometheus"}
SERVIDOR = 'service_name="pagos-api",span_kind="SPAN_KIND_SERVER"'
API = 'service_name="pagos-api"'
LLAMADAS = "traces_span_metrics_calls_total"
DURACION = "traces_span_metrics_duration_milliseconds"
RI = "$__rate_interval"


def tasa(filtro, por=None, metrica=LLAMADAS):
    agrupacion = f" by ({por})" if por else ""
    return f"sum{agrupacion} (rate({metrica}{{{filtro}}}[{RI}]))"


def percentil(q, por=None):
    claves = "le" + (f", {por}" if por else "")
    return f"histogram_quantile({q}, sum by ({claves}) (rate({DURACION}_bucket{{{SERVIDOR}}}[{RI}])))"


def error_pct(por=None):
    agrupacion = f" by ({por})" if por else ""
    errores = f'sum{agrupacion} (rate({LLAMADAS}{{{SERVIDOR},status_code="STATUS_CODE_ERROR"}}[{RI}]))'
    total = tasa(SERVIDOR, por)
    # "or 0 * total" para que la serie exista (en 0 %) aunque no haya errores
    return f"100 * ({errores} or 0 * {total}) / {total}"


def por_peticion(filtro):
    """Milisegundos promedio por petición consumidos en los spans que cumplen el filtro."""
    return f"({tasa(filtro, metrica=DURACION + '_sum')} or vector(0)) / {tasa(SERVIDOR)}"


paneles = []


def panel(titulo, objetivos, x, y, w=12, h=8, tipo="timeseries", unidad="short", descripcion="",
          apilado=False, minimo=0, umbral=None, decimales=None):
    ident = len(paneles) + 1
    defaults = {"unit": unidad, "min": minimo, "custom": {"lineWidth": 2, "fillOpacity": 10, "showPoints": "never"}}
    if decimales is not None:
        defaults["decimals"] = decimales
    if apilado:
        defaults["custom"].update({"stacking": {"mode": "normal"}, "fillOpacity": 60})
    if umbral is not None:
        defaults["thresholds"] = {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                                {"color": "red", "value": umbral}]}
        defaults["custom"]["thresholdsStyle"] = {"mode": "dashed" if tipo == "timeseries" else "off"}
    p = {
        "id": ident, "type": tipo, "title": titulo, "description": descripcion, "datasource": PROM,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "targets": [{"refId": chr(65 + i), "datasource": PROM, "expr": expr, "legendFormat": leyenda,
                     "range": True} for i, (expr, leyenda) in enumerate(objetivos)],
    }
    if tipo == "timeseries":
        p["options"] = {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}
    else:
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"}
    paneles.append(p)


# --- Fila 1: indicadores ------------------------------------------------------------
panel("Usuarios concurrentes (k6)", [("max(k6_vus)", "usuarios")], 0, 0, 4, 4, "stat")
panel("Peticiones / s (APM)", [(tasa(SERVIDOR), "pet/s")], 4, 0, 4, 4, "stat", "reqps", decimales=1)
panel("Latencia p95 (APM)", [(percentil(0.95), "p95")], 8, 0, 4, 4, "stat", "ms", umbral=500, decimales=0)
panel("Tasa de error (APM)", [(error_pct(), "error %")], 12, 0, 4, 4, "stat", "percent", umbral=1, decimales=2)
panel("CPU de la API", [('max(contenedor_cpu_porcentaje{servicio="api"})', "CPU")], 16, 0, 4, 4, "stat", "percent",
      umbral=90, decimales=0)
panel("Pool de la API en uso", [("max(db_pool_en_uso)", "en uso")], 20, 0, 4, 4, "stat", decimales=0)

# --- Fila 2: rendimiento por operación -----------------------------------------------
panel("Peticiones por segundo por operación", [(tasa(SERVIDOR, "span_name"), "{{span_name}}"),
                                               (tasa(SERVIDOR), "TOTAL")], 0, 4, unidad="reqps",
      descripcion="Calculado por el conector spanmetrics a partir del 100 % de los spans de servidor de la API.")
panel("Latencia p50 / p95 / p99 (todas las operaciones)",
      [(percentil(0.50), "p50"), (percentil(0.95), "p95"), (percentil(0.99), "p99")], 12, 4, unidad="ms", umbral=500,
      descripcion="La línea roja es el criterio declarado: p95 ≤ 500 ms.")
panel("Latencia p95 por operación", [(percentil(0.95, "span_name"), "{{span_name}}")], 0, 12, unidad="ms",
      umbral=500)
panel("Tasa de error por operación", [(error_pct("span_name"), "{{span_name}}"), (error_pct(), "TOTAL")], 12, 12,
      unidad="percent", umbral=1, descripcion="Spans de servidor con estado ERROR (respuestas 5xx). Criterio: ≤ 1 %.")

# --- Fila 3: ¿dónde se va el tiempo? -------------------------------------------------
consultas_bd = f'{API},span_kind="SPAN_KIND_CLIENT"'
espera_pool = f'{API},span_name="pool.acquire"'
publicacion = f'{API},span_kind="SPAN_KIND_PRODUCER"'
total = f"{tasa(SERVIDOR, metrica=DURACION + '_sum')} / {tasa(SERVIDOR)}"
panel("¿Dónde se va el tiempo de una petición? (ms promedio)", [
    (f"{total} - {por_peticion(consultas_bd)} - {por_peticion(espera_pool)} - {por_peticion(publicacion)}",
     "aplicación (Python)"),
    (por_peticion(espera_pool), "espera por conexión del pool"),
    (por_peticion(consultas_bd), "consultas a MySQL"),
    (por_peticion(publicacion), "publicación en RabbitMQ"),
], 0, 20, unidad="ms", apilado=True,
    descripcion="Suma de la duración de los spans hijos dividida entre las peticiones atendidas. "
                "La aplicación es el resto: duración del span de servidor menos los demás.")
panel("Duración media de cada sentencia SQL", [
    (f"{tasa(consultas_bd, 'span_name', DURACION + '_sum')} / {tasa(consultas_bd, 'span_name')}", "{{span_name}}"),
    (f"{tasa(espera_pool, metrica=DURACION + '_sum')} / {tasa(espera_pool)}", "pool.acquire (espera)"),
], 12, 20, unidad="ms")

# --- Fila 4: recursos -------------------------------------------------------------
panel("CPU de los contenedores (100 % = 1 núcleo)", [("contenedor_cpu_porcentaje", "{{servicio}}")], 0, 28,
      unidad="percent", descripcion="Mismo cálculo que docker stats. API y MySQL tienen un límite de 1 CPU (100 %).")
panel("Memoria de los contenedores", [("contenedor_memoria_bytes", "{{servicio}}")], 12, 28, unidad="bytes")
panel("Conexiones a MySQL", [
    ("mysql_global_status_threads_connected", "conexiones activas"),
    ("mysql_global_status_threads_running", "ejecutando una consulta"),
    ("mysql_global_variables_max_connections", "max_connections"),
], 0, 36, descripcion="Conexiones abiertas contra MySQL frente al máximo configurado en la base de datos.")
panel("Pool de conexiones de la API", [
    ("max(db_pool_en_uso)", "en uso"), ("max(db_pool_abiertas)", "abiertas"),
    ("max(db_pool_maximo)", "máximo del pool"), ("max(db_pool_en_espera)", "peticiones esperando conexión"),
], 12, 36, descripcion="Si 'en uso' llega al máximo y crecen las peticiones en espera, el pool es el cuello de botella.")

# --- Fila 5: generador de carga -------------------------------------------------------
panel("Generador de carga (k6): usuarios y peticiones/s", [
    ("max(k6_vus)", "usuarios"), ("sum(rate(k6_http_reqs_total[$__rate_interval]))", "pet/s enviadas"),
], 0, 44)
K6_FALLIDAS = 'sum(rate(k6_http_reqs_total{expected_response="false"}[$__rate_interval]))'
K6_TOTAL = "sum(rate(k6_http_reqs_total[$__rate_interval]))"
panel("Generador de carga (k6): peticiones fallidas vistas por el cliente", [
    (f"100 * ({K6_FALLIDAS} or 0 * {K6_TOTAL}) / {K6_TOTAL}", "error % (k6)"),
], 12, 44, unidad="percent", umbral=1,
    descripcion="Incluye los timeouts (10 s) que el servidor no registra como error: el cliente se rinde antes.")

tablero = {
    "uid": "apm-pagos", "title": "APM - CRUD de pagos (Laboratorio 04)", "tags": ["laboratorio04", "apm"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1,
    "refresh": "5s", "time": {"from": "now-30m", "to": "now"}, "panels": paneles,
    "links": [{"title": "Jaeger (trazas)", "url": "http://localhost:16686/search?service=pagos-api",
               "type": "link", "targetBlank": True}],
}
with open("tableros/apm-pagos.json", "w", encoding="utf-8") as archivo:
    json.dump(tablero, archivo, ensure_ascii=False, indent=2)
print(f"tableros/apm-pagos.json: {len(paneles)} paneles")
