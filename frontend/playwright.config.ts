import { defineConfig } from '@playwright/test'

const port = Number(process.env.BIONIC_CLAW_E2E_PORT ?? '5173')
const memoriesRoot = `/tmp/bionic-claw-e2e-${process.pid}`

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
      `cd .. && BIONIC_CLAW_E2E_PORT=${port} BIONIC_CLAW_MODEL_CONFIG=mock BIONIC_CLAW_MOCK_MODEL_DELAY_MS=800 BIONIC_CLAW_MEMORIES_ROOT=${memoriesRoot} ./dev.sh`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
