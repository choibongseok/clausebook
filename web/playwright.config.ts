import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  reporter: [['list'], ['json', { outputFile: '../build/web.json' }]],
  use: { baseURL: 'http://127.0.0.1:4373/clausebook/', channel: process.env.PW_CHANNEL === undefined ? 'chrome' : process.env.PW_CHANNEL || undefined, trace: 'off' },
  webServer: { command: 'node serve.mjs', url: 'http://127.0.0.1:4373/clausebook/', reuseExistingServer: false, timeout: 20_000 },
});
