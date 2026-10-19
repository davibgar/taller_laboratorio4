# Laboratorio 04: monitoreo con APM y prueba de estrés sobre el CRUD

**Fundación Escuela Tecnológica de Neiva “Jesús Oviedo Pérez”**
Optativa IV: Sistemas de Tiempo Real Distribuidos (2026-2)
Docente: Juan Carlos Polania Cortes
Estudiantes: **David Felipe Garcia Ortiz** y **Juan David Claros Cachaya**

Se parte del CRUD de pagos del Laboratorio 3 (API FastAPI + MySQL + RabbitMQ + consumidor) y se le agregan:

1. Un **APM** con **OpenTelemetry + Jaeger + Prometheus + Grafana**, todo en contenedores.
2. Una **prueba de estrés** con **k6**, en contenedor, con el escenario escrito en `carga/escenario.js`.

```
                     ┌──────────── red de docker compose ─────────────────────────────────────┐
  k6 (carga) ──HTTP──▶ API FastAPI ──SQL──▶ MySQL ◀── mysqld-exporter ◀───┐                   │
     │                 │  │  └─publish─▶ RabbitMQ ──▶ consumidor          │                   │
     │                 │  └── trazas + métricas (OTLP) ──▶ OTel Collector │                   │
     │                 │                         │ spanmetrics            │ scrape cada 5 s   │
     │                 │                         ├────────▶ Prometheus ◀──┤                   │
     │                 │                         └─ 20 % ─▶ Jaeger        │                   │
     └── métricas de k6 (remote write) ─────────────────▶ Prometheus ◀── monitor-contenedores │
                                                              │          (docker stats)       │
                                                           Grafana                            │
                     └────────────────────────────────────────────────────────────────────────┘
```

| Pieza | Imagen | Para qué |
|---|---|---|
| `api` | build `./api` | CRUD de pagos instrumentado con OpenTelemetry |
| `consumer` | build `./consumer` | Procesa los pagos de la cola (instrumentación automática) |
| `mysql` | `mysql:8.4` | Base de datos, con volumen `mysql_data` |
| `rabbitmq` | `rabbitmq:4-management-alpine` | Cola de pagos registrados |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.111.0` | Recibe la telemetría; calcula peticiones/s, errores y latencias (spanmetrics) |
| `jaeger` | `jaegertracing/all-in-one:1.62.0` | Trazas individuales y pestaña *Monitor* (panel del servicio) |
| `prometheus` | `prom/prometheus:v2.54.1` | Guarda todas las métricas (APM, MySQL, contenedores, k6) |
| `grafana` | `grafana/grafana:11.2.2` | Tablero del APM, ya configurado |
| `mysqld-exporter` | `prom/mysqld-exporter:v0.15.1` | Conexiones activas y `max_connections` de MySQL |
| `monitor-contenedores` | build `./monitor-contenedores` | CPU y memoria de cada contenedor, como `docker stats` |
| `k6` *(perfil prueba)* | `grafana/k6:0.54.0` | Generador de carga |
| `analisis` *(perfil prueba)* | build `./analisis` | Tabla de resultados, criterios y gráficas |
| `captura` *(perfil prueba)* | build `./evidencias/herramientas_captura` | Capturas de Grafana y Jaeger (Playwright) |

## 1. Requisitos

Sólo **Docker** (Docker Desktop en Windows o Mac, o Docker Engine con el plugin Compose en Linux).
En el equipo no se instala Python, MySQL, k6 ni ninguna librería. Docker Desktop debe tener al menos 4 CPU y 6 GB de memoria.

Puertos que se publican en el equipo (se cambian con la variable indicada):

| Servicio | URL | Variable |
|---|---|---|
| API + Swagger UI | http://localhost:8090/docs | `API_HOST_PORT` |
| **Grafana (tablero del APM)** | **http://localhost:3030** | `GRAFANA_PORT` |
| **Jaeger (trazas y Monitor)** | **http://localhost:16686** | `JAEGER_UI_PORT` |
| Prometheus | http://localhost:9090 | `PROMETHEUS_PORT` |
| Consola de RabbitMQ | http://localhost:15672 (guest / guest) | `RABBIT_UI_PORT` |
| MySQL | localhost:3307 (pagos_user / pagos_pass) | `MYSQL_HOST_PORT` |

## 2. Levantar todo el entorno (un único comando)

Desde la carpeta del proyecto:

```bash
docker compose up -d --build
```

Ese comando construye las imágenes y levanta la aplicación, la base de datos y el APM completo. La primera vez tarda unos minutos porque descarga las imágenes. Para comprobar que todo quedó arriba:

```bash
docker compose ps
```

Todos los servicios deben aparecer `Up` y la API, MySQL y RabbitMQ `(healthy)`. Luego:

- **Grafana:** http://localhost:3030 abre directamente el tablero *APM - CRUD de pagos* (sin usuario ni contraseña).
- **Jaeger:** en http://localhost:16686 → *Search* → servicio `pagos-api` se ven las trazas. *Monitor* → `pagos-api` muestra el panel del servicio.
- **Swagger:** en http://localhost:8090/docs se prueban las cuatro operaciones del CRUD.

Prueba rápida de que el APM recibe datos:

```bash
docker compose exec -T api python - < evidencias/verificar_apm.py
```

## 3. Operaciones del CRUD

| Operación | Método y ruta | Cuerpo |
|---|---|---|
| Crear | `POST /pagos` | `{"referencia": "PAG-1", "valor": 125000, "medio": "pse"}` |
| Listar (últimos 100) | `GET /pagos` | |
| Consultar | `GET /pagos/{id}` | |
| Actualizar *(nueva en el Lab. 4)* | `PUT /pagos/{id}` | `{"referencia": "PAG-1-B", "valor": 130000, "medio": "nequi"}` |

Medios válidos: `transferencia`, `tarjeta`, `efectivo`, `pse` y `nequi`.

## 4. Instrumentación

- **`api/telemetria.py`** configura OpenTelemetry. Envía trazas y métricas por OTLP al collector.
  - **FastAPI** queda instrumentada automáticamente: un span `SERVER` por petición, con su ruta y código HTTP.
  - **aio-pika** también: un span `PRODUCER` por cada publicación en RabbitMQ.
- **`api/app.py`** agrega lo que no tiene instrumentación oficial (aiomysql):
  - `pool.acquire`: el tiempo que la petición **esperó por una conexión libre del pool**.
  - `SELECT/INSERT/UPDATE/COMMIT pagos_db` (spans `CLIENT`): el tiempo **dentro de MySQL**, con la sentencia SQL como atributo.
  - Métricas del pool: `db.pool.en_uso`, `db.pool.abiertas`, `db.pool.maximo` y `db.pool.en_espera`.
- **El consumidor** se instrumenta sin cambiar código, con `opentelemetry-instrument` (pika + PyMySQL).
- **El collector** (`observabilidad/otel-collector.yml`) calcula con el conector **spanmetrics**, sobre el 100 % de los spans, las peticiones por segundo, la tasa de error y el histograma de latencia de cada operación. De ahí salen p50, p95 y p99. A Jaeger envía el 20 % de las trazas completas, suficiente para inspeccionarlas una a una sin que Jaeger se vuelva el cuello de botella.

Así la traza de una petición muestra cuánto tiempo pasó en la aplicación, cuánto esperando conexión y cuánto en MySQL.

## 5. Prueba de estrés

**Criterios de saturación:** están en [`CRITERIOS_SATURACION.md`](CRITERIOS_SATURACION.md) y se declararon el 2026-10-19, antes de medir. Error ≤ 1 %, p95 ≤ 500 ms y throughput que crece al menos 10 % por escalón.

**Escenario** (`carga/escenario.js`):
- **Mezcla:** 50 % consultar, 20 % listar, 20 % crear y 10 % actualizar, es decir 70 % lecturas.
- **Datos aleatorios** en cada petición: referencia, valor, medio e id a consultar o actualizar.
- **Usuarios:** cada usuario virtual espera de 0,5 s a 1,5 s entre peticiones.
- **Escalones:** 1 minuto de calentamiento y luego 10, 25, 50, 100, 200 y 400 usuarios, de 3 minutos cada uno.

### Opción A: todo automático (≈ 50 min)

Desde Git Bash en Windows, o cualquier bash:

```bash
bash evidencias/generar_evidencias.sh
```

El script hace lo siguiente:
1. Reinicia el entorno desde cero.
2. Verifica que todo esté arriba.
3. Registra la ficha del equipo.
4. Mide la línea base en reposo (2 min).
5. Ejecuta la prueba y la analiza.
6. Toma las capturas del APM.
7. Repite el escalón de saturación.

Todo queda en `evidencias/capturas/` y `resultados/`.

### Opción B: paso a paso (PowerShell o bash)

```bash
# 1. Entorno arriba y 2 minutos en reposo (línea base)
docker compose up -d --build

# 2. Prueba de estrés (unos 21 min). Mientras corre, mire el tablero en http://localhost:3030
docker compose run --rm -e CORRIDA=corrida1 k6

# 3. Tabla del punto 8, criterios, punto de saturación y gráficas del punto 9
docker compose run --rm --build analisis --corrida corrida1

# 4. Capturas de Grafana y Jaeger (hágalo enseguida: Jaeger guarda las trazas en memoria)
docker compose run --rm --build captura apm --corrida corrida1

# 5. Repetir el escalón de saturación (por ejemplo 200 y 400 usuarios) para confirmar
docker compose run --rm -e CORRIDA=repeticion -e ESCALONES=200,400 k6
docker compose run --rm analisis --corrida repeticion
```

El escenario acepta estas variables con `-e`:
- `ESCALONES` (por defecto `10,25,50,100,200,400`)
- `DURACION` (por defecto `3m`)
- `PAUSA_MIN` y `PAUSA_MAX` (por defecto `0.5` y `1.5`)
- `CALENTAMIENTO` (`1m` o `0s`)

Para probar otro tamaño de pool: `$env:DB_POOL_MAX=30; docker compose up -d api` en PowerShell, o `DB_POOL_MAX=30 docker compose up -d api` en bash.

### Qué deja el análisis (`resultados/<corrida>/`)

| Archivo | Contenido |
|---|---|
| `tabla.md`, `tabla.csv` | Una fila por escalón: usuarios, peticiones/s, p50, p95, p99, % de error, CPU de la app y de la BD, memoria, conexiones a MySQL y pool, y si cumple cada criterio |
| `resumen.json` | Todos los valores: peticiones/s y p95 por operación, desglose del tiempo, línea base y punto de saturación |
| `grafica_1_usuarios_vs_peticiones.png` | Usuarios frente a peticiones/s |
| `grafica_2_usuarios_vs_p95.png` | Usuarios frente a latencia p95 |
| `grafica_3_usuarios_vs_error.png` | Usuarios frente a tasa de error |
| `grafica_4_desglose_tiempo.png` | Aplicación, espera de pool, MySQL y RabbitMQ por escalón (pregunta 2) |
| `grafica_5_recursos.png` | CPU de la API, MySQL y k6, y uso del pool por escalón (pregunta 4) |
| `k6_resumen.json` | Resumen de k6, como contraste del lado del cliente |

**De dónde sale cada dato:**
- **Latencia y errores:** del APM (spanmetrics).
- **CPU y memoria:** de las estadísticas de los contenedores (`monitor-contenedores`).
- **Conexiones:** de `mysqld-exporter` y de las métricas del pool.
- **Ventana de cada escalón:** se descartan sus primeros 15 s.

### Corridas realizadas (19-oct-2026)

| Corrida | Script | Qué es |
|---|---|---|
| `corrida1` | `evidencias/generar_evidencias.sh` | Los 6 escalones declarados (10 a 400). El de 400 todavía cumplía los criterios |
| `corrida2` | `evidencias/corrida_ampliada.sh` | 8 escalones (10 a 800), como estaba previsto en la declaración. **Es la corrida principal** |
| `repeticion`, `repeticion2` | los dos anteriores | Repetición del escalón de saturación justo después de cada corrida |
| `confirmacion` | `evidencias/confirmacion_limpia.sh` | 200 y 400 usuarios partiendo de datos limpios |
| `pool30_limpio` | `evidencias/experimento_pool.sh` | 400 y 600 usuarios con el pool de la API en 30 conexiones |

**Resultado:** el punto de saturación es **400 usuarios concurrentes con ≈378 peticiones/s** (p95 247 ms, 0 % de error).
- Se midió en dos corridas independientes: 376,4 y 377,7 pet./s.
- El throughput se aplana en ≈427 pet./s.
- El límite lo impone la **CPU de la API**: un proceso de uvicorn con 1 CPU.
- El pool de 10 conexiones es lo primero que se llena, pero subirlo a 30 no eleva el techo.
- Con el equipo bajo carga sostenida el margen se estrecha. El límite que se cumplió en todas las mediciones es de 200 usuarios y ≈185 a 195 pet./s.

El detalle está en `Informe_Laboratorio04.docx` / `.pdf`.

## 6. Estructura

```
api/                  CRUD instrumentado (app.py, telemetria.py)
consumer/             consumidor del Lab. 3 con instrumentación automática
db/init.sql           tablas + usuario de lectura para mysqld-exporter
observabilidad/       configuración del collector, Prometheus y Grafana (tablero JSON)
monitor-contenedores/ exportador de docker stats a Prometheus
carga/escenario.js    escenario de k6 (versionado)
analisis/             análisis de la corrida: tabla, criterios y gráficas
evidencias/           script de evidencias, verificación y capturas
resultados/           salidas de cada corrida
CRITERIOS_SATURACION.md  declaración previa de los criterios
```

## 7. Apagar y limpiar

```bash
docker compose down        # detiene todo y conserva datos y métricas
docker compose down -v     # además borra los volúmenes (MySQL, RabbitMQ, Prometheus)
```
