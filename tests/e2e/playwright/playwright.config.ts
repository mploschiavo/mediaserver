import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: 'api',
      testMatch: ['ux-smoke.spec.ts', 'ingress.spec.ts'],
    },
    {
      name: 'browser',
      testMatch: ['app-navigation.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'screenshots',
      testMatch: ['screenshot-capture.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
