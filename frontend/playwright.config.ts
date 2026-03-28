import { defineConfig } from '@playwright/test'

const port = Number(process.env.PROJECT_X_E2E_PORT ?? '5173')
const memoriesRoot = `/tmp/project-x-e2e-${process.pid}`

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
  },
  webServer: {
    command:
      `cd .. && PROJECT_X_E2E_PORT=${port} PROJECT_X_MODEL_CONFIG=mock PROJECT_X_MOCK_MODEL_DELAY_MS=800 PROJECT_X_MEMORIES_ROOT=${memoriesRoot} ./dev.sh`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
