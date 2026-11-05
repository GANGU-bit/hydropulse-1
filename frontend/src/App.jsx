import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  X,
} from 'lucide-react';
import Header from './components/Header.jsx';
import ControlPanel from './components/ControlPanel.jsx';
import MapCanvas from './components/MapCanvas.jsx';
import IncidentPanel from './components/IncidentPanel.jsx';
import PressureCharts from './components/PressureCharts.jsx';
import RoiPanel from './components/RoiPanel.jsx';
import StatusBanner from './components/StatusBanner.jsx';
import NetworkStudio from './pages/NetworkStudio.jsx';
import { useHydroPulse } from './hooks/useHydroPulse.js';

export default function App() {
  const pulse = useHydroPulse();
  const {
    network,
    networkSource,
    presets,
    health,
    mode,
    phase,
    lastResult,
    error,
    load,
    simulate,
    isolate,
    reset,
  } = pulse;
  const [selected, setSelected] = useState('J15'),
    [area, setArea] = useState(25),
    [noise, setNoise] = useState(1.5),
    [judge, setJudge] = useState(false),
    [revealed, setRevealed] = useState(false),
    [chartsOpen, setChartsOpen] = useState(true),
    [help, setHelp] = useState(false),
    [roi, setRoi] = useState(false),
    [focusNode, setFocusNode] = useState(null),
    [toast, setToast] = useState('');
  const [studioOpen, setStudioOpen] = useState(false);
  const run = useCallback(
    async (body, preset) => {
      const r = await simulate(body, preset);
      if (r) {
        setRevealed(false);
        setFocusNode(
          network?.nodes?.find(
            (n) => n.id === r.predicted_top_candidates?.[0]?.node_id,
          ) || null,
        );
      }
    },
    [simulate, network],
  );
  useEffect(() => {
    if (lastResult?.actual_loss_lpm)
      setArea(lastResult.injected_leak_area_cm2 || 25);
  }, [lastResult]);
  useEffect(() => {
    if (error) {
      setToast(error);
      const id = setTimeout(() => setToast(''), 6000);
      return () => clearTimeout(id);
    }
  }, [error]);
  useEffect(() => {
    if (studioOpen) return undefined;
    // Ignore global shortcuts while the operator is typing or editing a control.
    const fn = (e) => {
      const target = e.target;
      if (
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName) ||
        target?.isContentEditable
      )
        return;
      const k = e.key.toLowerCase();
      if (k === '?' || k === '/') {
        e.preventDefault();
        setHelp((x) => !x);
      } else if (k === '1' || k === '2' || k === '3') {
        const p = presets[Number(k) - 1];
        if (p)
          run(
            {
              target_node_id: p.target_node_id,
              leak_area_cm2: p.leak_area_cm2,
              noise_pct: p.noise_pct,
              seed: null,
            },
            p.id,
          );
      } else if (k === ' ') {
        e.preventDefault();
        run(
          {
            target_node_id: selected || 'J15',
            leak_area_cm2: Number(area),
            noise_pct: Number(noise),
            seed: null,
          },
          null,
        );
      } else if (k === 'i' && lastResult) isolate();
      else if (k === 'r') reset();
      else if (k === 'j') setJudge((v) => !v);
      else if (k === 'v' && judge) setRevealed((v) => !v);
      else if (k === 'n') setStudioOpen(true);
      else if (k === 'b') document.querySelector('.map-toggle')?.click();
      else if (k === 'f') {
        if (!document.fullscreenElement)
          document.documentElement.requestFullscreen?.();
        else document.exitFullscreen?.();
      }
    };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [presets, run, selected, area, noise, lastResult, isolate, reset, judge, studioOpen]);
  const truth = lastResult?.injected_node_id;
  const candidate = lastResult?.predicted_top_candidates?.[0]?.node_id;
  // Older backend responses may omit localization_error_m, so derive distance from node coordinates.
  const distance =
    network?.nodes && truth && candidate
      ? (() => {
          const a = network.nodes.find((n) => n.id === truth),
            b = network.nodes.find((n) => n.id === candidate);
          return a && b
            ? Math.hypot((a.lat - b.lat) * 111000, (a.lng - b.lng) * 95000)
            : 0;
        })()
      : 0;
  const showTruth = !judge || revealed;
  const errorMeters = lastResult?.localization_error_m ?? distance;
  return (
    studioOpen ? (
      <NetworkStudio
        initialNetwork={network}
        networkSource={networkSource}
        onExit={() => setStudioOpen(false)}
      />
    ) : (
    <main className='app-shell'>
      <Header
        mode={mode}
        health={health}
        onHelp={() => setHelp(true)}
        onRoi={() => setRoi(true)}
        onStudio={() => setStudioOpen(true)}
      />
      <StatusBanner mode={mode} networkSource={networkSource} onRetry={load} />
      <div className='workspace'>
        <ControlPanel
          network={network}
          presets={presets}
          selected={selected}
          setSelected={setSelected}
          area={area}
          setArea={setArea}
          noise={noise}
          setNoise={setNoise}
          onSimulate={run}
          onIsolate={isolate}
          onReset={() => {
            reset();
            setFocusNode(null);
            setRevealed(false);
          }}
          phase={phase}
          judge={judge}
          setJudge={setJudge}
          onReveal={setRevealed}
          revealed={revealed}
          mode={mode}
        />
        <MapCanvas
          network={network}
          networkSource={networkSource}
          phase={phase}
          result={lastResult}
          selected={selected}
          setSelected={setSelected}
          judge={judge}
          revealed={revealed}
          mode={mode}
          onPick={(n) => {
            if (!judge) setSelected(n.id);
          }}
          focusNode={focusNode}
        />
        <IncidentPanel
          network={network}
          result={lastResult}
          phase={phase}
          showTruth={showTruth}
          mode={mode}
          onFocus={(n) => setFocusNode(n)}
        />
        <PressureCharts
          result={lastResult}
          open={chartsOpen}
          onToggle={() => setChartsOpen((v) => !v)}
          sensorIds={network?.sensor_ids || []}
        />
      </div>
      {revealed && lastResult && (
        <div className='reveal-banner'>
          <span className='truth-dot' />
          <b>TRUTH REVEALED</b>
          <span>
            Injected at <strong>{truth}</strong>
          </span>
          <i>
            Localization error{' '}
            <strong>{Number(errorMeters).toFixed(1)} m</strong>
          </i>
          <button onClick={() => setRevealed(false)} aria-label='Hide truth'>
            <X size={14} />
          </button>
        </div>
      )}
      {toast && (
        <div className='toast'>
          <AlertTriangle size={15} />
          {toast}
          <button onClick={() => setToast('')}>
            <X size={14} />
          </button>
        </div>
      )}
      <RoiPanel
        open={roi}
        onClose={() => setRoi(false)}
        lastResult={lastResult}
      />
      {help && (
        <div className='modal-backdrop' onClick={() => setHelp(false)}>
          <section className='help-modal' onClick={(e) => e.stopPropagation()}>
            <div className='modal-heading'>
              <div>
                <div className='eyebrow'>STAGE CONTROL</div>
                <h2>Keyboard shortcuts</h2>
              </div>
              <button
                className='icon-btn'
                onClick={() => setHelp(false)}
                aria-label='Close shortcuts'
              >
                <X size={17} />
              </button>
            </div>
            <div className='shortcut-grid'>
              {[
                ['1 / 2 / 3', 'Run scenario'],
                ['Space', 'Simulate burst'],
                ['I', 'Isolate valves'],
                ['R', 'Reset incident'],
                ['J', 'Toggle judge mode'],
                ['V', 'Reveal truth'],
                ['N', 'Network Studio'],
                ['B', 'Toggle basemap'],
                ['F', 'Fullscreen'],
                ['?', 'Show shortcuts'],
              ].map(([key, label]) => (
                <div key={key}>
                  <kbd>{key}</kbd>
                  <span>{label}</span>
                </div>
              ))}
            </div>
            <p>Shortcuts pause while a text field is focused.</p>
          </section>
        </div>
      )}
    </main>
    )
  );
}
