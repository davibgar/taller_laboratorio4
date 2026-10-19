"""
Verificación de que la aplicación funciona y el APM recibe datos (Laboratorio 4).
Se ejecuta dentro del contenedor de la API (red interna de docker compose):
    docker compose exec -T api python - < evidencias/verificar_apm.py
"""
import json
import time
import urllib.parse
import urllib.request


def pedir(metodo, url, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    r = urllib.request.Request(url, data=datos, method=metodo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as respuesta:
        return respuesta.status, respuesta.read().decode()


print("CRUD de la API:")
for metodo, ruta, cuerpo in [
    ("POST", "/pagos", {"referencia": "VERIFICACION-1", "valor": 125000, "medio": "pse"}),
    ("PUT", "/pagos/1", {"referencia": "VERIFICACION-1-B", "valor": 130000, "medio": "nequi"}),
    ("GET", "/pagos/1", None),
    ("GET", "/pagos", None),
]:
    estado, texto = pedir(metodo, "http://localhost:8000" + ruta, cuerpo)
    print(f"  {metodo:4} {ruta:9} -> {estado} {texto[:110]}")

# Jaeger guarda el 20 % de las trazas: unas consultas más para que aparezca el servicio
for _ in range(30):
    pedir("GET", "http://localhost:8000/pagos/1")
time.sleep(15)  # el collector agrupa spans y Prometheus lee cada 5 s
print("\nServicios que reporta Jaeger (http://localhost:16686):")
print("  ", json.loads(pedir("GET", "http://jaeger:16686/api/services")[1])["data"])

print("\nPeticiones por operación que registra el APM (spanmetrics en Prometheus):")
consulta = 'sum by (span_name) (traces_span_metrics_calls_total{service_name="pagos-api",span_kind="SPAN_KIND_SERVER"})'
url = "http://prometheus:9090/api/v1/query?query=" + urllib.parse.quote(consulta)
for r in json.loads(pedir("GET", url)[1])["data"]["result"]:
    print(f"   {r['metric']['span_name']:22} {r['value'][1]} peticiones")

print("\nSpans de base de datos registrados por la API:")
consulta = 'sum by (span_name) (traces_span_metrics_calls_total{service_name="pagos-api",span_kind!="SPAN_KIND_SERVER"})'
url = "http://prometheus:9090/api/v1/query?query=" + urllib.parse.quote(consulta)
for r in json.loads(pedir("GET", url)[1])["data"]["result"]:
    print(f"   {r['metric']['span_name']:22} {r['value'][1]} spans")
