// Keep the API base configurable while Vite proxies /api locally.
const BASE = import.meta.env.VITE_API_BASE ?? '';
const forced = import.meta.env.VITE_USE_MOCK === 'true';
// Fail over quickly so an unreachable backend does not leave the control room waiting.
const timeout = 4000;
// Vite injects fixture filenames so absent optional mocks do not produce avoidable 404s.
const mockFiles = __HYDROPULSE_MOCK_FILES__;
async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const e = new Error(data.detail || `Request failed (${response.status})`);
      e.status = response.status;
      throw e;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}
const mock = async (file) => {
  const res = await fetch(`/mock/${file}`);
  if (!res.ok) throw new Error(`Missing mock data: ${file}`);
  return res.json();
};
export const client = {
  forcedMock: forced,
  hasMockFile: (file) => mockFiles.includes(file),
  getHealth: () => request('/api/health'),
  getNetwork: () => request('/api/network'),
  getPresets: () => request('/api/presets'),
  mockHealth: () => mock('health.json'),
  mockNetwork: () => mock('network.json'),
  mockPresets: () => mock('presets.json'),
  mockSimulate: (body, presetId) => {
    // Preset snapshots are optional; callers synthesize a labeled demo result when none exists.
    const file = `simulate_leak_${presetId || 'default'}.json`;
    return mockFiles.includes(file) ? mock(file) : Promise.resolve(null);
  },
  simulateLeak: (body) =>
    request('/api/simulate-leak', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  isolate: (body) =>
    request('/api/isolate', { method: 'POST', body: JSON.stringify(body) }),
};
