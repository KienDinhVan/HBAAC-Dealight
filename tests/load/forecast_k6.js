// Sprint 6 load test for the SKU forecast API.
//
// Two scenarios mirror the acceptance targets in
// sku_demand_forecasting_sprint_plan.md §6.3:
//   - warmup     : 10 VUs for 30 s, p95 latency < 500 ms
//   - sustained  : 50 VUs for 60 s, p95 latency < 1000 ms
//
// Across both scenarios the global error rate must stay below 1 %.
//
// Run locally (inside docker-compose network):
//   make load-test
// or directly:
//   docker run --rm --network=host \
//     -v "$(pwd)/tests/load:/scripts" grafana/k6 run /scripts/forecast_k6.js

import http from 'k6/http';
import { check, group } from 'k6';
import { Rate } from 'k6/metrics';

const BASE = __ENV.API_URL || 'http://localhost:8000';
const SKU_COUNT = Number(__ENV.SKU_COUNT || 15972);
const TARGET_DATES = [
  '2025-09-06',
  '2025-09-15',
  '2025-09-30',
  '2025-10-15',
  '2025-10-31',
];

export const errorRate = new Rate('forecast_errors');

export const options = {
  discardResponseBodies: false,
  scenarios: {
    warmup: {
      executor: 'constant-vus',
      vus: 10,
      duration: '30s',
      tags: { scenario: 'warmup' },
      exec: 'forecastWorkflow',
    },
    sustained: {
      executor: 'constant-vus',
      vus: 50,
      duration: '60s',
      startTime: '35s',
      tags: { scenario: 'sustained' },
      exec: 'forecastWorkflow',
    },
  },
  thresholds: {
    // Plan §6: 10 VUs p95 < 500 ms, 50 VUs p95 < 1000 ms.
    'http_req_duration{scenario:warmup}': ['p(95)<500'],
    'http_req_duration{scenario:sustained}': ['p(95)<1000'],
    // Baseline (warmup) targets from §6.3.
    'http_req_duration{endpoint:health,scenario:warmup}': ['p(95)<100'],
    'http_req_duration{endpoint:forecast_sku,scenario:warmup}': ['p(95)<500'],
    // Global error budget across the whole test.
    'http_req_failed': ['rate<0.01'],
    'forecast_errors': ['rate<0.01'],
  },
};

function randomSku() {
  const n = 1 + Math.floor(Math.random() * SKU_COUNT);
  return `SKU-${String(n).padStart(5, '0')}`;
}

function randomTargetDate() {
  return TARGET_DATES[Math.floor(Math.random() * TARGET_DATES.length)];
}

function record(response, label) {
  const ok = check(
    response,
    {
      [`${label} status is 2xx`]: (r) => r.status >= 200 && r.status < 300,
    },
    { endpoint: label },
  );
  errorRate.add(!ok);
}

export function forecastWorkflow() {
  group('health', () => {
    const r = http.get(`${BASE}/health`, { tags: { endpoint: 'health' } });
    record(r, 'health');
  });

  group('forecast_sku', () => {
    const sku = randomSku();
    const r = http.get(`${BASE}/forecast/${sku}?days=7`, {
      tags: { endpoint: 'forecast_sku' },
    });
    // 404 is acceptable for SKUs outside the universe; do not mark as error.
    const ok = r.status === 200 || r.status === 404;
    check(r, { 'forecast_sku status is 200 or 404': () => ok });
    errorRate.add(!ok);
  });

  group('top_skus', () => {
    const target = randomTargetDate();
    const r = http.get(
      `${BASE}/forecast/top-skus?target_date=${target}&limit=20`,
      { tags: { endpoint: 'top_skus' } },
    );
    record(r, 'top_skus');
  });

  group('summary', () => {
    const target = randomTargetDate();
    const r = http.get(`${BASE}/forecast/summary?target_date=${target}`, {
      tags: { endpoint: 'summary' },
    });
    record(r, 'summary');
  });
}
