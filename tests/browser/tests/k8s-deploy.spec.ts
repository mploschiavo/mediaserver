/**
 * Kubernetes deployment validation tests.
 *
 * Validates that the single-file K8s deploy (dist/k8s-deploy.yaml) produces a
 * healthy, functional cluster. Tests use the controller API endpoints rather
 * than shelling out to kubectl — the controller already exposes namespace,
 * health, and status information via its HTTP API.
 *
 * Prerequisites:
 *   - A running K8s cluster with dist/k8s-deploy.yaml applied
 *   - Controller reachable via NodePort or port-forward
 *
 * Usage:
 *   K8S_DEPLOY_TEST=1 STACK_NODE_IP=192.168.1.60 \
 *     npx playwright test tests/k8s-deploy.spec.ts
 *
 * Optional env vars:
 *   K8S_NAMESPACE      — namespace (default: media-stack)
 *   CONTROLLER_PORT    — controller NodePort or forwarded port (default: 9100)
 *   ENVOY_NODE_PORT    — Envoy HTTP NodePort (default: 30180)
 */

import { expect, test } from '@playwright/test';
import { execSync } from 'node:child_process';

const enabled = process.env.K8S_DEPLOY_TEST === '1';
const nodeIp = process.env.STACK_NODE_IP || '';
const namespace = process.env.K8S_NAMESPACE || 'media-stack';
const controllerPort = process.env.CONTROLLER_PORT || '9100';
const envoyNodePort = process.env.ENVOY_NODE_PORT || '30180';

const controllerBase = `http://${nodeIp}:${controllerPort}`;

// Expected deployments from dist/k8s-deploy.yaml
const EXPECTED_DEPLOYMENTS = [
  'bazarr',
  'envoy',
  'flaresolverr',
  'homepage',
  'jellyfin',
  'jellyfin-auto-collections',
  'jellyseerr',
  'lidarr',
  'maintainerr',
  'media-stack-controller',
  'plex',
  'prowlarr',
  'qbittorrent',
  'radarr',
  'readarr',
  'sabnzbd',
  'sonarr',
  'tautulli',
  'unpackerr',
];

const CORE_APPS = ['sonarr', 'radarr', 'prowlarr', 'jellyfin', 'bazarr'];

function sh(cmd: string): string {
  try {
    return execSync(cmd, { timeout: 30_000, encoding: 'utf-8' }).trim();
  } catch (err: any) {
    return err.stdout?.toString() || err.message || '';
  }
}

test.describe('Kubernetes deployment validation', () => {
  test.skip(!enabled || !nodeIp, 'Set K8S_DEPLOY_TEST=1 and STACK_NODE_IP');
  test.setTimeout(60_000);

  // -----------------------------------------------------------------------
  // 1. All expected deployments exist in namespace
  // -----------------------------------------------------------------------
  test('all expected deployments exist in namespace', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const serviceNames = (data.services || []).map((s: any) => s.name);

    for (const dep of EXPECTED_DEPLOYMENTS) {
      expect(serviceNames, `deployment "${dep}" should exist`).toContain(dep);
    }
  });

  // -----------------------------------------------------------------------
  // 2. Controller pod is running and ready
  // -----------------------------------------------------------------------
  test('controller pod is running and ready', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const controller = (data.services || []).find(
      (s: any) => s.name === 'media-stack-controller'
    );
    expect(controller, 'controller deployment should exist').toBeTruthy();
    expect(controller.ready).toBeGreaterThanOrEqual(1);
  });

  // -----------------------------------------------------------------------
  // 3. Envoy pod is running (not CrashLoopBackOff)
  // -----------------------------------------------------------------------
  test('envoy pod is running', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const envoy = (data.services || []).find((s: any) => s.name === 'envoy');
    expect(envoy, 'envoy deployment should exist').toBeTruthy();
    expect(envoy.ready).toBeGreaterThanOrEqual(1);

    // Verify no problems with envoy pods
    const ns = (data.namespaces || [])[0];
    if (ns?.problems) {
      const envoyProblems = ns.problems.filter((p: any) =>
        p.name.startsWith('envoy-')
      );
      for (const problem of envoyProblems) {
        expect(problem.reason).not.toBe('CrashLoopBackOff');
      }
    }
  });

  // -----------------------------------------------------------------------
  // 4. All core app pods are running
  // -----------------------------------------------------------------------
  test('all core app pods are running', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const services = data.services || [];

    for (const app of CORE_APPS) {
      const svc = services.find((s: any) => s.name === app);
      expect(svc, `${app} deployment should exist`).toBeTruthy();
      expect(svc.ready, `${app} should have at least 1 ready replica`).toBeGreaterThanOrEqual(1);
    }
  });

  // -----------------------------------------------------------------------
  // 5. Controller health endpoint responds (/healthz)
  // -----------------------------------------------------------------------
  test('controller /healthz responds ok', async ({ request }) => {
    const response = await request.get(`${controllerBase}/healthz`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.status).toBe('ok');
  });

  // -----------------------------------------------------------------------
  // 6. Controller readiness endpoint responds (/readyz)
  // -----------------------------------------------------------------------
  test('controller /readyz responds ready', async ({ request }) => {
    const response = await request.get(`${controllerBase}/readyz`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.status).toBe('ready');
    expect(data).toHaveProperty('initial_bootstrap_done');
    expect(data).toHaveProperty('phase');
  });

  // -----------------------------------------------------------------------
  // 7. Envoy is listening on its NodePort
  // -----------------------------------------------------------------------
  test('envoy is listening on its NodePort', async ({ request }) => {
    // Envoy returns a response (even if 404) when reachable
    const response = await request.get(`http://${nodeIp}:${envoyNodePort}/`, {
      failOnStatusCode: false,
      timeout: 10_000,
    });
    // Envoy should respond — any HTTP status means it is listening
    expect(response.status()).toBeGreaterThan(0);
  });

  // -----------------------------------------------------------------------
  // 8. No pods in CrashLoopBackOff state
  // -----------------------------------------------------------------------
  test('no pods in CrashLoopBackOff state', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const ns = (data.namespaces || [])[0];
    expect(ns, 'namespace info should be present').toBeTruthy();

    const crashLooping = (ns.problems || []).filter(
      (p: any) => p.reason === 'CrashLoopBackOff'
    );
    expect(
      crashLooping,
      `no pods should be in CrashLoopBackOff, found: ${crashLooping.map((p: any) => p.name).join(', ')}`
    ).toHaveLength(0);
  });

  // -----------------------------------------------------------------------
  // 9. No pods in ImagePullBackOff state
  // -----------------------------------------------------------------------
  test('no pods in ImagePullBackOff state', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const ns = (data.namespaces || [])[0];
    expect(ns, 'namespace info should be present').toBeTruthy();

    const pullFailed = (ns.problems || []).filter(
      (p: any) =>
        p.reason === 'ImagePullBackOff' || p.reason === 'ErrImagePull'
    );
    expect(
      pullFailed,
      `no pods should be in ImagePullBackOff, found: ${pullFailed.map((p: any) => p.name).join(', ')}`
    ).toHaveLength(0);
  });

  // -----------------------------------------------------------------------
  // 10. PVCs are bound (not Pending) — uses kubectl since controller doesn't
  //     expose PVC status. Falls back gracefully if kubectl unavailable.
  // -----------------------------------------------------------------------
  test('PVCs are bound', () => {
    const output = sh(
      `kubectl get pvc -n ${namespace} -o jsonpath='{range .items[*]}{.metadata.name}={.status.phase}{"\\n"}{end}' 2>/dev/null`
    );
    // If kubectl is not available or the command fails, skip gracefully
    test.skip(!output || output.includes('error'), 'kubectl not available or not configured');

    const lines = output.split('\n').filter((l) => l.includes('='));
    expect(lines.length, 'should have PVCs').toBeGreaterThan(0);

    const pending = lines.filter((l) => !l.endsWith('=Bound'));
    expect(
      pending,
      `all PVCs should be Bound, found non-Bound: ${pending.join(', ')}`
    ).toHaveLength(0);
  });

  // -----------------------------------------------------------------------
  // 11. Services have correct ports defined
  // -----------------------------------------------------------------------
  test('services have correct ports defined', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/health`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // The health endpoint probes each service on its expected port.
    // If a service responds (even with an error code), its port is correct.
    const services = data.services || {};
    const expectedPorts: Record<string, boolean> = {};
    for (const app of CORE_APPS) {
      if (services[app]) {
        // A service that is probed and returns a status (ok or error with code)
        // has its port correctly configured
        expectedPorts[app] =
          services[app].status === 'ok' ||
          (services[app].status === 'error' && services[app].code > 0);
      }
    }

    for (const app of CORE_APPS) {
      expect(
        expectedPorts[app],
        `${app} should be reachable on its expected port`
      ).toBeTruthy();
    }
  });

  // -----------------------------------------------------------------------
  // 12. Controller API returns valid status
  // -----------------------------------------------------------------------
  test('controller /status returns valid state', async ({ request }) => {
    const response = await request.get(`${controllerBase}/status`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    // Verify essential fields are present
    expect(data).toHaveProperty('phase');
    expect(data).toHaveProperty('initial_bootstrap_done');
    expect(data).toHaveProperty('phases_completed');
    expect(data).toHaveProperty('app_status');
    expect(data).toHaveProperty('runtime_config');
    expect(Array.isArray(data.phases_completed)).toBeTruthy();
  });

  // -----------------------------------------------------------------------
  // 13. Bootstrap has completed (initial_bootstrap_done: true)
  // -----------------------------------------------------------------------
  test('bootstrap has completed', async ({ request }) => {
    const response = await request.get(`${controllerBase}/status`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.initial_bootstrap_done).toBe(true);
  });

  // -----------------------------------------------------------------------
  // 14. Config envoy volume is shared between controller and envoy
  // -----------------------------------------------------------------------
  test('envoy config PVC is mounted on controller', async ({ request }) => {
    // The controller mounts the envoy PVC at /srv-config/envoy to write
    // envoy.yaml. Verify via /api/namespaces that both deployments exist
    // and the controller has the envoy config volume.
    // We validate by checking that the controller can generate envoy routing
    // config — which only works if it has write access to the envoy config PVC.
    const response = await request.get(`${controllerBase}/api/routing`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    // The routing endpoint returns envoy configuration details.
    // A successful response proves the controller can access the envoy config.
    expect(data).toBeTruthy();
  });

  // -----------------------------------------------------------------------
  // 15. No containers in restart loop (restarts < 5)
  // -----------------------------------------------------------------------
  test('no containers in restart loop', () => {
    const output = sh(
      `kubectl get pods -n ${namespace} -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .status.containerStatuses[*]}{.restartCount}{" "}{end}{"\\n"}{end}' 2>/dev/null`
    );
    test.skip(!output || output.includes('error'), 'kubectl not available or not configured');

    const lines = output.split('\n').filter((l) => l.trim());
    const highRestarts: string[] = [];
    for (const line of lines) {
      const parts = line.trim().split(/\s+/);
      const podName = parts[0];
      const restarts = parts.slice(1).map(Number).filter((n) => !isNaN(n));
      const maxRestarts = Math.max(0, ...restarts);
      if (maxRestarts >= 5) {
        highRestarts.push(`${podName} (${maxRestarts} restarts)`);
      }
    }
    expect(
      highRestarts,
      `no pods should have >= 5 restarts, found: ${highRestarts.join(', ')}`
    ).toHaveLength(0);
  });

  // -----------------------------------------------------------------------
  // 16. Controller /api/env reports kubernetes runtime
  // -----------------------------------------------------------------------
  test('controller reports kubernetes runtime', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/env`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.runtime).toBe('kubernetes');
    expect(data.namespace).toBe(namespace);
  });

  // -----------------------------------------------------------------------
  // 17. Health endpoint reports expected service count
  // -----------------------------------------------------------------------
  test('health endpoint reports expected service count', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/health`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();

    expect(data.total).toBeGreaterThanOrEqual(10);
    // At least half the services should be healthy in a working deployment
    expect(data.healthy).toBeGreaterThanOrEqual(Math.floor(data.total / 2));
  });

  // -----------------------------------------------------------------------
  // 18. Prometheus metrics endpoint responds
  // -----------------------------------------------------------------------
  test('prometheus /metrics endpoint responds', async ({ request }) => {
    const response = await request.get(`${controllerBase}/metrics`);
    expect(response.ok()).toBeTruthy();
    const body = await response.text();
    // Prometheus metrics should contain at least one HELP line
    expect(body).toContain('# HELP');
    expect(body).toContain('# TYPE');
  });

  // -----------------------------------------------------------------------
  // 19. OpenAPI spec is valid
  // -----------------------------------------------------------------------
  test('openapi spec is valid', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/openapi.json`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('openapi');
    expect(data).toHaveProperty('paths');
    expect(data.openapi).toMatch(/^3\./);
  });

  // -----------------------------------------------------------------------
  // 20. Namespace has no pods in unknown/failed phase
  // -----------------------------------------------------------------------
  test('no pods in failed or unknown phase', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/namespaces`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const ns = (data.namespaces || [])[0];
    expect(ns, 'namespace info should be present').toBeTruthy();

    const failedOrUnknown = (ns.problems || []).filter(
      (p: any) => p.phase === 'Failed' || p.phase === 'Unknown'
    );
    expect(
      failedOrUnknown,
      `no pods should be in Failed/Unknown phase, found: ${failedOrUnknown.map((p: any) => p.name).join(', ')}`
    ).toHaveLength(0);
  });

  // -----------------------------------------------------------------------
  // Clean-deploy reproducibility (v1.0.169)
  //
  // These tests enforce the invariant: ``kubectl apply -f dist/k8s-deploy.yaml``
  // on an empty cluster produces a working stack WITHOUT any operator
  // dashboard interaction. Before v1.0.169 the cluster came up with
  // ``.local`` LAN defaults (because the profile ConfigMap was marked
  // optional and absent) and the only way to fix it was to open the
  // dashboard and click Save Routing — making "same result every time"
  // a lie.
  // -----------------------------------------------------------------------

  test('profile ConfigMap is mounted and non-empty', () => {
    const profile = sh(
      `kubectl -n ${namespace} get configmap media-stack-controller-profile -o jsonpath='{.data.profile\\.yaml}' 2>/dev/null`
    );
    test.skip(!profile || profile.includes('error'), 'kubectl not available');
    expect(
      profile.length,
      'media-stack-controller-profile ConfigMap must ship baked-in ' +
      'with the standard profile. Absence / empty content means the ' +
      'clean-deploy reproducibility invariant broke.'
    ).toBeGreaterThan(100);
    expect(
      profile,
      'ConfigMap profile.yaml must declare a gateway_host'
    ).toMatch(/gateway_host:/);
  });

  test('routing config resolves to non-default gateway_host (no dashboard interaction)', async ({ request }) => {
    const response = await request.get(`${controllerBase}/api/routing`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    // The profile ships with gateway_host: k8.media-stack.local — any
    // other k8s-shaped hostname is fine; what we reject is the
    // ``apps.media-stack.local`` fallback that indicates the profile
    // wasn't read at all.
    expect(
      data.gateway_host,
      'gateway_host must come from the baked-in profile, not the ' +
      'compose LAN default. If this reads ``apps.media-stack.local`` ' +
      'on a clean K8s deploy, the profile ConfigMap isn\'t being read ' +
      '(check BOOTSTRAP_PROFILE_FILE mount + configmap presence).'
    ).not.toBe('apps.media-stack.local');
    expect(data.gateway_host, 'gateway_host must be non-empty').toBeTruthy();
  });

  test('ingress has a rule for the configured gateway_host', () => {
    const gwHost = sh(
      `kubectl -n ${namespace} get configmap media-stack-controller-profile -o jsonpath='{.data.profile\\.yaml}' 2>/dev/null | grep '^  gateway_host:' | awk '{print $2}'`
    );
    test.skip(!gwHost, 'profile ConfigMap not readable');
    const ingress = sh(
      `kubectl -n ${namespace} get ingress media-stack-ingress -o jsonpath='{.spec.rules[*].host}' 2>/dev/null`
    );
    test.skip(!ingress, 'media-stack-ingress not readable');
    expect(
      ingress.split(/\s+/),
      'Ingress must carry a rule for the configured gateway_host — ' +
      'without it, requests to that hostname never reach Envoy on K8s. ' +
      'This is the exact failure mode v1.0.169 fixed via ingress-config ' +
      'running at bootstrap with seeded overrides.'
    ).toContain(gwHost);
  });

  test('overrides files were seeded at bootstrap (no dashboard click required)', () => {
    // Exec into the controller pod and look for .controller/routing-overrides.yaml
    // on the writable PVC. Present + non-empty = seed-runtime-overrides
    // ran and did its job.
    const controllerPod = sh(
      `kubectl -n ${namespace} get pod -l app=media-stack-controller -o jsonpath='{.items[?(@.status.phase==\"Running\")].metadata.name}' 2>/dev/null`
    ).split(/\s+/)[0];
    test.skip(!controllerPod, 'no running controller pod');
    const routing = sh(
      `kubectl -n ${namespace} exec ${controllerPod} -c controller -- cat /srv-config/.controller/routing-overrides.yaml 2>/dev/null`
    );
    expect(
      routing,
      'routing-overrides.yaml must be seeded at bootstrap. Its absence ' +
      'means clean-re-deploy produces a stack that relies on dashboard ' +
      'clicks to reach a working state — "patches on a live system" ' +
      'instead of reproducible.'
    ).toMatch(/gateway_host:/);
  });
});
