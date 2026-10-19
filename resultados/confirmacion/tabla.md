# Resultados de la corrida confirmacion

| Esc. | Usuarios | Pet./s | p50 (ms) | p95 (ms) | p99 (ms) | Error % | CPU app | CPU BD | Memoria app / BD (MB) | Conex. BD (activas / máx.) | Pool app (en uso / tamaño; en espera) | Cumple |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 185.4 | 19 | 312 | 943 | 0.00 | 74 % | 24 % | 60 / 471 | 12 / 151 | 10 / 10; 5 prom. (65 máx.) | ✅ **← punto de saturación** |
| 2 | 400 | 157.7 | 405 | 7 239 | 16 824 | 2.97 | 105 % | 43 % | 69 / 477 | 12 / 151 | 10 / 10; 250 prom. (428 máx.) | ❌ error, p95, throughput |
