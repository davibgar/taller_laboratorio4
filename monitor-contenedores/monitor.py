"""
Monitor de contenedores - Taller de Laboratorio N.º 4
Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya

Publica en formato Prometheus (http://monitor-contenedores:9200/metrics) las mismas
cifras que muestra `docker stats` para cada contenedor del proyecto:

  contenedor_cpu_porcentaje        CPU usada; 100 = un núcleo completo (igual que docker stats)
  contenedor_cpu_limite_nucleos    límite de CPU asignado en docker-compose.yml
  contenedor_memoria_bytes         memoria usada (sin la caché de archivos, igual que docker stats)
  contenedor_memoria_limite_bytes  límite de memoria asignado

Lee la API de Docker por el socket /var/run/docker.sock (montado sólo lectura).
Prometheus lo consulta cada 5 s, así las estadísticas de los contenedores quedan
alineadas en el tiempo con las del APM.
"""
import logging
import os
import threading
import time

import docker
from prometheus_client import Gauge, start_http_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MONITOR] %(message)s")
log = logging.getLogger("monitor")

PROYECTO = os.getenv("PROYECTO", "lab04-pagos")
PUERTO = int(os.getenv("PUERTO", "9200"))
ETIQUETAS = ["contenedor", "servicio"]

CPU = Gauge("contenedor_cpu_porcentaje", "CPU usada (100 = un núcleo)", ETIQUETAS)
CPU_LIMITE = Gauge("contenedor_cpu_limite_nucleos", "Límite de CPU del contenedor (núcleos)", ETIQUETAS)
MEMORIA = Gauge("contenedor_memoria_bytes", "Memoria usada sin caché de archivos", ETIQUETAS)
MEMORIA_LIMITE = Gauge("contenedor_memoria_limite_bytes", "Límite de memoria del contenedor", ETIQUETAS)
METRICAS = (CPU, CPU_LIMITE, MEMORIA, MEMORIA_LIMITE)

cliente = docker.from_env()
vigilados = set()
candado = threading.Lock()


def cpu_porcentaje(actual, previo):
    """Mismo cálculo que el cliente de docker stats."""
    delta_cpu = actual["cpu_usage"]["total_usage"] - previo["cpu_usage"]["total_usage"]
    delta_sistema = actual.get("system_cpu_usage", 0) - previo.get("system_cpu_usage", 0)
    nucleos = actual.get("online_cpus") or len(actual["cpu_usage"].get("percpu_usage") or [1])
    if delta_sistema <= 0 or delta_cpu < 0:
        return 0.0
    return delta_cpu / delta_sistema * nucleos * 100.0


def memoria_usada(memoria):
    datos = memoria.get("stats", {})
    cache = datos.get("inactive_file", datos.get("total_inactive_file", 0))  # cgroup v2 / v1
    return max(memoria.get("usage", 0) - cache, 0)


def vigilar(contenedor):
    nombre = contenedor.name
    servicio = contenedor.labels.get("com.docker.compose.service", nombre)
    etiquetas = (nombre, servicio)
    nano_cpus = contenedor.attrs["HostConfig"].get("NanoCpus") or 0
    CPU_LIMITE.labels(*etiquetas).set(nano_cpus / 1e9)
    log.info("Vigilando %s (servicio %s, límite %.2f CPU)", nombre, servicio, nano_cpus / 1e9)
    try:
        # El flujo de docker entrega una muestra por segundo con la anterior incluida (precpu_stats).
        for muestra in contenedor.stats(stream=True, decode=True):
            if not muestra.get("cpu_stats", {}).get("cpu_usage"):
                continue
            if muestra.get("precpu_stats", {}).get("cpu_usage"):
                CPU.labels(*etiquetas).set(cpu_porcentaje(muestra["cpu_stats"], muestra["precpu_stats"]))
            memoria = muestra.get("memory_stats", {})
            MEMORIA.labels(*etiquetas).set(memoria_usada(memoria))
            if memoria.get("limit"):
                MEMORIA_LIMITE.labels(*etiquetas).set(memoria["limit"])
    except Exception as exc:  # noqa: BLE001
        log.warning("Se dejó de vigilar %s: %s", nombre, exc)
    finally:
        # El contenedor terminó (p. ej. el generador de carga): sus series desaparecen.
        for metrica in METRICAS:
            try:
                metrica.remove(*etiquetas)
            except KeyError:
                pass
        with candado:
            vigilados.discard(contenedor.id)


def descubrir():
    """Cada 5 s busca contenedores nuevos del proyecto (el generador de carga aparece después)."""
    while True:
        try:
            for contenedor in cliente.containers.list(filters={"label": f"com.docker.compose.project={PROYECTO}"}):
                with candado:
                    if contenedor.id in vigilados:
                        continue
                    vigilados.add(contenedor.id)
                threading.Thread(target=vigilar, args=(contenedor,), daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            log.warning("No fue posible listar los contenedores: %s", exc)
        time.sleep(5)


if __name__ == "__main__":
    start_http_server(PUERTO)
    log.info("Métricas en :%d/metrics para el proyecto %s", PUERTO, PROYECTO)
    descubrir()
