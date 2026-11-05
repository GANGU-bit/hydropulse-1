export const STUDIO_DRAFT_KEY = 'hydropulse.network-studio.draft.v1';
export const STUDIO_DRAFT_VERSION = 1;

export function createEmptyDraft(name = 'Untitled Water Network') {
  return {
    schema_version: STUDIO_DRAFT_VERSION,
    project: {
      id: `draft-${Date.now().toString(36)}`,
      name,
    },
    network: { network_name: name, nodes: [], pipes: [] },
    editor_layout: {
      mode: 'schematic',
      positions: {},
      labels: {},
      notes: {},
      viewport: { x: 0, y: 0, zoom: 1 },
      grid_size: 20,
    },
  };
}

export function normalizeDraft(value) {
  if (
    !value ||
    value.schema_version !== STUDIO_DRAFT_VERSION ||
    !value.project ||
    !Array.isArray(value.network?.nodes) ||
    !Array.isArray(value.network?.pipes) ||
    value.editor_layout?.mode !== 'schematic'
  ) {
    throw new Error('This file is not a supported HydroPulse schematic draft.');
  }
  const nodeIds = new Set(value.network.nodes.map((node) => node.id));
  const positions = Object.fromEntries(
    Object.entries(value.editor_layout.positions || {}).filter(
      ([id, point]) =>
        nodeIds.has(id) && Number.isFinite(point?.x) && Number.isFinite(point?.y),
    ),
  );
  return {
    ...value,
    project: { ...value.project, name: value.project.name || 'Imported draft' },
    network: {
      ...value.network,
      nodes: value.network.nodes.map((node) => ({ ...node })),
      pipes: value.network.pipes.map((pipe) => ({ ...pipe })),
    },
    editor_layout: {
      mode: 'schematic',
      ...value.editor_layout,
      positions,
      labels: { ...(value.editor_layout.labels || {}) },
      notes: { ...(value.editor_layout.notes || {}) },
      viewport: {
        x: Number(value.editor_layout.viewport?.x) || 0,
        y: Number(value.editor_layout.viewport?.y) || 0,
        zoom: Number(value.editor_layout.viewport?.zoom) || 1,
      },
    },
  };
}

export function createDemoDraft(network, source = 'synthetic') {
  if (!Array.isArray(network?.nodes) || network.nodes.length === 0) {
    throw new Error('The network is still loading. Try again in a moment.');
  }

  const byId = new Map(network.nodes.map((node) => [node.id, node]));
  const adjacency = new Map(network.nodes.map((node) => [node.id, []]));
  for (const pipe of network.pipes || []) {
    if (byId.has(pipe.source) && byId.has(pipe.target)) {
      adjacency.get(pipe.source).push(pipe.target);
      adjacency.get(pipe.target).push(pipe.source);
    }
  }

  // Start at a real source, then follow actual network links to keep the sample coherent.
  const seed =
    network.nodes.find((node) => ['reservoir', 'tank'].includes(node.type)) ||
    network.nodes[0];
  const picked = [];
  const seen = new Set();
  const queue = [seed.id];
  while (queue.length && picked.length < 8) {
    const id = queue.shift();
    if (seen.has(id)) continue;
    seen.add(id);
    picked.push(byId.get(id));
    queue.push(...(adjacency.get(id) || []));
  }
  for (const node of network.nodes) {
    if (picked.length >= 8) break;
    if (!seen.has(node.id)) picked.push(node);
  }
  const ids = new Set(picked.map((node) => node.id));
  const name = `${network.network_name || 'HydroPulse'} schematic draft`;
  const draft = createEmptyDraft(name);
  draft.project.source = source;
  draft.network = {
    ...network,
    nodes: picked.map((node) => ({ ...node })),
    pipes: (network.pipes || [])
      .filter((pipe) => ids.has(pipe.source) && ids.has(pipe.target))
      .map((pipe) => ({ ...pipe })),
  };
  draft.editor_layout.positions = Object.fromEntries(
    picked.map((node, index) => [
      node.id,
      { x: 100 + (index % 3) * 185, y: 80 + Math.floor(index / 3) * 150 },
    ]),
  );
  draft.editor_layout.labels = Object.fromEntries(
    picked.map((node) => [node.id, node.display_label || node.id]),
  );
  return draft;
}

export function makeNextId(type, nodes, pipes) {
  const prefix = { junction: 'J', reservoir: 'R', tank: 'T', pipe: 'P' }[type];
  const existing = [...nodes, ...pipes];
  const used = new Set(existing.map((item) => item.id));
  const pattern = new RegExp(`^${prefix}-?(\\d+)$`);
  const largest = existing.reduce((max, item) => {
    const match = String(item.id).match(pattern);
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);
  let sequence = largest + 1;
  let id = `${prefix}-${String(sequence).padStart(3, '0')}`;
  while (used.has(id)) {
    sequence += 1;
    id = `${prefix}-${String(sequence).padStart(3, '0')}`;
  }
  return id;
}

export function validateDraft(draft) {
  const errors = [];
  const warnings = [];
  const addWarning = (message, objectType, objectId) =>
    warnings.push({ severity: 'warning', message, objectType, objectId });
  const addError = (message, objectType, objectId) =>
    errors.push({ severity: 'error', message, objectType, objectId });
  const { nodes, pipes } = draft.network;
  const nodeIds = new Set();
  const objectIds = new Set();

  for (const node of nodes) {
    if (!node.id || objectIds.has(node.id)) {
      addError(`Duplicate or empty node ID: ${node.id || '(empty)'}`, 'node', node.id);
    }
    nodeIds.add(node.id);
    objectIds.add(node.id);
    const numericFields =
      node.type === 'junction' ? ['elevation_m', 'base_demand_lps'] : ['elevation_m'];
    for (const field of numericFields) {
      const value = node[field];
      if (value == null || value === '') {
        addWarning(`${node.id} is missing ${field}.`, 'node', node.id);
      } else if (!Number.isFinite(Number(value))) {
        addError(`${node.id} has an invalid ${field}.`, 'node', node.id);
      } else if (Number(value) < 0) {
        addError(`${field} cannot be negative.`, 'node', node.id);
      }
    }
    if (!draft.editor_layout.positions[node.id]) {
      addWarning(`${node.id} has no schematic position.`, 'node', node.id);
    }
  }

  const endpointPairs = new Set();
  const degree = new Map(nodes.map((node) => [node.id, 0]));
  for (const pipe of pipes) {
    if (!pipe.id || objectIds.has(pipe.id)) {
      addError(`Duplicate or empty pipe ID: ${pipe.id || '(empty)'}`, 'pipe', pipe.id);
    }
    objectIds.add(pipe.id);
    if (!nodeIds.has(pipe.source) || !nodeIds.has(pipe.target)) {
      addError(`${pipe.id} references a node that does not exist.`, 'pipe', pipe.id);
      continue;
    }
    if (pipe.source === pipe.target) {
      addError(`${pipe.id} cannot connect a node to itself.`, 'pipe', pipe.id);
      continue;
    }
    degree.set(pipe.source, degree.get(pipe.source) + 1);
    degree.set(pipe.target, degree.get(pipe.target) + 1);
    const pair = [pipe.source, pipe.target].sort().join('|');
    if (endpointPairs.has(pair)) {
      addWarning(`${pipe.id} duplicates an existing endpoint pair.`, 'pipe', pipe.id);
    }
    endpointPairs.add(pair);
    if (pipe.status !== 'OPEN' && pipe.status !== 'CLOSED') {
      addError(`${pipe.id} must have OPEN or CLOSED status.`, 'pipe', pipe.id);
    }
    if (pipe.road_class == null || pipe.road_class === '') {
      addWarning(`${pipe.id} is missing road_class metadata.`, 'pipe', pipe.id);
    }
    for (const field of ['diameter_mm', 'length_m']) {
      const value = pipe[field];
      if (value == null || value === '') {
        addWarning(`${pipe.id} is missing ${field}.`, 'pipe', pipe.id);
      } else if (!Number.isFinite(Number(value)) || Number(value) <= 0) {
        addError(`${pipe.id} needs a positive ${field}.`, 'pipe', pipe.id);
      }
    }
  }

  const sources = nodes
    .filter((node) => ['reservoir', 'tank'].includes(node.type))
    .map((node) => node.id);
  if (!sources.length && nodes.length) {
    addWarning('Add a reservoir or tank to identify a network source.', 'node', nodes[0].id);
  }

  // Reachability is an undirected topology check; it does not estimate flow or pressure.
  const adjacency = new Map(nodes.map((node) => [node.id, []]));
  for (const pipe of pipes) {
    if (adjacency.has(pipe.source) && adjacency.has(pipe.target) && pipe.source !== pipe.target) {
      adjacency.get(pipe.source).push(pipe.target);
      adjacency.get(pipe.target).push(pipe.source);
    }
  }
  const reachable = new Set();
  const queue = [...sources];
  while (queue.length) {
    const id = queue.shift();
    if (reachable.has(id)) continue;
    reachable.add(id);
    queue.push(...(adjacency.get(id) || []));
  }
  const warnedComponents = new Set();
  for (const node of nodes) {
    if (node.type === 'junction' && (degree.get(node.id) || 0) === 0) {
      addWarning(`${node.id} is isolated.`, 'node', node.id);
    } else if (node.type === 'junction' && degree.get(node.id) === 1) {
      addWarning(`${node.id} is a dead end.`, 'node', node.id);
    }
    if (!reachable.has(node.id) && !warnedComponents.has(node.id)) {
      const component = [];
      const pending = [node.id];
      while (pending.length) {
        const id = pending.pop();
        if (warnedComponents.has(id)) continue;
        warnedComponents.add(id);
        component.push(id);
        pending.push(...(adjacency.get(id) || []));
      }
      addWarning(
        `Component (${component.length} node${component.length === 1 ? '' : 's'}) is not connected to a source.`,
        'node',
        node.id,
      );
    }
  }

  return { errors, warnings };
}
