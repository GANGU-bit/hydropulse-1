import { useMemo, useState } from 'react';
import {
  Activity,
  ChevronDown,
  Droplet,
  RotateCcw,
  Search,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { holeLabel } from '../lib/format.js';
export default function ControlPanel({
  network,
  presets,
  selected,
  setSelected,
  area,
  setArea,
  noise,
  setNoise,
  onSimulate,
  onIsolate,
  onReset,
  phase,
  judge,
  setJudge,
  onReveal,
  revealed,
  mode,
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  // The target picker intentionally offers junctions only, never reservoirs or tanks.
  const nodes = useMemo(
    () => network?.nodes?.filter((n) => n.type === 'junction') || [],
    [network],
  );
  const matches = nodes
    .filter((n) =>
      n.id.toLowerCase().includes((query || selected || '').toLowerCase()),
    )
    .slice(0, 7);
  return (
    <aside className='control-panel'>
      <div className='panel-title-row'>
        <div>
          <div className='eyebrow'>INCIDENT CONTROL</div>
          <h2>Response desk</h2>
        </div>
        <span className='section-number'>01</span>
      </div>
      <div className='section-label'>
        <span>QUICK SCENARIOS</span>
        <span className='small-muted'>SELECT TO RUN</span>
      </div>
      <div className='preset-list'>
        {presets.map((p, i) => (
          <button
            key={p.id}
            className='preset-card'
            disabled={!network || phase === 'simulating'}
            onClick={() => {
              // A preset updates the form and starts its run as one operator action.
              setSelected(p.target_node_id);
              setQuery('');
              setArea(p.leak_area_cm2);
              setNoise(p.noise_pct);
              onSimulate(
                {
                  target_node_id: p.target_node_id,
                  leak_area_cm2: p.leak_area_cm2,
                  noise_pct: p.noise_pct,
                  seed: null,
                },
                p.id,
              );
            }}
          >
            <span className={`preset-icon preset-${i}`}>
              <Zap size={15} />
            </span>
            <span className='preset-copy'>
              <b>{p.label}</b>
              <small>{p.description}</small>
            </span>
            <ChevronDown className='preset-arrow' size={15} />
          </button>
        ))}
      </div>
      <div className='form-section'>
        <div className='section-label'>
          <span>BURST PARAMETERS</span>
          <span className='live-tag'>
            <span />
            CONFIGURABLE
          </span>
        </div>
        <label className='field-label'>
          Target junction{' '}
          <span className='field-note'>
            {judge ? 'HIDDEN IN JUDGE MODE' : 'MAP SELECTABLE'}
          </span>
        </label>
        <div className='select-wrap'>
          <Search size={14} />
          <input
            aria-label='Search target junction'
            value={query || selected || ''}
            onChange={(e) => {
              const value = e.target.value.toUpperCase();
              setQuery(value);
              setSelected(value);
              setOpen(true);
            }}
            onFocus={(e) => {
              e.currentTarget.select();
              setOpen(true);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && matches[0]) {
                setSelected(matches[0].id);
                setQuery('');
                setOpen(false);
              }
              if (e.key === 'Escape') setOpen(false);
            }}
            placeholder='Search junction e.g. J15'
          />
          <button
            aria-label='Show junction list'
            onClick={() => setOpen(!open)}
          >
            <ChevronDown size={14} />
          </button>
          {open && (
            <div className='dropdown'>
              {matches.map((n) => (
                <button
                  key={n.id}
                  onClick={() => {
                    setSelected(n.id);
                    setQuery('');
                    setOpen(false);
                  }}
                >
                  <span>{n.id}</span>
                  <small>{n.households} households</small>
                </button>
              ))}
            </div>
          )}
        </div>
        <label className='field-label range-label'>
          Leak hole area{' '}
          <span className='mono value-label'>
            {Number(area).toFixed(1)} cm²
          </span>
        </label>
        <input
          aria-label='Leak hole area'
          className='range'
          type='range'
          min='0.5'
          max='100'
          step='0.5'
          value={area}
          onChange={(e) => setArea(Number(e.target.value))}
        />
        <div className='range-scale'>
          <span>0.5 cm²</span>
          <span>100 cm²</span>
        </div>
        <div className='helper'>
          <Droplet size={13} />
          {holeLabel(area)}
        </div>
        <label className='field-label range-label noise-label'>
          Sensor noise{' '}
          <span className='mono value-label'>{Number(noise).toFixed(1)}%</span>
        </label>
        <input
          aria-label='Sensor noise'
          className='range noise-range'
          type='range'
          min='0'
          max='10'
          step='0.1'
          value={noise}
          onChange={(e) => setNoise(Number(e.target.value))}
        />
        <div className='range-scale'>
          <span>0%</span>
          <span>10%</span>
        </div>
      </div>
      <div className='judge-row'>
        <span className='judge-icon'>
          <ShieldCheck size={15} />
        </span>
        <div>
          <b>
            Judge mode <small>BLIND</small>
          </b>
          <p>Hide the injected junction</p>
        </div>
        <button
          className={`switch ${judge ? 'on' : ''}`}
          role='switch'
          aria-checked={judge}
          aria-label='Toggle judge mode'
          onClick={() => {
            setJudge(!judge);
            onReveal(false);
          }}
        >
          <span />
        </button>
      </div>
      {judge && phase !== 'idle' && (
        <button className='reveal-button' onClick={() => onReveal(!revealed)}>
          {revealed ? 'Hide truth' : 'Reveal truth'}
        </button>
      )}
      <div className='action-row'>
        <button
          className='simulate-button'
          disabled={!network || phase === 'simulating'}
          onClick={() =>
            onSimulate(
              {
                target_node_id: selected || 'J15',
                leak_area_cm2: Number(area),
                noise_pct: Number(noise),
                seed: null,
              },
              null,
            )
          }
        >
          <Activity size={16} />
          {!network
            ? 'Connecting…'
            : phase === 'simulating'
              ? 'Scanning network…'
              : 'Simulate burst'}
          <kbd>SPACE</kbd>
        </button>
        <button
          className='reset-button'
          aria-label='Reset simulation'
          onClick={onReset}
        >
          <RotateCcw size={15} />
        </button>
      </div>
      <button
        className='isolate-button'
        disabled={!['located', 'isolated'].includes(phase)}
        onClick={onIsolate}
      >
        <ShieldCheck size={15} />
        Isolate valves <kbd>I</kbd>
      </button>
      <div className='panel-foot'>
        <span className={`api-connection ${mode}`}>
          <span />
          {mode === 'live'
            ? 'API connected'
            : mode === 'mock'
              ? 'Local simulation'
              : 'Recorded data active'}
        </span>
        <span>v1.0.4</span>
      </div>
    </aside>
  );
}
