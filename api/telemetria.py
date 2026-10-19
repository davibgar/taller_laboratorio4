"""
Instrumentación OpenTelemetry de la API - Taller de Laboratorio N.º 4
Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya

La API envía trazas y métricas por OTLP (HTTP) al OpenTelemetry Collector.
Toda la configuración llega por variables de entorno estándar de OpenTelemetry
(definidas en docker-compose.yml):
  OTEL_SERVICE_NAME            nombre del servicio en el APM (pagos-api)
  OTEL_EXPORTER_OTLP_ENDPOINT  http://otel-collector:4318
  OTEL_BSP_*                   tamaño de la cola y de los lotes de spans

Qué se instrumenta:
  - FastAPI (automático): un span SERVER por petición, con ruta, método y código HTTP.
  - aio-pika (automático): un span PRODUCER por cada publicación en RabbitMQ.
  - MySQL (manual, en app.py): aiomysql no tiene instrumentación oficial, así que cada
    sentencia SQL y cada espera por una conexión del pool abre su propio span.
"""
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

INTERVALO_METRICAS_MS = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "5000"))


def iniciar():
    """Configura los proveedores de trazas y métricas. Se llama antes de crear la app."""
    recurso = Resource.create()  # toma OTEL_SERVICE_NAME y OTEL_RESOURCE_ATTRIBUTES
    proveedor = TracerProvider(resource=recurso)
    # El procesador por lotes lee OTEL_BSP_MAX_QUEUE_SIZE, OTEL_BSP_SCHEDULE_DELAY, etc.
    proveedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(proveedor)

    lector = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=INTERVALO_METRICAS_MS)
    metrics.set_meter_provider(MeterProvider(resource=recurso, metric_readers=[lector]))

    AioPikaInstrumentor().instrument()


def instrumentar_app(app):
    # /health no se traza; los spans internos "http receive/send" de ASGI sólo agregan ruido.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health", exclude_spans=["receive", "send"])
