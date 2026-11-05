import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from '@xyflow/react';
import {
  AlertTriangle,
  ArrowLeft,
  Box,
  Check,
  CheckCircle2,
  Circle,
  Download,
  FilePlus2,
  GitBranch,
  Layers2,
  MousePointer2,
  Redo2,
  RotateCcw,
  Save,
  Undo2,
  Upload,
  Waves,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import {
  createDemoDraft,
  createEmptyDraft,
  makeNextId,
  normalizeDraft,
  STUDIO_DRAFT_KEY,
  validateDraft,
} from '../lib/networkStudio.js';

const clone = (value) => JSON.parse(JSON.stringify(value));
const NODE_TYPES = ['junction', 'reservoir', 'tank'];
const TOOL_ITEMS = [
  { id: 'select', label: 'Select', icon: MousePointer2, key: 'V' },
  { id: 'junction', label: 'Junction', icon: Circle, key: 'J' },
  { id: 'reservoir', label: 'Reservoir', icon: Waves, key: 'R' },
  { id: 'tank', label: 'Tank', icon: Box, key: 'T' },
  { id: 'pipe', label: 'Pipe', icon: GitBranch, key: 'P' },
  { id: 'delete', label: 'Delete', icon: X, key: 'Del' },
];

function StudioNode({ data, selected }) {
  if (data.preview) return <div className='studio-ghost-node'><Handle type='target' position={Position.Left} id='in' /></div>;
  const type = data.componentType;
  const Icon = type === 'reservoir' ? Waves : type === 'tank' ? Box : Circle;
  return (
    <div
      className={`studio-node node-${type} ${selected ? 'is-selected' : ''} ${data.pending ? 'is-pending' : ''}`}
      aria-label={`${type} ${data.label}`}
      title={`${type} · ${data.id}`}
    >
      <Handle type='target' position={Position.Left} id='in' />
      <span className='studio-node-mark' aria-hidden='true'>
        <Icon size={type === 'junction' ? 14 : 17} strokeWidth={2.2} />
      </span>
      <span className='studio-node-copy'>
        <b>{data.label}</b>
        <small>{data.id}</small>
      </span>
      <Handle type='source' position={Position.Right} id='out' />
    </div>
  );
}

function StudioPipe({ id, sourceX, sourceY, targetX, targetY, data, selected }) {
  const end = data?.previewEnd;
  const finalX = end?.x ?? targetX;
  const finalY = end?.y ?? targetY;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX: finalX,
    targetY: finalY,
  });
  const preview = data?.preview;
  const closed = data?.status === 'CLOSED';
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: preview ? '#EFC07B' : '#EFC07B',
          strokeWidth: selected ? 5 : 3.5,
          strokeDasharray: preview || closed ? '8 7' : undefined,
          opacity: preview ? 0.78 : closed ? 0.48 : 0.92,
          strokeLinecap: 'round',
          filter: selected ? 'drop-shadow(0 0 5px #efc07b88)' : undefined,
        }}
      />
      {!preview && (selected || data?.showLabel) && (
        <EdgeLabelRenderer>
          <div
            className='studio-pipe-label nodrag nopan'
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }}
          >
            {id} · {data?.diameter ? `${data.diameter} mm` : 'Ø —'}
          </div>
        </EdgeLabelRenderer>
      )}
      {preview && (
        <EdgeLabelRenderer>
          <div
            className='studio-pipe-label pipe-preview-label nodrag nopan'
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }}
          >
            Choose destination
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { studioNode: StudioNode };
const edgeTypes = { studioPipe: StudioPipe };

function ToolbarButton({ icon: Icon, label, onClick, disabled = false, active = false, title }) {
  return (
    <button
      className={`studio-icon-button ${active ? 'is-active' : ''}`}
      type='button'
      aria-label={label}
      title={title || label}
      disabled={disabled}
      onClick={onClick}
    >
      <Icon size={16} />
    </button>
  );
}

function StudioField({ label, value, type = 'text', unit, onCommit, min, step, readOnly = false }) {
  const [draft, setDraft] = useState(value ?? '');
  useEffect(() => setDraft(value ?? ''), [value]);
  return (
    <label className='studio-field'>
      <span>{label}{unit && <small>{unit}</small>}</span>
      <input
        type={type}
        min={min}
        step={step}
        value={draft}
        readOnly={readOnly}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => !readOnly && onCommit(draft)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur();
        }}
      />
    </label>
  );
}

function StudioNotesField({ value, onCommit }) {
  const [draft, setDraft] = useState(value || '');
  useEffect(() => setDraft(value || ''), [value]);
  return (
    <label className='studio-field studio-notes-field'>
      <span>Draft note</span>
      <textarea
        rows='3'
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => onCommit(draft)}
      />
    </label>
  );
}

function PropertiesPanel({ draft, selection, onField, onRemove }) {
  const network = draft.network;
  const selectedNode =
    selection?.type === 'node'
      ? network.nodes.find((node) => node.id === selection.id)
      : null;
  const selectedPipe =
    selection?.type === 'pipe'
      ? network.pipes.find((pipe) => pipe.id === selection.id)
      : null;
  const selected = selectedNode || selectedPipe;
  const typeName = selectedNode?.type || (selectedPipe ? 'pipe' : '');
  const labelMap = draft.editor_layout.labels || {};
  const notesMap = draft.editor_layout.notes || {};
  const label = selected ? labelMap[selected.id] || selected.id : '';
  const commitNumber = (field, raw) =>
    onField(selected.id, field, raw === '' ? '' : Number(raw), selection.type);
  return (
    <aside className='studio-properties clay-panel'>
      <div className='studio-aside-heading'>
        <div>
          <span className='studio-eyebrow'>ENGINEERING DATA</span>
          <h2>Properties</h2>
        </div>
        <Layers2 size={17} />
      </div>
      {!selected ? (
        <div className='studio-property-empty'>
          <div className='studio-empty-glyph'><MousePointer2 size={19} /></div>
          <b>No selection</b>
          <p>Select a station or pipe to inspect and edit its draft properties.</p>
        </div>
      ) : (
        <div className='studio-property-content' key={`${selection.type}:${selected.id}`}>
          <div className='studio-selected-object'>
            <span className={`studio-object-icon object-${typeName}`}>
              {selectedPipe ? <GitBranch size={16} /> : typeName === 'tank' ? <Box size={16} /> : typeName === 'reservoir' ? <Waves size={16} /> : <Circle size={16} />}
            </span>
            <div><small>SELECTED {typeName.toUpperCase()}</small><b>{selected.id}</b></div>
          </div>
          <div className='studio-field-group'>
            <span className='studio-group-title'>IDENTITY</span>
            <StudioField label='Object ID' value={selected.id} readOnly onCommit={() => {}} />
            <StudioField
              label='Display label'
              value={label}
              onCommit={(value) => onField(selected.id, 'label', value, 'label')}
            />
          </div>
          {selectedNode && (
            <div className='studio-field-group'>
              <span className='studio-group-title'>NODE DATA</span>
              <StudioField
                label={selectedNode.type === 'reservoir' ? 'Head / elevation' : 'Elevation'}
                unit='m'
                type='number'
                min='0'
                step='0.1'
                value={selectedNode.elevation_m}
                onCommit={(value) => commitNumber('elevation_m', value)}
              />
              {selectedNode.type === 'junction' && (
                <StudioField
                  label='Base demand'
                  unit='L/s'
                  type='number'
                  min='0'
                  step='0.01'
                  value={selectedNode.base_demand_lps}
                  onCommit={(value) => commitNumber('base_demand_lps', value)}
                />
              )}
              {selectedNode.type === 'tank' && (
                <p className='studio-field-note'>Tank level fields are not part of the current HydroPulse network contract.</p>
              )}
            </div>
          )}
          {selectedPipe && (
            <div className='studio-field-group'>
              <span className='studio-group-title'>PIPE DATA</span>
              <StudioField label='Source node' value={selectedPipe.source} readOnly onCommit={() => {}} />
              <StudioField label='Target node' value={selectedPipe.target} readOnly onCommit={() => {}} />
              <StudioField
                label='Diameter'
                unit='mm'
                type='number'
                min='0'
                step='1'
                value={selectedPipe.diameter_mm}
                onCommit={(value) => commitNumber('diameter_mm', value)}
              />
              <StudioField
                label='Length'
                unit='m'
                type='number'
                min='0'
                step='1'
                value={selectedPipe.length_m}
                onCommit={(value) => commitNumber('length_m', value)}
              />
              <label className='studio-field'>
                <span>Status</span>
                <select value={selectedPipe.status || 'OPEN'} onChange={(event) => onField(selected.id, 'status', event.target.value, 'pipe')}>
                  <option value='OPEN'>Open</option>
                  <option value='CLOSED'>Closed</option>
                </select>
              </label>
              <p className='studio-field-note'>Roughness and material are not stored in the current network contract.</p>
            </div>
          )}
          <div className='studio-field-group'>
            <span className='studio-group-title'>NOTES</span>
            <StudioNotesField
              value={notesMap[selected.id] || ''}
              onCommit={(value) => onField(selected.id, 'notes', value, 'notes')}
            />
          </div>
          <button className='studio-delete-object' onClick={() => onRemove(selection)}>
            <X size={14} /> Delete {typeName}
          </button>
        </div>
      )}
    </aside>
  );
}

function FlowViewport({ draft, viewVersion }) {
  const flow = useReactFlow();
  useEffect(() => {
    flow.setViewport(draft.editor_layout.viewport || { x: 0, y: 0, zoom: 1 }, { duration: 0 });
  }, [flow, draft.project.id, viewVersion]);
  return null;
}

function StudioWorkspace({ initialNetwork, networkSource, onExit }) {
  const [draft, setDraft] = useState(() => createEmptyDraft());
  const draftRef = useRef(draft);
  const [tool, setTool] = useState('select');
  const [selection, setSelection] = useState(null);
  const [pendingConnection, setPendingConnection] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [past, setPast] = useState([]);
  const [future, setFuture] = useState([]);
  const [notice, setNotice] = useState('');
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [viewVersion, setViewVersion] = useState(0);
  const [projectNameDraft, setProjectNameDraft] = useState(draft.project.name);
  const fileInput = useRef(null);
  const dragSnapshot = useRef(null);
  const flow = useReactFlow();
  const validation = useMemo(() => validateDraft(draft), [draft]);
  const allIssues = [...validation.errors, ...validation.warnings];

  const replaceDraft = useCallback((next, markDirty = true) => {
    const normalized = normalizeDraft(next);
    draftRef.current = normalized;
    setDraft(normalized);
    setProjectNameDraft(normalized.project.name);
    setSelection(null);
    setPendingConnection(null);
    setPast([]);
    setFuture([]);
    setDirty(markDirty);
    setViewVersion((version) => version + 1);
  }, []);

  const commit = useCallback((mutate) => {
    const before = draftRef.current;
    const next = mutate(clone(before));
    if (!next || JSON.stringify(next) === JSON.stringify(before)) return;
    setPast((items) => [...items.slice(-49), clone(before)]);
    setFuture([]);
    draftRef.current = next;
    setDraft(next);
    setProjectNameDraft(next.project.name);
    setDirty(true);
  }, []);

  const setLiveDocument = useCallback((mutate) => {
    const next = mutate(clone(draftRef.current));
    draftRef.current = next;
    setDraft(next);
    setDirty(true);
  }, []);

  const loadDemo = useCallback(() => {
    if (dirty && !window.confirm('Discard unsaved changes and load the Kengeri sample?')) return;
    try {
      replaceDraft(createDemoDraft(initialNetwork, networkSource), true);
      setNotice(networkSource === 'synthetic' ? 'Loaded the synthetic Kengeri demo. Its engineering data is illustrative.' : 'Loaded a schematic sample from the current Kengeri network.');
    } catch (error) {
      setNotice(error.message);
    }
  }, [dirty, initialNetwork, networkSource, replaceDraft]);

  const newBlank = useCallback(() => {
    if (dirty && !window.confirm('Discard unsaved changes and start a blank network?')) return;
    replaceDraft(createEmptyDraft(), true);
    setNotice('Blank schematic created. Add a source, then place and connect nodes.');
  }, [dirty, replaceDraft]);

  const saveDraft = useCallback(() => {
    const saved = { ...draftRef.current, saved_at: new Date().toISOString() };
    try {
      localStorage.setItem(STUDIO_DRAFT_KEY, JSON.stringify(saved));
      draftRef.current = saved;
      setDraft(saved);
      setDirty(false);
      setNotice('Draft saved in this browser.');
    } catch {
      setNotice('Could not save this draft in browser storage. Export the JSON file instead.');
    }
  }, []);

  const reopenDraft = useCallback(() => {
    if (dirty && !window.confirm('Discard unsaved changes and reopen the saved draft?')) return;
    try {
      const raw = localStorage.getItem(STUDIO_DRAFT_KEY);
      if (!raw) {
        setNotice('No saved draft was found in this browser.');
        return;
      }
      replaceDraft(JSON.parse(raw), false);
      setNotice('Saved draft reopened.');
    } catch (error) {
      setNotice(error.message || 'The saved draft could not be opened.');
    }
  }, [dirty, replaceDraft]);

  const exportDraft = useCallback(() => {
    const blob = new Blob([JSON.stringify(draftRef.current, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(draftRef.current.project.name || 'hydropulse-draft').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.json`;
    link.click();
    URL.revokeObjectURL(url);
    setNotice('Draft JSON exported.');
  }, []);

  const importDraft = useCallback(async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (dirty && !window.confirm('Discard unsaved changes and open this draft file?')) return;
    try {
      replaceDraft(JSON.parse(await file.text()), false);
      setNotice(`Opened ${file.name}.`);
    } catch (error) {
      setNotice(error.message || 'Could not read this draft file.');
    }
  }, [dirty, replaceDraft]);

  const undo = useCallback(() => {
    if (!past.length) return;
    const previous = past[past.length - 1];
    setFuture((items) => [...items, clone(draftRef.current)]);
    setPast((items) => items.slice(0, -1));
    draftRef.current = previous;
    setDraft(previous);
    setProjectNameDraft(previous.project.name);
    setDirty(true);
    setSelection(null);
  }, [past]);

  const redo = useCallback(() => {
    if (!future.length) return;
    const next = future[future.length - 1];
    setPast((items) => [...items, clone(draftRef.current)]);
    setFuture((items) => items.slice(0, -1));
    draftRef.current = next;
    setDraft(next);
    setProjectNameDraft(next.project.name);
    setDirty(true);
    setSelection(null);
  }, [future]);

  const updateField = useCallback((id, field, value, kind) => {
    commit((next) => {
      if (kind === 'label') next.editor_layout.labels[id] = value;
      else if (kind === 'notes') next.editor_layout.notes[id] = value;
      else if (kind === 'pipe') {
        next.network.pipes = next.network.pipes.map((pipe) => pipe.id === id ? { ...pipe, [field]: value } : pipe);
      } else {
        next.network.nodes = next.network.nodes.map((node) => node.id === id ? { ...node, [field]: value } : node);
      }
      return next;
    });
  }, [commit]);

  const addNode = useCallback((type, position) => {
    const before = draftRef.current;
    const id = makeNextId(type, before.network.nodes, before.network.pipes);
    const node = {
      id,
      type,
      lat: null,
      lng: null,
      elevation_m: null,
      base_pressure_m: null,
      base_demand_lps: type === 'junction' ? null : 0,
      households: null,
      is_sensor: false,
      leak_probability: 0,
    };
    commit((next) => {
      next.network.nodes.push(node);
      next.editor_layout.positions[id] = {
        x: Math.round(position.x / 20) * 20,
        y: Math.round(position.y / 20) * 20,
      };
      next.editor_layout.labels[id] = `${type[0].toUpperCase()}${type.slice(1)} ${id.split('-').at(-1)}`;
      return next;
    });
    setSelection({ type: 'node', id });
    setNotice(`${type[0].toUpperCase()}${type.slice(1)} ${id} added. Set its required properties in the right panel.`);
  }, [commit]);

  const addPipe = useCallback((source, target) => {
    const current = draftRef.current;
    if (source === target) {
      setNotice('A pipe must connect two different nodes.');
      return;
    }
    const exists = current.network.pipes.some((pipe) =>
      (pipe.source === source && pipe.target === target) ||
      (pipe.source === target && pipe.target === source),
    );
    if (exists) {
      setNotice('Those nodes already have a pipe between them.');
      return;
    }
    const id = makeNextId('pipe', current.network.nodes, current.network.pipes);
    commit((next) => {
      next.network.pipes.push({
        id,
        source,
        target,
        diameter_mm: null,
        length_m: null,
        flow_m3h: null,
        status: 'OPEN',
        road_class: null,
      });
      return next;
    });
    setSelection({ type: 'pipe', id });
    setPendingConnection(null);
    setNotice(`Pipe ${id} added. Enter its diameter and length in the right panel.`);
  }, [commit]);

  const requestDelete = useCallback((item) => {
    if (!item) return;
    const connected = item.type === 'node'
      ? draftRef.current.network.pipes.filter((pipe) => pipe.source === item.id || pipe.target === item.id)
      : [];
    if (connected.length) setPendingDelete({ ...item, connected });
    else setPendingDelete({ ...item, connected: [] });
  }, []);

  const confirmDelete = useCallback(() => {
    const item = pendingDelete;
    if (!item) return;
    commit((next) => {
      if (item.type === 'node') {
        next.network.nodes = next.network.nodes.filter((node) => node.id !== item.id);
        next.network.pipes = next.network.pipes.filter((pipe) => pipe.source !== item.id && pipe.target !== item.id);
        delete next.editor_layout.positions[item.id];
        delete next.editor_layout.labels[item.id];
        delete next.editor_layout.notes[item.id];
      } else {
        next.network.pipes = next.network.pipes.filter((pipe) => pipe.id !== item.id);
      }
      return next;
    });
    setSelection(null);
    setPendingDelete(null);
    setNotice(`${item.id} and any dependent pipes were deleted. Undo can restore them.`);
  }, [commit, pendingDelete]);

  const handleNodeClick = useCallback((_event, node) => {
    if (tool === 'delete') {
      requestDelete({ type: 'node', id: node.id });
      return;
    }
    if (tool === 'pipe') {
      if (!pendingConnection) {
        setPendingConnection(node.id);
        setSelection({ type: 'node', id: node.id });
        setNotice(`Starting at ${node.id}. Choose a different node to finish the pipe.`);
      } else {
        addPipe(pendingConnection, node.id);
      }
      return;
    }
    setSelection({ type: 'node', id: node.id });
  }, [tool, pendingConnection, requestDelete, addPipe]);

  const handleEdgeClick = useCallback((_event, edge) => {
    if (tool === 'delete') requestDelete({ type: 'pipe', id: edge.id });
    else if (edge.data?.preview) return;
    else setSelection({ type: 'pipe', id: edge.id });
  }, [tool, requestDelete]);

  const onPaneClick = useCallback((event) => {
    if (NODE_TYPES.includes(tool)) {
      addNode(tool, flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    } else if (tool === 'select') {
      setSelection(null);
    } else if (tool === 'delete') {
      setSelection(null);
    }
  }, [tool, addNode, flow]);

  const onNodesChange = useCallback((changes) => {
    const positions = changes.filter((change) => change.type === 'position' && change.position);
    if (positions.length) {
      setLiveDocument((next) => {
        for (const change of positions) next.editor_layout.positions[change.id] = change.position;
        return next;
      });
    }
    for (const change of changes) {
      if (change.type === 'select') {
        setSelection(change.selected ? { type: 'node', id: change.id } : null);
      }
    }
  }, [setLiveDocument]);

  // Explicit dimensions, measured geometry, and handle definitions ensure React Flow can
  // synchronously resolve handle bounds on every render without waiting for ResizeObserver,
  // preventing controlled edges from temporarily vanishing when selection or draft state updates.
  const nodes = useMemo(() => draft.network.nodes.map((node) => ({
    id: node.id,
    type: 'studioNode',
    width: 160,
    height: 56,
    initialWidth: 160,
    initialHeight: 56,
    measured: { width: 160, height: 56 },
    handles: [
      { id: 'in', type: 'target', position: Position.Left, x: 0, y: 28, width: 8, height: 8 },
      { id: 'out', type: 'source', position: Position.Right, x: 160, y: 28, width: 8, height: 8 },
    ],
    position: draft.editor_layout.positions[node.id] || { x: 0, y: 0 },
    selected: selection?.type === 'node' && selection.id === node.id,
    data: {
      componentType: node.type,
      label: draft.editor_layout.labels[node.id] || node.id,
      pending: pendingConnection === node.id,
    },
  })), [draft.network.nodes, draft.editor_layout.positions, draft.editor_layout.labels, selection, pendingConnection]);

  const flowNodes = useMemo(() => {
    if (!pendingConnection || !cursor) return nodes;
    return [...nodes, {
      id: '__connection-preview__',
      type: 'studioNode',
      width: 2,
      height: 2,
      initialWidth: 2,
      initialHeight: 2,
      measured: { width: 2, height: 2 },
      handles: [
        { id: 'in', type: 'target', position: Position.Left, x: 0, y: 1, width: 2, height: 2 },
      ],
      position: cursor,
      selectable: false,
      draggable: false,
      connectable: false,
      data: { preview: true },
    }];
  }, [nodes, pendingConnection, cursor]);

  const edges = useMemo(() => {
    const nodeIds = new Set(draft.network.nodes.map((node) => node.id));
    const real = draft.network.pipes.map((pipe) => ({
      id: pipe.id,
      source: pipe.source,
      target: pipe.target,
      sourceHandle: 'out',
      targetHandle: 'in',
      type: 'studioPipe',
      selected: selection?.type === 'pipe' && selection.id === pipe.id,
      data: { status: pipe.status, diameter: pipe.diameter_mm, showLabel: false },
    })).filter((edge) =>
      edge.source !== edge.target && nodeIds.has(edge.source) && nodeIds.has(edge.target),
    );
    if (!pendingConnection || !cursor) return real;
    return [...real, {
      id: '__connection-preview__',
      source: pendingConnection,
      target: '__connection-preview__',
      sourceHandle: 'out',
      targetHandle: 'in',
      type: 'studioPipe',
      selectable: false,
      focusable: false,
      data: { preview: true, previewEnd: cursor },
    }];
  }, [draft.network.pipes, selection, pendingConnection, cursor]);

  const startDrag = useCallback(() => {
    dragSnapshot.current = clone(draftRef.current);
  }, []);

  const finishDrag = useCallback(() => {
    const before = dragSnapshot.current;
    const after = draftRef.current;
    dragSnapshot.current = null;
    if (before && JSON.stringify(before.editor_layout.positions) !== JSON.stringify(after.editor_layout.positions)) {
      setPast((items) => [...items.slice(-49), before]);
      setFuture([]);
    }
  }, []);

  const onMoveEnd = useCallback((_event, viewport) => {
    const current = draftRef.current.editor_layout.viewport || {};
    if (
      Math.abs((current.x || 0) - viewport.x) < 0.01 &&
      Math.abs((current.y || 0) - viewport.y) < 0.01 &&
      Math.abs((current.zoom || 1) - viewport.zoom) < 0.001
    ) return;
    setLiveDocument((next) => {
      next.editor_layout.viewport = viewport;
      return next;
    });
  }, [setLiveDocument]);

  const focusIssue = useCallback((issue) => {
    setSelection({ type: issue.objectType, id: issue.objectId });
    if (issue.objectType === 'node') flow.fitView({ nodes: [{ id: issue.objectId }], duration: 350, padding: 0.7 });
    else {
      const pipe = draftRef.current.network.pipes.find((item) => item.id === issue.objectId);
      if (pipe) flow.fitView({ nodes: [{ id: pipe.source }, { id: pipe.target }], duration: 350, padding: 0.7 });
    }
    setIssuesOpen(false);
  }, [flow]);

  const changeTool = useCallback((nextTool) => {
    setTool(nextTool);
    setPendingConnection(null);
    setCursor(null);
    if (nextTool !== 'select') setSelection(null);
  }, []);

  const leaveStudio = useCallback(() => {
    if (dirty && !window.confirm('You have unsaved schematic changes. Leave Network Studio?')) return;
    onExit();
  }, [dirty, onExit]);

  useEffect(() => {
    const preventClose = (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', preventClose);
    return () => window.removeEventListener('beforeunload', preventClose);
  }, [dirty]);

  useEffect(() => {
    const onKeyDown = (event) => {
      const target = event.target;
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName) || target?.isContentEditable;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        if (typing) return;
        event.preventDefault();
        event.shiftKey ? redo() : undo();
        return;
      }
      if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === 'escape') {
        setPendingConnection(null);
        if (tool !== 'select') changeTool('select');
      } else if (key === 'delete' || key === 'backspace') {
        if (selection) {
          event.preventDefault();
          requestDelete(selection);
        }
      } else {
        const item = TOOL_ITEMS.find((candidate) => candidate.key.toLowerCase() === key);
        if (item && item.id !== 'delete') changeTool(item.id);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [undo, redo, tool, changeTool, selection, requestDelete]);

  const placeHint = NODE_TYPES.includes(tool)
    ? `Click the canvas to place a ${tool}.`
    : tool === 'pipe'
      ? pendingConnection ? `Choose the second endpoint for ${pendingConnection}.` : 'Click a starting node, then an ending node.'
      : tool === 'delete'
        ? 'Click an object to delete it.'
        : 'Drag stations to arrange the schematic. Click an object to edit its properties.';

  return (
    <main className='hp-studio'>
      <header className='studio-toolbar clay-panel'>
        <button className='studio-back' onClick={leaveStudio} aria-label='Back to control room'>
          <ArrowLeft size={17} /><span>Control room</span>
        </button>
        <div className='studio-brand-mark'><GitBranch size={17} /></div>
        <div className='studio-title-block'>
          <span className='studio-eyebrow'>HYDROPULSE · WORKSPACE</span>
          <input
            aria-label='Draft project name'
            value={projectNameDraft}
            onChange={(event) => setProjectNameDraft(event.target.value)}
            onBlur={() => {
              if (projectNameDraft !== draftRef.current.project.name) commit((next) => {
                next.project.name = projectNameDraft || 'Untitled Water Network';
                next.network.network_name = next.project.name;
                return next;
              });
            }}
            onKeyDown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
          />
        </div>
        <span className={`studio-save-state ${dirty ? 'is-dirty' : ''}`}>
          <i />{dirty ? 'Unsaved changes' : 'Saved locally'}
        </span>
        <div className='studio-toolbar-separator' />
        <ToolbarButton icon={Undo2} label='Undo' disabled={!past.length} onClick={undo} />
        <ToolbarButton icon={Redo2} label='Redo' disabled={!future.length} onClick={redo} />
        <div className='studio-toolbar-separator' />
        <button className='studio-text-button secondary' onClick={loadDemo} title='Load a small Kengeri network sample'>
          <Waves size={15} /> Load sample
        </button>
        <button className='studio-text-button secondary studio-new-button' onClick={newBlank}>
          <FilePlus2 size={15} /> New blank
        </button>
        <ToolbarButton icon={RotateCcw} label='Reopen saved draft' onClick={reopenDraft} />
        <ToolbarButton icon={Upload} label='Open draft JSON' onClick={() => fileInput.current?.click()} />
        <ToolbarButton icon={Download} label='Export draft JSON' onClick={exportDraft} disabled={!draft.network.nodes.length} />
        <button className='studio-text-button primary' onClick={saveDraft}>
          <Save size={15} /> Save draft
        </button>
        <input ref={fileInput} type='file' accept='application/json,.json' hidden onChange={importDraft} />
      </header>

      <section className='studio-workspace'>
        <aside className='studio-toolbox clay-panel'>
          <div className='studio-aside-heading'>
            <div><span className='studio-eyebrow'>BUILD</span><h2>Toolbox</h2></div>
          </div>
          <span className='studio-group-title'>EDIT TOOLS</span>
          <div className='studio-tool-list'>
            {TOOL_ITEMS.map(({ id, label, icon: Icon, key }) => (
              <button
                key={id}
                className={`studio-tool ${tool === id ? 'is-active' : ''} ${id === 'delete' ? 'is-destructive' : ''}`}
                aria-pressed={tool === id}
                onClick={() => changeTool(id)}
              >
                <Icon size={17} /><span>{label}</span><kbd>{key}</kbd>
              </button>
            ))}
          </div>
          <div className='studio-tool-note'>
            <b><CheckCircle2 size={14} /> Schematic only</b>
            <p>Moving a station changes its diagram position. It never changes latitude or longitude.</p>
          </div>
          <div className='studio-tool-legend'>
            <span><i className='legend-junction' /> Junction</span>
            <span><i className='legend-reservoir' /> Reservoir</span>
            <span><i className='legend-tank' /> Tank</span>
            <span><i className='legend-pipe' /> Pipe · undirected</span>
          </div>
        </aside>

        <section className={`studio-canvas-shell ${tool === 'pipe' ? 'tool-pipe' : ''} ${NODE_TYPES.includes(tool) ? 'tool-place' : ''}`}>
          <div className='studio-canvas-topline'>
            <div><span className='studio-canvas-dot' /> SCHEMATIC CANVAS <small>NOT TO SCALE</small></div>
            <div className='studio-canvas-controls'>
              <ToolbarButton icon={ZoomOut} label='Zoom out' onClick={() => flow.zoomOut({ duration: 160 })} />
              <span>{Math.round((draft.editor_layout.viewport.zoom || 1) * 100)}%</span>
              <ToolbarButton icon={ZoomIn} label='Zoom in' onClick={() => flow.zoomIn({ duration: 160 })} />
              <button className='studio-fit-button' onClick={() => flow.fitView({ padding: 0.18, duration: 300 })}>Fit</button>
            </div>
          </div>
          <div className='studio-flow-wrap'>
            {draft.network.nodes.length === 0 && (
              <div className='studio-welcome'>
                <div className='studio-welcome-icon'><GitBranch size={25} /></div>
                <span className='studio-eyebrow'>NETWORK STUDIO</span>
                <h1>Sketch a connected water network.</h1>
                <p>Start from a small Kengeri sample or build a blank schematic. Screen positions remain separate from geographic coordinates.</p>
                <div className='studio-welcome-actions'>
                  <button className='studio-text-button primary' onClick={loadDemo}><Waves size={16} /> Load Kengeri sample</button>
                  <button className='studio-text-button secondary' onClick={() => addNode('reservoir', { x: 280, y: 250 })}><FilePlus2 size={16} /> Add first source</button>
                </div>
                <small>Drafts save in this browser. You can also export or import JSON.</small>
              </div>
            )}
            <ReactFlow
              nodes={flowNodes}
              edges={edges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={(changes) => {
                changes.forEach((change) => {
                  if (change.type === 'select') setSelection(change.selected ? { type: 'pipe', id: change.id } : null);
                });
              }}
              onNodeClick={handleNodeClick}
              onNodeDragStart={startDrag}
              onNodeDragStop={finishDrag}
              onEdgeClick={handleEdgeClick}
              onPaneClick={onPaneClick}
              onPaneMouseMove={(event) => {
                if (pendingConnection) setCursor(flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
              }}
              onMoveEnd={onMoveEnd}
              onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => {
                if (selectedNodes.length) setSelection({ type: 'node', id: selectedNodes.at(-1).id });
                else if (selectedEdges.length) setSelection({ type: 'pipe', id: selectedEdges.at(-1).id });
                else setSelection(null);
              }}
              onPaneContextMenu={(event) => event.preventDefault()}
              fitView={false}
              defaultViewport={draft.editor_layout.viewport}
              minZoom={0.25}
              maxZoom={2}
              nodesDraggable={tool === 'select'}
              nodesConnectable={false}
              edgesReconnectable={false}
              elementsSelectable={tool === 'select' || tool === 'delete'}
              panOnDrag
              selectionOnDrag={false}
              deleteKeyCode={null}
              snapToGrid
              snapGrid={[20, 20]}
              onError={(code, message) => console.error('[RF ERROR]', code, message)}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1.15} color='#304260' />
              <FlowViewport draft={draft} viewVersion={viewVersion} />
            </ReactFlow>
            <div className='studio-canvas-hint'><MousePointer2 size={13} />{placeHint}</div>
          </div>
        </section>

        <PropertiesPanel draft={draft} selection={selection} onField={updateField} onRemove={requestDelete} />
      </section>

      <footer className={`studio-validation ${issuesOpen ? 'issues-expanded' : ''}`}>
        <button className='studio-validation-summary' onClick={() => setIssuesOpen((open) => !open)} aria-expanded={issuesOpen}>
          <span className='studio-valid-title'><AlertTriangle size={15} /> TOPOLOGY CHECK</span>
          <span className='studio-count'><b>{draft.network.nodes.length}</b> nodes</span>
          <span className='studio-count'><b>{draft.network.pipes.length}</b> pipes</span>
          <span className={`studio-issue-count ${validation.errors.length ? 'has-errors' : ''}`}><X size={13} /> {validation.errors.length} errors</span>
          <span className='studio-issue-count'><AlertTriangle size={13} /> {validation.warnings.length} warnings</span>
          <span className='studio-validation-spacer' />
          <span className='studio-hydraulic-note'><Check size={13} /> Topology only · no hydraulic calculation</span>
          <span className='studio-expand-indicator'>{issuesOpen ? 'Hide details' : 'Review'}</span>
        </button>
        {issuesOpen && (
          <div className='studio-issue-list'>
            {!allIssues.length ? (
              <div className='studio-no-issues'><CheckCircle2 size={15} /> No topology or property issues found.</div>
            ) : allIssues.slice(0, 12).map((issue, index) => (
              <button key={`${issue.objectType}-${issue.objectId}-${index}`} onClick={() => focusIssue(issue)}>
                {issue.severity === 'error' ? <X size={13} /> : <AlertTriangle size={13} />}
                <span>{issue.message}</span><b>{issue.objectId}</b>
              </button>
            ))}
          </div>
        )}
      </footer>

      {notice && (
        <div className='studio-toast' role='status'>
          <span>{notice}</span><button aria-label='Dismiss message' onClick={() => setNotice('')}><X size={14} /></button>
        </div>
      )}

      {pendingDelete && (
        <div className='studio-modal-backdrop' role='presentation' onMouseDown={(event) => event.target === event.currentTarget && setPendingDelete(null)}>
          <section className='studio-delete-modal' role='dialog' aria-modal='true' aria-labelledby='delete-title'>
            <div className='studio-modal-icon'><AlertTriangle size={19} /></div>
            <span className='studio-eyebrow'>CONFIRM DELETE</span>
            <h2 id='delete-title'>Delete {pendingDelete.id}?</h2>
            <p>{pendingDelete.type === 'node' && pendingDelete.connected.length
              ? `This will also remove ${pendingDelete.connected.length} connected pipe${pendingDelete.connected.length === 1 ? '' : 's'}. Undo can restore the complete topology.`
              : `This ${pendingDelete.type} will be removed from the schematic. Undo can restore it.`}</p>
            <div className='studio-modal-actions'>
              <button className='studio-text-button secondary' onClick={() => setPendingDelete(null)}>Cancel</button>
              <button className='studio-text-button danger' onClick={confirmDelete}><X size={15} /> Delete</button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

export default function NetworkStudio(props) {
  return (
    <ReactFlowProvider>
      <StudioWorkspace {...props} />
    </ReactFlowProvider>
  );
}
