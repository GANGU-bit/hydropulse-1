import { useCallback, useEffect, useState } from 'react';
import { client } from '../api/client.js';

function createDemoNetwork() {
  // Deterministic UI fallback only; these generated values are not measured network data.
  const center = { lat: 12.9081, lng: 77.4852 };
  const sensorIds = ['J12', 'J40', 'J77', 'J103', 'J150'];
  const nodes = [];
  for (let row = 0; row < 14; row += 1) {
    for (let col = 0; col < 14; col += 1) {
      const n = row * 14 + col + 1;
      nodes.push({
        id: `J${n}`,
        type: n === 1 ? 'reservoir' : 'junction',
        lat: center.lat + (row - 6.5) * 0.00072 + Math.sin(n * 2.3) * 0.00009,
        lng: center.lng + (col - 6.5) * 0.00079 + Math.cos(n * 1.7) * 0.0001,
        elevation_m: 815 + row * 1.1 + col * 0.24,
        base_pressure_m: 39 + ((row + col) % 9) * 0.8,
        base_demand_lps: 0.22 + (n % 7) * 0.05,
        households: 24 + ((n * 17) % 77),
        is_sensor: sensorIds.includes(`J${n}`),
        leak_probability: 0,
      });
    }
  }

  const pipes = [];
  let pipeNumber = 1;
  for (let row = 0; row < 14; row += 1) {
    for (let col = 0; col < 14; col += 1) {
      const n = row * 14 + col + 1;
      if (col < 13) {
        pipes.push({
          id: `P${pipeNumber++}`,
          source: `J${n}`,
          target: `J${n + 1}`,
          diameter_mm: col % 4 === 0 ? 400 : 250,
          length_m: 75 + (n % 90),
          flow_m3h: 80 + ((n * 7) % 90),
          status: 'OPEN',
          road_class: col % 4 === 0 ? 'primary' : 'residential',
        });
      }
      if (row < 13) {
        pipes.push({
          id: `P${pipeNumber++}`,
          source: `J${n}`,
          target: `J${n + 14}`,
          diameter_mm: row % 5 === 0 ? 400 : 150,
          length_m: 68 + (n % 65),
          flow_m3h: 45 + ((n * 9) % 68),
          status: 'OPEN',
          road_class: row % 5 === 0 ? 'primary' : 'residential',
        });
      }
    }
  }
  return {
    network_name: 'synthetic_kengeri_demo',
    center,
    sensor_ids: sensorIds,
    nodes,
    pipes,
  };
}

const demoPresets = [
  {
    id: 'kengeri_mainline_rupture',
    label: 'Mainline rupture',
    description: 'High-flow trunk failure near the Kengeri bus terminal',
    target_node_id: 'J15',
    leak_area_cm2: 45,
    noise_pct: 1.5,
  },
  {
    id: 'residential_leak',
    label: 'Residential leak',
    description: 'Low-flow leak in a dense residential pocket',
    target_node_id: 'J92',
    leak_area_cm2: 8,
    noise_pct: 2,
  },
  {
    id: 'elevated_zone_failure',
    label: 'Elevated zone failure',
    description: 'Pressure loss near the eastern ridge',
    target_node_id: 'J168',
    leak_area_cm2: 24,
    noise_pct: 1,
  },
];

function makeMockResult(network, body) {
  // Keep the demo response stable and internally consistent without a backend or recorded fixture.
  const target = network?.nodes.find((node) => node.id === body.target_node_id);
  if (!target) {
    const error = new Error(`Unknown junction id: ${body.target_node_id}`);
    error.status = 404;
    throw error;
  }
  const junctions = network.nodes.filter((node) => node.type === 'junction');
  const distance = (node) =>
    Math.hypot(
      (node.lat - target.lat) * 111000,
      (node.lng - target.lng) * 95000,
    );
  const candidates = junctions
    .map((node) => ({ node, distance: distance(node) }))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, 5);
  const loss = body.leak_area_cm2 * 19.3;
  const valves = network.pipes
    .filter((pipe) => pipe.source === target.id || pipe.target === target.id)
    .slice(0, 2)
    .map((pipe) => pipe.id);
  const heatmap = Object.fromEntries(
    junctions.map((node) => [
      node.id,
      node.id === target.id
        ? 1
        : Math.max(0, Math.exp(-distance(node) / 250) * 0.84),
    ]),
  );
  const readings = network.sensor_ids.map((sensorId, i) => {
    const baseline =
      network.nodes.find((node) => node.id === sensorId)?.base_pressure_m ?? 42;
    const drop = (5 - i) * 0.8 + ((i * 13 + target.id.length * 7) % 5) * 0.11;
    return {
      sensor_id: sensorId,
      baseline_m: baseline,
      observed_m: baseline - drop,
      drop_m: drop,
    };
  });
  return {
    injected_node_id: target.id,
    injected_leak_area_cm2: body.leak_area_cm2,
    actual_loss_lpm: loss,
    predicted_top_candidates: candidates.map(({ node }, i) => ({
      node_id: node.id,
      confidence_pct: [94.2, 3.1, 1.4, 0.8, 0.5][i],
      estimated_loss_lpm: loss * (1 - i * 0.025),
      tankers_lost_per_day: (loss * 1440) / 6000,
      estimated_leak_area_cm2: body.leak_area_cm2 * (1 - i * 0.02),
    })),
    sensor_readings: readings,
    node_heatmap: heatmap,
    recommended_valves_to_close: valves,
    isolated_node_ids: [
      target.id,
      ...network.nodes
        .filter((node) => node.id !== target.id)
        .slice(0, 5)
        .map((node) => node.id),
    ],
    affected_households_after_isolation: 42,
    localization_error_m: 0,
    compute_time_ms: 312.4,
  };
}

async function loadMockData() {
  // Prefer committed fixtures field by field and synthesize only the pieces that are missing.
  const [networkResult, presetsResult, healthResult] = await Promise.all([
    client.hasMockFile('network.json')
      ? client
          .mockNetwork()
          .then((value) => ({ value, synthetic: false }))
          .catch(() => ({ value: createDemoNetwork(), synthetic: true }))
      : Promise.resolve({ value: createDemoNetwork(), synthetic: true }),
    client.hasMockFile('presets.json')
      ? client.mockPresets().catch(() => ({ presets: demoPresets }))
      : Promise.resolve({ presets: demoPresets }),
    client.hasMockFile('health.json')
      ? client.mockHealth().catch(() => ({
          status: 'mock',
          num_junctions: 196,
          num_pipes: 364,
          sensor_ids: ['J12', 'J40', 'J77', 'J103', 'J150'],
        }))
      : Promise.resolve({
          status: 'mock',
          num_junctions: 196,
          num_pipes: 364,
          sensor_ids: ['J12', 'J40', 'J77', 'J103', 'J150'],
        }),
  ]);
  return {
    network: networkResult.value,
    networkSource: networkResult.synthetic ? 'synthetic' : 'recorded',
    presets: presetsResult.presets || [],
    health: healthResult,
  };
}

export function useHydroPulse() {
  const [network, setNetwork] = useState(null);
  const [networkSource, setNetworkSource] = useState('synthetic');
  const [health, setHealth] = useState(null);
  const [presets, setPresets] = useState([]);
  const [mode, setMode] = useState(client.forcedMock ? 'mock' : 'loading');
  const [phase, setPhase] = useState('idle');
  const [lastResult, setLastResult] = useState(null);
  const [lastRequest, setLastRequest] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    if (client.forcedMock) {
      const data = await loadMockData();
      setNetwork(data.network);
      setNetworkSource(data.networkSource);
      setPresets(data.presets);
      setHealth(data.health);
      setMode('mock');
      return;
    }
    try {
      // Load live health, topology, and presets together as one coherent network snapshot.
      const [liveHealth, liveNetwork, livePresets] = await Promise.all([
        client.getHealth(),
        client.getNetwork(),
        client.getPresets(),
      ]);
      setHealth(liveHealth);
      setNetwork(liveNetwork);
      setNetworkSource('live');
      setPresets(livePresets.presets || []);
      setMode('live');
    } catch (loadError) {
      const data = await loadMockData();
      setNetwork(data.network);
      setNetworkSource(data.networkSource);
      setPresets(data.presets);
      setHealth(data.health);
      setMode('offline');
      setError(loadError.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (mode !== 'live') return undefined;
    // Detect backend outages that happen after the initial load succeeded.
    const timer = setInterval(() => {
      client.getHealth().catch(() => {
        setMode('offline');
        setError('Backend offline: running on recorded data');
      });
    }, 12000);
    return () => clearInterval(timer);
  }, [mode]);

  const simulate = useCallback(
    async (body, presetId) => {
      if (!network) return null;
      setPhase('simulating');
      setError('');
      setLastRequest(body);
      const start = performance.now();
      try {
        let result;
        if (mode === 'live') {
          try {
            result = await client.simulateLeak(body);
          } catch (requestError) {
            // Preserve actionable contract errors; use local fallback only for transport failures.
            if (requestError.status === 404 || requestError.status === 422)
              throw requestError;
            setMode('offline');
            setError('Backend offline: running on recorded data');
            result =
              (await client.mockSimulate(body, presetId)) ||
              makeMockResult(network, body);
          }
        } else {
          result =
            (await client.mockSimulate(body, presetId)) ||
            makeMockResult(network, body);
        }
        // Keep the scan animation visible briefly even when a local fixture resolves instantly.
        await new Promise((resolve) =>
          setTimeout(resolve, Math.max(0, 900 - (performance.now() - start))),
        );
        setLastResult(result);
        setPhase('located');
        return result;
      } catch (simulationError) {
        setError(simulationError.message);
        setPhase(lastResult ? 'located' : 'idle');
        return null;
      }
    },
    [mode, network, lastResult],
  );

  const isolate = useCallback(async () => {
    if (!lastResult) return;
    if (mode === 'live') {
      try {
        // Send the selected valves and incident context the operator is about to isolate.
        await client.isolate({
          valve_ids: lastResult.recommended_valves_to_close,
          target_node_id: lastResult.injected_node_id,
          leak_area_cm2: lastResult.injected_leak_area_cm2,
        });
      } catch (isolationError) {
        if (isolationError.status !== 404) setError(isolationError.message);
      }
    }
    setPhase('isolated');
  }, [lastResult, mode]);

  const reset = () => {
    setLastResult(null);
    setLastRequest(null);
    setPhase('idle');
    setError('');
  };

  return {
    network,
    networkSource,
    health,
    presets,
    mode,
    phase,
    lastResult,
    lastRequest,
    error,
    load,
    simulate,
    isolate,
    reset,
  };
}
