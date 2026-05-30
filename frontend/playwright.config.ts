import { defineConfig } from '@playwright/test'

const port = Number(process.env.PROJECT_X_E2E_PORT ?? '5173')
const backendPort = Number(process.env.PROJECT_X_E2E_BACKEND_PORT ?? String(18_000 + (process.pid % 1000)))
const baseRoot = `/tmp/project-x-e2e-${process.pid}`
const memoriesRoot = `${baseRoot}/memories`
const modelConfig = process.env.PROJECT_X_MODEL_CONFIG ?? 'openai-codex'

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
      `cd .. && rm -rf ${baseRoot} && mkdir -p ${baseRoot} && PROJECT_X_E2E_PORT=${port} PROJECT_X_PORT=${backendPort} PROJECT_X_BACKEND_ORIGIN=http://127.0.0.1:${backendPort} PROJECT_X_ROOT=${baseRoot} PROJECT_X_MODEL_CONFIG=${modelConfig} PROJECT_X_MEMORIES_ROOT=${memoriesRoot} ./dev.sh`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
