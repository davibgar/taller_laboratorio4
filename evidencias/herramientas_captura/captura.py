"""
Capturas del APM - Taller de Laboratorio N.º 4
Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya

Se ejecuta dentro de un contenedor Playwright unido a la red de docker compose
(no requiere instalar nada en el equipo):

    docker compose run --rm captura apm --corrida corrida1
    docker compose run --rm captura terminales /ev/capturas

- apm: con los tiempos de la corrida (resultados/<corrida>/resumen.json) fotografía
  el tablero de Grafana completo y sus paneles principales (peticiones por segundo,
  percentiles, tasa de error, desglose del tiempo, recursos), la pestaña Monitor de
  Jaeger (panel del servicio) y trazas individuales desglosadas de Jaeger.
- terminales: convierte cada .txt (salida real de un comando) en una imagen con
  aspecto de terminal. La primera línea del .txt es la hora y la segunda el comando.
"""
import argparse
import html
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

GRAFANA = "http://grafana:3000"
JAEGER = "http://jaeger:16686"
ZONA = ZoneInfo("America/Bogota")
SALIDA = Path("/ev/capturas")

# Paneles del tablero (ids asignados por observabilidad/grafana/generar_tablero.py)
PANELES = {
    7: "panel_peticiones_por_operacion",
    8: "panel_latencia_percentiles",
    9: "panel_latencia_p95_por_operacion",
    10: "panel_tasa_error_por_operacion",
    11: "panel_desglose_tiempo",
    12: "panel_duracion_sql",
    13: "panel_cpu_contenedores",
    14: "panel_memoria_contenedores",
    15: "panel_conexiones_mysql",
    16: "panel_pool_api",
    17: "panel_k6_usuarios",
}

MARCO = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#dfe3e8;font-family:Segoe UI,Arial,sans-serif}}
.win{{margin:14px;border-radius:8px;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,.35);background:#fff;display:inline-block;min-width:760px;max-width:1470px}}
.bar{{background:#2b2b2b;color:#ddd;font-size:13px;padding:7px 12px;display:flex;justify-content:space-between}}
.tag{{color:#8ab4f8}}
pre{{margin:0;padding:14px 16px;background:#0c0c0c;color:#e6e6e6;font:13px/1.35 Consolas,'DejaVu Sans Mono',monospace;white-space:pre-wrap;word-break:break-all}}
.cmd{{color:#f9f1a5}} .err{{color:#ff6b6b}} .ok{{color:#7ee787}}
</style></head><body><div class="win">{cuerpo}</div></body></html>"""


def hora(ts):
    return datetime.fromtimestamp(ts, ZONA).strftime("%H:%M:%S")


def jaeger_api(ruta, **params):
    url = f"{JAEGER}/api/{ruta}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["data"]


# ---------------------------------------------------------------------------
# Grafana
# ---------------------------------------------------------------------------
def esperar_grafana(pagina):
    pagina.wait_for_load_state("networkidle")
    time.sleep(4)  # las gráficas terminan de dibujarse después de recibir los datos


def grafana(pagina, desde_ms, hasta_ms, prefijo):
    rango = f"from={desde_ms}&to={hasta_ms}&timezone=browser&refresh="
    pagina.set_viewport_size({"width": 1700, "height": 1900})
    pagina.goto(f"{GRAFANA}/d/apm-pagos/apm?orgId=1&{rango}&kiosk")
    esperar_grafana(pagina)
    ruta = SALIDA / f"{prefijo}_01_grafana_tablero_completo.png"
    pagina.screenshot(path=str(ruta), full_page=True)
    print("captura", ruta.name)

    pagina.set_viewport_size({"width": 1300, "height": 520})
    for numero, (panel, nombre) in enumerate(PANELES.items(), start=2):
        pagina.goto(f"{GRAFANA}/d-solo/apm-pagos/apm?orgId=1&panelId={panel}&{rango}")
        esperar_grafana(pagina)
        ruta = SALIDA / f"{prefijo}_{numero:02d}_grafana_{nombre}.png"
        pagina.screenshot(path=str(ruta))
        print("captura", ruta.name)


# ---------------------------------------------------------------------------
# Jaeger
# ---------------------------------------------------------------------------
def jaeger_monitor(pagina, prefijo):
    """Pestaña Monitor (Service Performance Monitoring): panel del servicio con
    latencia, tasa de error y peticiones por segundo de cada operación."""
    pagina.set_viewport_size({"width": 1600, "height": 1300})
    pagina.goto(f"{JAEGER}/monitor")
    pagina.wait_for_load_state("networkidle")
    # Selector de servicio (el primero de la página): se elige la API
    pagina.locator(".ant-select").first.click()
    pagina.locator(".ant-select-item-option", has_text="pagos-api").first.click()
    pagina.wait_for_load_state("networkidle")
    time.sleep(3)
    ruta = SALIDA / f"{prefijo}_20_jaeger_monitor_servicio.png"
    pagina.screenshot(path=str(ruta), full_page=True)
    print("captura", ruta.name)


def elegir_traza(operacion, desde_ts, hasta_ts, percentil=0.95):
    """Traza de la operación en la ventana dada cuya duración está cerca del percentil indicado."""
    trazas = jaeger_api("traces", service="pagos-api", operation=operacion, limit=500,
                        start=int(desde_ts * 1e6), end=int(hasta_ts * 1e6))
    candidatas = []
    for traza in trazas:
        raiz = min(traza["spans"], key=lambda s: s["startTime"])
        if raiz["operationName"] == operacion and len(traza["spans"]) >= 3:
            candidatas.append((raiz["duration"], traza["traceID"]))
    if not candidatas:
        return None
    candidatas.sort()
    return candidatas[min(int(len(candidatas) * percentil), len(candidatas) - 1)]


def jaeger_traza(pagina, operacion, desde_ts, hasta_ts, ruta, detalle=None, percentil=0.95):
    elegida = elegir_traza(operacion, desde_ts, hasta_ts, percentil)
    if elegida is None:
        print("sin trazas de", operacion, "entre", hora(desde_ts), "y", hora(hasta_ts))
        return None
    duracion_us, traza = elegida
    pagina.set_viewport_size({"width": 1600, "height": 900})
    pagina.goto(f"{JAEGER}/trace/{traza}")
    pagina.wait_for_selector("text=Trace Start")
    time.sleep(1.5)
    if detalle:
        # Abre el detalle del span (atributos: sentencia SQL, tiempo, etc.)
        pagina.locator(".span-name", has_text=detalle).first.click()
        time.sleep(1)
    pagina.screenshot(path=str(ruta), full_page=True)
    print("captura", ruta.name, operacion, f"{duracion_us / 1000:.1f} ms", traza)
    return {"traza": traza, "operacion": operacion, "duracion_ms": duracion_us / 1000}


def apm(corrida):
    resumen = json.loads(Path(f"/resultados/{corrida}/resumen.json").read_text(encoding="utf-8"))
    escalones = resumen["escalones"]
    desde_ms = int((resumen["inicio_prueba_ts"] - 120) * 1000)
    hasta_ms = int(resumen["fin_prueba_ts"] * 1000)  # los indicadores muestran el último escalón
    punto = resumen.get("punto_saturacion")
    # Escalón donde se rompió el primer criterio (el siguiente al punto de saturación)
    numero_quiebre = (punto["escalon"] + 1) if punto else 1
    quiebre = next((e for e in escalones if e["numero"] == numero_quiebre), escalones[-1])
    primero = escalones[0]
    SALIDA.mkdir(parents=True, exist_ok=True)
    prefijo = corrida
    trazas = []
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(timezone_id="America/Bogota", locale="es-CO")
        grafana(pagina, desde_ms, hasta_ms, prefijo)
        jaeger_monitor(pagina, prefijo)
        casos = [
            ("POST /pagos", primero, "INSERT pagos_db", 0.5, "21_jaeger_traza_crear_carga_baja"),
            ("GET /pagos/{pago_id}", primero, "SELECT pagos_db", 0.5, "22_jaeger_traza_consultar_carga_baja"),
            ("POST /pagos", quiebre, "pool.acquire", 0.95, "23_jaeger_traza_crear_saturado"),
            ("GET /pagos", quiebre, "pool.acquire", 0.95, "24_jaeger_traza_listar_saturado"),
        ]
        for operacion, escalon, detalle, percentil, nombre in casos:
            ruta = SALIDA / f"{prefijo}_{nombre}.png"
            info = jaeger_traza(pagina, operacion, escalon["desde_ts"], escalon["hasta_ts"], ruta, detalle, percentil)
            if info:
                trazas.append({**info, "archivo": ruta.name, "escalon": escalon["numero"],
                               "usuarios": escalon["usuarios"]})
        navegador.close()
    (SALIDA / f"{prefijo}_trazas.json").write_text(json.dumps(trazas, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Salidas de terminal
# ---------------------------------------------------------------------------
def colorear(linea):
    texto = html.escape(linea)
    if any(p in linea for p in ("ERROR", "Error", "Exited", "error", "unhealthy")):
        return f'<span class="err">{texto}</span>'
    if any(p in linea for p in ("healthy", "✓", "Started", "Healthy")):
        return f'<span class="ok">{texto}</span>'
    return texto


def terminales(carpeta):
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        for txt in sorted(Path(carpeta).glob("*.txt")):
            lineas = txt.read_text(encoding="utf-8", errors="replace").splitlines()
            fecha, comando, salida = lineas[0], lineas[1], lineas[2:]
            cuerpo = (f'<div class="bar"><span>Windows PowerShell - Laboratorio04_APM_Estres</span>'
                      f'<span class="tag">{html.escape(fecha)}</span></div><pre>'
                      f'<span class="cmd">PS C:\\Laboratorio04_APM_Estres&gt; {html.escape(comando)}</span>\n'
                      + "\n".join(colorear(linea) for linea in salida) + "</pre>")
            pagina.set_viewport_size({"width": 1500, "height": 400})
            pagina.set_content(MARCO.format(cuerpo=cuerpo))
            png = txt.with_suffix(".png")
            pagina.locator(".win").screenshot(path=str(png))
            print("captura", png.name)
        navegador.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("modo", choices=["apm", "terminales"])
    parser.add_argument("carpeta", nargs="?", default="/ev/capturas")
    parser.add_argument("--corrida")
    args = parser.parse_args()
    if args.modo == "apm":
        apm(args.corrida)
    else:
        terminales(args.carpeta)
