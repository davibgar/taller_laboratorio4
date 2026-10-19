# Declaración de los criterios de saturación

**Taller de Laboratorio N.º 4 – Monitoreo con APM y prueba de estrés sobre el CRUD**
Optativa IV – Sistemas de Tiempo Real Distribuidos (2026-2) · Docente: Juan Carlos Polania Cortes
Estudiantes: **David Felipe Garcia Ortiz** y **Juan David Claros Cachaya**

**Fecha de definición: 2026-10-19**, antes de ejecutar la primera medición de la prueba de estrés.
Este archivo queda versionado en git; el historial de commits muestra que no se modificó después de medir.

## Criterios

Un escalón **cumple** si satisface los tres criterios a la vez. El **punto de saturación** es el último
escalón que los cumple. Desde el primero que falle en adelante se considera que el sistema saturó.

| # | Criterio | Umbral declarado | Fuente del dato | Qué significa |
|---|---|---|---|---|
| 1 | Tasa de error | **≤ 1 %** de las peticiones del escalón | APM (spans de servidor con estado `ERROR`, respuestas 5xx). Se contrasta con `http_req_failed` de k6, que también cuenta los *timeouts* que el servidor nunca registra; **se toma el mayor de los dos** | Por encima de ese umbral el sistema ya está rechazando trabajo |
| 2 | Latencia p95 | **≤ 500 ms** (todas las operaciones juntas) | APM (histograma de duración de los spans de servidor) | Por encima, la experiencia del usuario se considera inaceptable |
| 3 | Crecimiento del throughput | El throughput (peticiones/s atendidas) del escalón es **al menos 10 % mayor** que el del escalón anterior. El escalón 1 cumple por definición | APM (peticiones/s de los spans de servidor) | Si al subir usuarios el throughput deja de crecer (se aplana), el sistema ya saturó |

## Condiciones de la medición (fijadas también antes de medir)

- **Escalones:** 10, 25, 50, 100, 200 y 400 usuarios concurrentes; 3 minutos cada uno. Antes de ellos,
  1 minuto de calentamiento con 5 usuarios, que se descarta del análisis.
  Entre escalones hay una rampa de 10 s, que tampoco se analiza.
- **Ventana de análisis:** de cada escalón se descartan los primeros 15 s, mientras el sistema se estabiliza.
  Se analizan los 165 s restantes.
- **Usuario virtual:** después de cada petición espera entre 0,5 s y 1,5 s (tiempo de pensar, 1 s en promedio).
  Así el número de usuarios concurrentes representa usuarios reales y no un bucle sin pausa.
- **Mezcla de operaciones:** 50 % consultar un pago, 20 % listar pagos, 20 % crear un pago y 10 % actualizar un pago,
  es decir, 70 % lecturas y 30 % escrituras.
- **Límites de recursos de los contenedores:** API 1 CPU / 512 MB, MySQL 1 CPU / 1 GB y generador de carga 2 CPU / 1 GB.
  El resto están en `docker-compose.yml`.
- Si en el escalón de 400 usuarios no aparece la saturación, se agregan escalones de 600 y 800 usuarios
  **sin cambiar los criterios**.
