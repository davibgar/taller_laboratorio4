/*
 * Escenario de la prueba de estrés - Taller de Laboratorio N.º 4
 * Autores: David Felipe Garcia Ortiz - Juan David Claros Cachaya
 *
 * Generador de carga: k6 (en contenedor). Se ejecuta con:
 *     docker compose run --rm -e CORRIDA=corrida1 k6
 *
 * MEZCLA DE OPERACIONES (declarada antes de medir, ver CRITERIOS_SATURACION.md)
 *     50 %  consultar un pago     GET  /pagos/{id}   (lectura)
 *     20 %  listar pagos          GET  /pagos        (lectura)
 *     20 %  crear un pago         POST /pagos        (escritura)
 *     10 %  actualizar un pago    PUT  /pagos/{id}   (escritura)
 *     => 70 % lecturas y 30 % escrituras.
 *
 * DATOS VARIABLES: cada creación y actualización envía una referencia, un valor y un
 * medio de pago aleatorios, y las consultas y actualizaciones eligen un id al azar entre
 * todos los pagos existentes. Así no se mide sólo el efecto de una caché.
 *
 * ESCALONES: 1 min de calentamiento (descartado) y luego un escalón por cada valor de
 * ESCALONES, de DURACION cada uno, con una rampa de RAMPA entre escalones.
 * Cada usuario virtual espera entre PAUSA_MIN y PAUSA_MAX segundos entre peticiones.
 */
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = __ENV.BASE_URL || 'http://api:8000';
const CORRIDA = __ENV.CORRIDA || 'corrida';
const ESCALONES = (__ENV.ESCALONES || '10,25,50,100,200,400').split(',').map(Number);
const DURACION = __ENV.DURACION || '3m';
const RAMPA = __ENV.RAMPA || '10s';
const CALENTAMIENTO = __ENV.CALENTAMIENTO || '1m';
const USUARIOS_CALENTAMIENTO = Number(__ENV.USUARIOS_CALENTAMIENTO || 5);
const PAUSA_MIN = Number(__ENV.PAUSA_MIN || 0.5);
const PAUSA_MAX = Number(__ENV.PAUSA_MAX || 1.5);
const PAGOS_INICIALES = Number(__ENV.PAGOS_INICIALES || 300);

const MEZCLA = [
  { operacion: 'consultar', peso: 50 },
  { operacion: 'listar', peso: 20 },
  { operacion: 'crear', peso: 20 },
  { operacion: 'actualizar', peso: 10 },
];
const MEDIOS = ['transferencia', 'tarjeta', 'efectivo', 'pse', 'nequi'];

// Calentamiento: rampa corta y el resto del minuto con pocos usuarios.
const etapas = [{ duration: '10s', target: USUARIOS_CALENTAMIENTO }, { duration: '50s', target: USUARIOS_CALENTAMIENTO }];
if (CALENTAMIENTO === '0s') etapas.length = 0;
for (const usuarios of ESCALONES) {
  etapas.push({ duration: RAMPA, target: usuarios });
  etapas.push({ duration: DURACION, target: usuarios });
}

export const options = {
  // La etiqueta "corrida" acompaña a todas las métricas que k6 envía a Prometheus;
  // el análisis la usa para encontrar los escalones de esta corrida.
  tags: { corrida: CORRIDA },
  scenarios: {
    escalones: { executor: 'ramping-vus', startVUs: 0, stages: etapas, gracefulRampDown: '10s', gracefulStop: '10s' },
  },
  // Sin umbrales que aborten la prueba: el objetivo es llevar el sistema a la saturación.
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: false,
};

const PARAMS = { headers: { 'Content-Type': 'application/json' }, timeout: '10s' };

function aleatorio(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

let contador = 0;
function datosPago() {
  return JSON.stringify({
    referencia: `K6-${CORRIDA}-${__VU}-${++contador}-${aleatorio(1000, 9999)}`,
    valor: aleatorio(1000, 5_000_000) + aleatorio(0, 99) / 100,
    medio: MEDIOS[aleatorio(0, MEDIOS.length - 1)],
  });
}

function elegirOperacion() {
  let tiro = Math.random() * 100;
  for (const { operacion, peso } of MEZCLA) {
    if ((tiro -= peso) < 0) return operacion;
  }
  return MEZCLA[0].operacion;
}

// Se ejecuta una vez antes de la prueba: garantiza que existan pagos para consultar y
// actualizar, y averigua el id más alto.
export function setup() {
  for (let i = 0; i < PAGOS_INICIALES; i++) {
    http.post(`${BASE}/pagos`, datosPago(), PARAMS);
  }
  const lista = http.get(`${BASE}/pagos`, PARAMS).json('data');
  const idMaximo = lista && lista.length ? lista[0].id : 1;
  console.log(`Corrida ${CORRIDA}: escalones ${ESCALONES.join(', ')} usuarios de ${DURACION}; ids 1..${idMaximo}`);
  return { idMaximo };
}

// Ids creados por este usuario virtual durante la prueba (también se consultan).
let idMaximoLocal = 0;

export default function (datos) {
  const idMaximo = Math.max(datos.idMaximo, idMaximoLocal);
  const id = aleatorio(1, idMaximo);
  const operacion = elegirOperacion();
  let r;

  switch (operacion) {
    case 'consultar':
      r = http.get(`${BASE}/pagos/${id}`, { ...PARAMS, tags: { name: 'GET /pagos/{id}', operacion } });
      check(r, { 'consultar: 200': (x) => x.status === 200 });
      break;
    case 'listar':
      r = http.get(`${BASE}/pagos`, { ...PARAMS, tags: { name: 'GET /pagos', operacion } });
      check(r, { 'listar: 200': (x) => x.status === 200 });
      break;
    case 'crear':
      r = http.post(`${BASE}/pagos`, datosPago(), { ...PARAMS, tags: { name: 'POST /pagos', operacion } });
      if (check(r, { 'crear: 201': (x) => x.status === 201 })) {
        idMaximoLocal = Math.max(idMaximoLocal, r.json('data.id'));
      }
      break;
    case 'actualizar':
      r = http.put(`${BASE}/pagos/${id}`, datosPago(), { ...PARAMS, tags: { name: 'PUT /pagos/{id}', operacion } });
      check(r, { 'actualizar: 200': (x) => x.status === 200 });
      break;
  }

  sleep(PAUSA_MIN + Math.random() * (PAUSA_MAX - PAUSA_MIN));
}
