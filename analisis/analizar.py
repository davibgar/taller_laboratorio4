"""
Análisis de una corrida de la prueba de estrés - Taller de Laboratorio N.º 4
Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya

    docker compose run --rm analisis --corrida corrida1

1. Encuentra los escalones de la corrida en Prometheus a partir de la métrica k6_vus
   (cada tramo en el que el número de usuarios se mantiene constante). El primero, de
   calentamiento, se descarta.
2. Para cada escalón descarta los primeros segundos (--descartar) y, sobre el resto
   de la ventana, consulta en Prometheus:
     - del APM (spanmetrics): peticiones/s, p50, p95, p99, tasa de error y desglose del tiempo;
     - de las estadísticas de los contenedores: CPU y memoria de la API, de MySQL y de k6;
     - de MySQL y del pool de la API: conexiones activas y en espera.
3. Evalúa los tres criterios declarados en CRITERIOS_SATURACION.md, determina el punto
   de saturación y escribe en /resultados/<corrida>/:
     tabla.md, tabla.csv, resumen.json y las gráficas (PNG).
"""
import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PROM = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
ZONA = ZoneInfo(os.getenv("TZ", "America/Bogota"))
SERVIDOR = 'service_name="pagos-api",span_kind="SPAN_KIND_SERVER"'
API = 'service_name="pagos-api"'
LLAMADAS = "traces_span_metrics_calls_total"
DURACION = "traces_span_metrics_duration_milliseconds"


# ---------------------------------------------------------------------------
# Consultas a Prometheus
# ---------------------------------------------------------------------------
def consulta(expr, momento):
    r = requests.get(f"{PROM}/api/v1/query", params={"query": expr, "time": momento}, timeout=30)
    r.raise_for_status()
    datos = r.json()["data"]
    return datos["resultType"], datos["result"]


def escalar(expr, momento, defecto=None):
    """Valor de una consulta que devuelve una sola serie (o None si no hay datos)."""
    _, resultado = consulta(expr, momento)
    if not resultado:
        return defecto
    valor = float(resultado[0]["value"][1])
    return defecto if math.isnan(valor) or math.isinf(valor) else valor


def por_etiqueta(expr, etiqueta, momento):
    _, resultado = consulta(expr, momento)
    return {r["metric"].get(etiqueta, "?"): float(r["value"][1]) for r in resultado}


def muestras_vus(corrida):
    """Muestras crudas de k6_vus de la corrida: [(timestamp, usuarios)]."""
    tipo, resultado = consulta(f'k6_vus{{corrida="{corrida}"}}[30d]', datetime.now().timestamp())
    if not resultado:
        sys.exit(f"No hay datos de k6_vus para la corrida '{corrida}'. ¿Se ejecutó con CORRIDA={corrida}?")
    puntos = {}
    for serie in resultado:
        for ts, valor in serie["values"]:
            puntos[float(ts)] = max(puntos.get(float(ts), 0), float(valor))
    return sorted(puntos.items())


def tramos_constantes(puntos, minimo_s):
    """Agrupa las muestras consecutivas con el mismo número de usuarios."""
    tramos, actual = [], None
    for ts, vus in puntos:
        if actual and vus == actual["usuarios"]:
            actual["fin"] = ts
        else:
            actual = {"usuarios": int(vus), "inicio": ts, "fin": ts}
            tramos.append(actual)
    return [t for t in tramos if t["usuarios"] > 0 and t["fin"] - t["inicio"] >= minimo_s]


# ---------------------------------------------------------------------------
# Medición de un escalón
# ---------------------------------------------------------------------------
def medir(inicio, fin, corrida):
    v = max(int(fin - inicio), 10)
    w = f"{v}s"
    t = fin

    def tasa(filtro, metrica=LLAMADAS):
        return f"sum(rate({metrica}{{{filtro}}}[{w}]))"

    def percentil(q):
        return escalar(f"histogram_quantile({q}, sum by (le) (rate({DURACION}_bucket{{{SERVIDOR}}}[{w}])))", t)

    def contenedor(servicio, metrica, funcion="avg_over_time"):
        return escalar(f'max({funcion}({metrica}{{servicio="{servicio}"}}[{w}]))', t)

    def ms_por_peticion(filtro):
        suma = escalar(tasa(filtro, f"{DURACION}_sum"), t, 0.0)
        return suma / pet_s if pet_s else None

    pet_s = escalar(tasa(SERVIDOR), t, 0.0)
    errores = escalar(f'sum(rate({LLAMADAS}{{{SERVIDOR},status_code="STATUS_CODE_ERROR"}}[{w}]))', t, 0.0)
    k6_total = escalar(f'sum(rate(k6_http_reqs_total{{corrida="{corrida}"}}[{w}]))', t, 0.0)
    k6_fallidas = escalar(f'sum(rate(k6_http_reqs_total{{corrida="{corrida}",expected_response="false"}}[{w}]))',
                          t, 0.0)
    total_ms = ms_por_peticion(SERVIDOR)
    bd_ms = ms_por_peticion(f'{API},span_kind="SPAN_KIND_CLIENT"')
    pool_ms = ms_por_peticion(f'{API},span_name="pool.acquire"')
    cola_ms = ms_por_peticion(f'{API},span_kind="SPAN_KIND_PRODUCER"')
    app_ms = None if total_ms is None else max(total_ms - bd_ms - pool_ms - cola_ms, 0.0)
    mb = 1024 * 1024
    medicion = {
        "ventana_s": v,
        "pet_s": pet_s,
        "pet_s_por_operacion": por_etiqueta(f"sum by (span_name) (rate({LLAMADAS}{{{SERVIDOR}}}[{w}]))",
                                            "span_name", t),
        "p50_ms": percentil(0.50),
        "p95_ms": percentil(0.95),
        "p99_ms": percentil(0.99),
        "p95_ms_por_operacion": por_etiqueta(
            f"histogram_quantile(0.95, sum by (le, span_name) (rate({DURACION}_bucket{{{SERVIDOR}}}[{w}])))",
            "span_name", t),
        "error_apm_pct": 100 * errores / pet_s if pet_s else 0.0,
        "k6_pet_s": k6_total,
        "error_k6_pct": 100 * k6_fallidas / k6_total if k6_total else 0.0,
        "cpu_api_pct": contenedor("api", "contenedor_cpu_porcentaje"),
        "cpu_api_max_pct": contenedor("api", "contenedor_cpu_porcentaje", "max_over_time"),
        "cpu_bd_pct": contenedor("mysql", "contenedor_cpu_porcentaje"),
        "cpu_bd_max_pct": contenedor("mysql", "contenedor_cpu_porcentaje", "max_over_time"),
        "cpu_k6_pct": contenedor("k6", "contenedor_cpu_porcentaje"),
        "cpu_collector_pct": contenedor("otel-collector", "contenedor_cpu_porcentaje"),
        "mem_api_mb": (contenedor("api", "contenedor_memoria_bytes") or 0) / mb,
        "mem_bd_mb": (contenedor("mysql", "contenedor_memoria_bytes") or 0) / mb,
        "conexiones_bd_max": escalar(f"max(max_over_time(mysql_global_status_threads_connected[{w}]))", t),
        "max_connections": escalar("max(mysql_global_variables_max_connections)", t),
        "pool_en_uso_prom": escalar(f"max(avg_over_time(db_pool_en_uso[{w}]))", t),
        "pool_en_uso_max": escalar(f"max(max_over_time(db_pool_en_uso[{w}]))", t),
        "pool_maximo": escalar("max(db_pool_maximo)", t),
        "pool_en_espera_prom": escalar(f"max(avg_over_time(db_pool_en_espera[{w}]))", t),
        "pool_en_espera_max": escalar(f"max(max_over_time(db_pool_en_espera[{w}]))", t),
        "tiempo_ms": {"total": total_ms, "aplicacion": app_ms, "espera_pool": pool_ms,
                      "consultas_bd": bd_ms, "publicacion_cola": cola_ms},
    }
    return medicion


def linea_base(antes_de):
    """Consumo en reposo (con el APM corriendo) en los 2 minutos previos a la prueba."""
    fin = antes_de - 5
    w = "115s"
    servicios = por_etiqueta(f"avg_over_time(contenedor_cpu_porcentaje[{w}])", "servicio", fin)
    memoria = por_etiqueta(f"avg_over_time(contenedor_memoria_bytes[{w}])", "servicio", fin)
    return {
        "desde": hora(fin - 115), "hasta": hora(fin),
        "cpu_pct": {s: round(c, 2) for s, c in sorted(servicios.items())},
        "memoria_mb": {s: round(m / 1024 / 1024, 1) for s, m in sorted(memoria.items())},
        "conexiones_bd": escalar(f"max(avg_over_time(mysql_global_status_threads_connected[{w}]))", fin),
    }


# ---------------------------------------------------------------------------
# Criterios y salida
# ---------------------------------------------------------------------------
def hora(ts):
    return datetime.fromtimestamp(ts, ZONA).strftime("%Y-%m-%d %H:%M:%S")


def evaluar(escalones, criterios):
    anterior = None
    for e in escalones:
        m = e["medicion"]
        error = max(m["error_apm_pct"], m["error_k6_pct"])
        crecimiento = None if anterior is None else 100 * (m["pet_s"] / anterior - 1) if anterior else None
        e["criterios"] = {
            "error_pct": error,
            "cumple_error": error <= criterios.max_error,
            "cumple_p95": m["p95_ms"] is not None and m["p95_ms"] <= criterios.max_p95,
            "crecimiento_pct": crecimiento,
            "cumple_crecimiento": crecimiento is None or crecimiento >= criterios.crecimiento,
        }
        c = e["criterios"]
        c["cumple_todo"] = c["cumple_error"] and c["cumple_p95"] and c["cumple_crecimiento"]
        anterior = m["pet_s"]
    punto = None
    for e in escalones:
        if not e["criterios"]["cumple_todo"]:
            break
        punto = e
    return punto


def f(valor, decimales=1):
    return "–" if valor is None else f"{valor:,.{decimales}f}".replace(",", " ")


def tabla_markdown(escalones, punto):
    filas = ["| Esc. | Usuarios | Pet./s | p50 (ms) | p95 (ms) | p99 (ms) | Error % | CPU app | CPU BD | "
             "Memoria app / BD (MB) | Conex. BD (activas / máx.) | Pool app (en uso / tamaño; en espera) | Cumple |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in escalones:
        m, c = e["medicion"], e["criterios"]
        marca = "✅" if c["cumple_todo"] else "❌ " + ", ".join(
            n for n, ok in (("error", c["cumple_error"]), ("p95", c["cumple_p95"]),
                            ("throughput", c["cumple_crecimiento"])) if not ok)
        if punto is e:
            marca += " **← punto de saturación**"
        filas.append(
            f"| {e['numero']} | {e['usuarios']} | {f(m['pet_s'])} | {f(m['p50_ms'], 0)} | {f(m['p95_ms'], 0)} | "
            f"{f(m['p99_ms'], 0)} | {f(c['error_pct'], 2)} | {f(m['cpu_api_pct'], 0)} % | {f(m['cpu_bd_pct'], 0)} % | "
            f"{f(m['mem_api_mb'], 0)} / {f(m['mem_bd_mb'], 0)} | {f(m['conexiones_bd_max'], 0)} / "
            f"{f(m['max_connections'], 0)} | {f(m['pool_en_uso_max'], 0)} / {f(m['pool_maximo'], 0)}; "
            f"{f(m['pool_en_espera_prom'], 0)} prom. ({f(m['pool_en_espera_max'], 0)} máx.) | {marca} |")
    return "\n".join(filas)


def graficas(escalones, punto, criterios, carpeta, corrida):
    usuarios = [e["usuarios"] for e in escalones]
    # Escalones equiespaciados en el eje X (10, 25 y 50 usuarios se enciman en una escala lineal)
    pos = list(range(len(usuarios)))
    pos_punto = usuarios.index(punto["usuarios"]) if punto else None
    pet = [e["medicion"]["pet_s"] for e in escalones]
    p95 = [e["medicion"]["p95_ms"] or float("nan") for e in escalones]
    err = [e["criterios"]["error_pct"] for e in escalones]
    marcas = {"marker": "o", "linewidth": 2, "markersize": 7}

    def base(titulo, eje_y, nombre, valores, color, umbral=None, etiqueta_umbral=None, formato="{:.1f}"):
        fig, ax = plt.subplots(figsize=(9, 5.2), dpi=130)
        ax.plot(pos, valores, color=color, **marcas)
        for x, y in zip(pos, valores):
            if not math.isnan(y):
                ax.annotate(formato.format(y), (x, y), textcoords="offset points", xytext=(0, 9), ha="center",
                            fontsize=9)
        if umbral is not None:
            ax.axhline(umbral, color="#c0392b", linestyle="--", linewidth=1.3, label=etiqueta_umbral)
        if punto:
            ax.axvline(pos_punto, color="#7f8c8d", linestyle=":", linewidth=1.5,
                       label=f"Punto de saturación ({punto['usuarios']} usuarios)")
        ax.set_title(f"{titulo}\n(corrida {corrida})", fontsize=12)
        ax.set_xlabel("Usuarios concurrentes (escalones)")
        ax.set_ylabel(eje_y)
        ax.set_xticks(pos, [str(u) for u in usuarios])
        ax.grid(alpha=0.3)
        tope = max([v for v in valores if not math.isnan(v)] + [umbral or 0, 1e-9])
        ax.set_ylim(0, tope * 1.15)
        if umbral is not None or punto:
            ax.legend(loc="upper left")
        fig.tight_layout()
        fig.savefig(carpeta / nombre)
        plt.close(fig)

    base("Usuarios vs. peticiones por segundo (throughput)", "Peticiones por segundo (APM)",
         "grafica_1_usuarios_vs_peticiones.png", pet, "#2471a3")
    base("Usuarios vs. latencia p95", "Latencia p95 (ms, APM)", "grafica_2_usuarios_vs_p95.png", p95, "#d68910",
         criterios.max_p95, f"Criterio: p95 ≤ {criterios.max_p95:g} ms", "{:.0f}")
    base("Usuarios vs. tasa de error", "Tasa de error (%)", "grafica_3_usuarios_vs_error.png", err, "#c0392b",
         criterios.max_error, f"Criterio: error ≤ {criterios.max_error:g} %", "{:.2f}")

    # Extra: dónde se va el tiempo de una petición en cada escalón (pregunta 2)
    partes = [("aplicacion", "Aplicación (Python)", "#5dade2"), ("espera_pool", "Espera por conexión del pool", "#e67e22"),
              ("consultas_bd", "Consultas a MySQL", "#27ae60"), ("publicacion_cola", "Publicación en RabbitMQ", "#8e44ad")]
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=130)
    etiquetas = [str(u) for u in usuarios]
    abajo = [0.0] * len(escalones)
    for clave, nombre, color in partes:
        valores = [e["medicion"]["tiempo_ms"][clave] or 0 for e in escalones]
        ax.bar(etiquetas, valores, bottom=abajo, label=nombre, color=color)
        abajo = [a + v for a, v in zip(abajo, valores)]
    for i, e in enumerate(escalones):
        total = e["medicion"]["tiempo_ms"]["total"] or 0
        bd = (e["medicion"]["tiempo_ms"]["consultas_bd"] or 0) + (e["medicion"]["tiempo_ms"]["espera_pool"] or 0)
        ax.annotate(f"{total:.0f} ms\nBD+pool {100 * bd / total:.0f} %" if total else "", (i, abajo[i]),
                    textcoords="offset points", xytext=(0, 4), ha="center", fontsize=8)
    ax.set_title(f"Tiempo promedio de una petición según las trazas del APM\n(corrida {corrida})", fontsize=12)
    ax.set_xlabel("Usuarios concurrentes")
    ax.set_ylabel("ms por petición")
    ax.set_ylim(0, max(abajo) * 1.2 if max(abajo) else 1)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2, fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(carpeta / "grafica_4_desglose_tiempo.png")
    plt.close(fig)

    # Extra: recursos (pregunta 4)
    fig, ax1 = plt.subplots(figsize=(9, 5.2), dpi=130)
    ax1.plot(pos, [e["medicion"]["cpu_api_pct"] or 0 for e in escalones], label="CPU API (%)", color="#2471a3",
             **marcas)
    ax1.plot(pos, [e["medicion"]["cpu_bd_pct"] or 0 for e in escalones], label="CPU MySQL (%)", color="#27ae60",
             **marcas)
    ax1.plot(pos, [e["medicion"]["cpu_k6_pct"] or 0 for e in escalones], label="CPU k6 (%)", color="#95a5a6",
             linestyle="--", marker="s")
    ax1.axhline(100, color="#c0392b", linestyle="--", linewidth=1, label="Límite de 1 CPU (API y MySQL)")
    ax1.set_ylabel("CPU (%; 100 = 1 núcleo)")
    ax1.set_xlabel("Usuarios concurrentes (escalones)")
    ax1.set_xticks(pos, [str(u) for u in usuarios])
    ax1.set_ylim(bottom=0)
    ax2 = ax1.twinx()
    ax2.plot(pos, [e["medicion"]["pool_en_uso_prom"] or 0 for e in escalones], label="Pool en uso (prom.)",
             color="#e67e22", **marcas)
    ax2.plot(pos, [e["medicion"]["pool_en_espera_prom"] or 0 for e in escalones],
             label="Peticiones esperando conexión (prom.)", color="#8e44ad", **marcas)
    ax2.set_ylabel("Conexiones / peticiones")
    ax2.set_ylim(bottom=0)
    lineas = ax1.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    nombres = ax1.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax1.legend(lineas, nombres, loc="upper left", fontsize=8)
    ax1.grid(alpha=0.3)
    ax1.set_title(f"Consumo de recursos por escalón\n(corrida {corrida})", fontsize=12)
    fig.tight_layout()
    fig.savefig(carpeta / "grafica_5_recursos.png")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corrida", required=True)
    p.add_argument("--calentamiento", type=int, default=5, help="usuarios del calentamiento (se descarta)")
    p.add_argument("--descartar", type=int, default=15, help="segundos iniciales de cada escalón que no se analizan")
    p.add_argument("--max-error", type=float, default=1.0, help="criterio 1: tasa de error máxima (%%)")
    p.add_argument("--max-p95", type=float, default=500.0, help="criterio 2: p95 máximo (ms)")
    p.add_argument("--crecimiento", type=float, default=10.0, help="criterio 3: crecimiento mínimo del throughput (%%)")
    criterios = p.parse_args()
    corrida = criterios.corrida

    puntos = muestras_vus(corrida)
    tramos = tramos_constantes(puntos, minimo_s=40)
    if tramos and tramos[0]["usuarios"] == criterios.calentamiento and len(tramos) > 1:
        calentamiento = tramos.pop(0)
        print(f"Calentamiento descartado: {calentamiento['usuarios']} usuarios, "
              f"{hora(calentamiento['inicio'])} → {hora(calentamiento['fin'])}")
    escalones = []
    for numero, tramo in enumerate(tramos, start=1):
        inicio = tramo["inicio"] + criterios.descartar
        print(f"Escalón {numero}: {tramo['usuarios']} usuarios, ventana {hora(inicio)} → {hora(tramo['fin'])}")
        escalones.append({"numero": numero, "usuarios": tramo["usuarios"], "desde": hora(inicio),
                          "hasta": hora(tramo["fin"]), "desde_ts": inicio, "hasta_ts": tramo["fin"],
                          "medicion": medir(inicio, tramo["fin"], corrida)})

    punto = evaluar(escalones, criterios)
    carpeta = Path("/resultados") / corrida
    carpeta.mkdir(parents=True, exist_ok=True)

    resumen = {
        "corrida": corrida,
        "generado": hora(datetime.now().timestamp()),
        "criterios": {"max_error_pct": criterios.max_error, "max_p95_ms": criterios.max_p95,
                      "crecimiento_min_pct": criterios.crecimiento, "segundos_descartados": criterios.descartar},
        "inicio_prueba_ts": puntos[0][0],
        "fin_prueba_ts": puntos[-1][0],
        "linea_base": linea_base(puntos[0][0]),
        "escalones": escalones,
        "punto_saturacion": None if punto is None else {
            "escalon": punto["numero"], "usuarios": punto["usuarios"], "pet_s": punto["medicion"]["pet_s"],
            "p95_ms": punto["medicion"]["p95_ms"], "error_pct": punto["criterios"]["error_pct"]},
    }
    (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(carpeta / "tabla.csv", "w", newline="", encoding="utf-8") as archivo:
        w = csv.writer(archivo)
        w.writerow(["escalon", "usuarios", "pet_s", "p50_ms", "p95_ms", "p99_ms", "error_pct", "error_apm_pct",
                    "error_k6_pct", "cpu_api_pct", "cpu_bd_pct", "cpu_k6_pct", "mem_api_mb", "mem_bd_mb",
                    "conexiones_bd_max", "max_connections", "pool_en_uso_max", "pool_maximo", "pool_en_espera_prom",
                    "ms_total", "ms_aplicacion", "ms_espera_pool", "ms_consultas_bd", "ms_publicacion_cola",
                    "cumple_error", "cumple_p95", "cumple_crecimiento"])
        for e in escalones:
            m, c, t = e["medicion"], e["criterios"], e["medicion"]["tiempo_ms"]
            w.writerow([e["numero"], e["usuarios"], m["pet_s"], m["p50_ms"], m["p95_ms"], m["p99_ms"],
                        c["error_pct"], m["error_apm_pct"], m["error_k6_pct"], m["cpu_api_pct"], m["cpu_bd_pct"],
                        m["cpu_k6_pct"], m["mem_api_mb"], m["mem_bd_mb"], m["conexiones_bd_max"],
                        m["max_connections"], m["pool_en_uso_max"], m["pool_maximo"], m["pool_en_espera_prom"],
                        t["total"], t["aplicacion"], t["espera_pool"], t["consultas_bd"], t["publicacion_cola"],
                        c["cumple_error"], c["cumple_p95"], c["cumple_crecimiento"]])

    tabla = tabla_markdown(escalones, punto)
    (carpeta / "tabla.md").write_text(f"# Resultados de la corrida {corrida}\n\n{tabla}\n", encoding="utf-8")
    graficas(escalones, punto, criterios, carpeta, corrida)

    print()
    print(tabla)
    print()
    for e in escalones:
        t = e["medicion"]["tiempo_ms"]
        if t["total"]:
            print(f"Escalón {e['numero']} ({e['usuarios']} u): {t['total']:.1f} ms/petición = app "
                  f"{t['aplicacion']:.1f} + espera pool {t['espera_pool']:.1f} + MySQL {t['consultas_bd']:.1f} + "
                  f"RabbitMQ {t['publicacion_cola']:.1f} | CPU k6 {f(e['medicion']['cpu_k6_pct'], 0)} % | "
                  f"crecimiento {f(e['criterios']['crecimiento_pct'])} %")
    print()
    if punto:
        m = punto["medicion"]
        print(f"PUNTO DE SATURACIÓN: escalón {punto['numero']} → {punto['usuarios']} usuarios concurrentes, "
              f"{m['pet_s']:.1f} peticiones/s (p95 {m['p95_ms']:.0f} ms, error {punto['criterios']['error_pct']:.2f} %)")
        if punto is escalones[-1]:
            print("ATENCIÓN: el último escalón todavía cumple los criterios; el sistema no saturó. "
                  "Agregue escalones mayores (ver CRITERIOS_SATURACION.md).")
    else:
        print("Ningún escalón cumple los tres criterios (el sistema ya estaba saturado en el primero).")
    print(f"Resultados en resultados/{corrida}/")


if __name__ == "__main__":
    main()
