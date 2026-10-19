# Resultados de la corrida repeticion2

| Esc. | Usuarios | Pet./s | p50 (ms) | p95 (ms) | p99 (ms) | Error % | CPU app | CPU BD | Memoria app / BD (MB) | Conex. BD (activas / máx.) | Pool app (en uso / tamaño; en espera) | Cumple |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 400 | 316.8 | 63 | 873 | 1 486 | 0.00 | 96 % | 29 % | 84 / 504 | 12 / 151 | 10 / 10; 101 prom. (235 máx.) | ❌ p95 |
| 2 | 600 | 341.9 | 522 | 2 546 | 4 400 | 0.00 | 100 % | 27 % | 87 / 505 | 12 / 151 | 10 / 10; 277 prom. (434 máx.) | ❌ p95, throughput |
