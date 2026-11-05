import { useEffect, useState } from 'react';
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Droplet,
  MapPinned,
  Shield,
  Truck,
  Users,
  Wind,
} from 'lucide-react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { number } from '../lib/format.js';
function CountUp({ value, digits = 0 }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    let start = null;
    let raf;
    const step = (t) => {
      if (!start) start = t;
      const p = Math.min(1, (t - start) / 750);
      setN((Number(value) || 0) * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{number(n, digits)}</>;
}
export default function IncidentPanel({
  network,
  result,
  phase,
  onFocus,
  showTruth,
  mode,
}) {
  const nodes = network?.nodes || [];
  const pressures = nodes.map((n) => n.base_pressure_m || 0);
  const avg = pressures.reduce((a, b) => a + b, 0) / (pressures.length || 1);
  const households = nodes.reduce((a, n) => a + (n.households || 0), 0);
  const totalLoss = result?.actual_loss_lpm || 0;
  // Express daily loss as an equivalent number of 6,000 L tanker loads.
  const tankers = (totalLoss * 1440) / 6000;
  const supplied = Math.max(
    0,
    100 -
      ((result?.affected_households_after_isolation || 0) /
        Math.max(households, 1)) *
        100,
  );
  const pie = [
    { name: 'Supplied', value: supplied },
    { name: 'Affected', value: 100 - supplied },
  ];
  return (
    <aside className='incident-panel'>
      <div className='panel-title-row'>
        <div>
          <div className='eyebrow'>NETWORK INTELLIGENCE</div>
          <h2>Incident overview</h2>
        </div>
        <span className='live-indicator'>
          <i />
          {mode === 'live' ? 'LIVE' : mode === 'offline' ? 'CACHED' : 'MOCK'}
        </span>
      </div>
      {!result ? (
        <>
          <div className='health-card'>
            <div className='health-heading'>
              <div className='health-icon'>
                <Activity size={16} />
              </div>
              <div>
                <b>Network health</b>
                <small>ALL SYSTEMS NOMINAL</small>
              </div>
              <span className='health-ok'>
                <CheckCircle2 size={15} /> HEALTHY
              </span>
            </div>
            <div className='pressure-stats'>
              <div>
                <span>MIN PRESSURE</span>
                <b>
                  {number(Math.min(...pressures), 1)} <small>m</small>
                </b>
              </div>
              <div>
                <span>AVG PRESSURE</span>
                <b>
                  {number(avg, 1)} <small>m</small>
                </b>
              </div>
            </div>
            <div className='health-foot'>
              <span>
                <i />5 sentinel sensors online
              </span>
              <span>
                <Users size={12} />
                {number(households)} households
              </span>
            </div>
          </div>
          <div className='awaiting-card'>
            <div className='awaiting-icon'>
              <Wind size={21} />
            </div>
            <b>Awaiting pressure event</b>
            <p>
              Run a burst scenario to localize the leak and calculate an
              isolation plan.
            </p>
            <div className='awaiting-line'>
              <span />
              <span />
              <span />
              <span />
              <span />
            </div>
            <small>READY TO SIMULATE</small>
          </div>
          <div className='network-summary'>
            <div className='section-label'>
              <span>DMA SNAPSHOT</span>
              <span>KE-04</span>
            </div>
            <div className='summary-row'>
              <span>District metered area</span>
              <b>Kengeri West</b>
            </div>
            <div className='summary-row'>
              <span>Pressure monitoring</span>
              <b className='green'>5 / 5 sensors</b>
            </div>
            <div className='summary-row'>
              <span>Baseline pressure</span>
              <b>{number(avg, 1)} m</b>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className='result-headline'>
            <div className='result-kicker'>
              <span className='alert-pulse' />
              LEAK PINPOINTED
            </div>
            <h3>
              {result.predicted_top_candidates?.[0]?.node_id ||
                result.injected_node_id}
            </h3>
            <div className='confidence-row'>
              <span>LOCALIZATION CONFIDENCE</span>
              <b>
                {number(
                  result.predicted_top_candidates?.[0]?.confidence_pct || 0,
                  1,
                )}
                %
              </b>
            </div>
            <div className='confidence-track'>
              <span
                style={{
                  width: `${result.predicted_top_candidates?.[0]?.confidence_pct || 0}%`,
                }}
              />
            </div>
          </div>
          <div className='loss-card'>
            <div className='loss-icon'>
              <Droplet size={16} />
            </div>
            <div className='loss-copy'>
              <span>ESTIMATED WATER LOSS</span>
              <b>
                <CountUp value={totalLoss} digits={1} /> <small>LPM</small>
              </b>
            </div>
            <div className='loss-vrule' />
            <div className='loss-copy tanker-loss'>
              <span>TANKERS / DAY</span>
              <b>
                <Truck size={14} />
                <CountUp value={tankers} digits={1} />
              </b>
            </div>
          </div>
          <div className='candidate-card'>
            <div className='card-heading'>
              <span>RANKED CANDIDATES</span>
              <span>
                CONFIDENCE <ChevronRight size={12} />
              </span>
            </div>
            {(result.predicted_top_candidates || []).slice(0, 5).map((c, i) => (
              <button
                className={`candidate-row ${i === 0 ? 'first' : ''}`}
                key={c.node_id}
                onClick={() => onFocus(nodes.find((n) => n.id === c.node_id))}
              >
                <span className={`rank rank-${i + 1}`}>
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span className='candidate-node'>
                  {c.node_id}
                  <small>
                    {i === 0
                      ? 'Most likely leak point'
                      : `${number(c.estimated_leak_area_cm2 || 0, 1)} cm² · ${number(c.estimated_loss_lpm || 0, 0)} LPM`}
                  </small>
                </span>
                <span className='candidate-meter'>
                  <i style={{ width: `${c.confidence_pct}%` }} />
                </span>
                <b className='candidate-pct'>{number(c.confidence_pct, 1)}%</b>
                <ArrowUpRight size={12} className='candidate-arrow' />
              </button>
            ))}
          </div>
          <div className='isolation-card'>
            <div className='isolation-top'>
              <div className='valve-icon'>
                <Shield size={15} />
              </div>
              <div>
                <b>
                  {phase === 'isolated'
                    ? 'Isolation confirmed'
                    : 'Isolation plan ready'}
                </b>
                <small>
                  {result.recommended_valves_to_close?.length || 0} VALVES ·{' '}
                  {result.affected_households_after_isolation || 0} HOUSEHOLDS
                  IMPACTED
                </small>
              </div>
              <span className='isolation-state'>
                {phase === 'isolated' ? 'CLOSED' : 'READY'}
              </span>
            </div>
            <div className='isolation-bottom'>
              <div className='valve-list'>
                {(result.recommended_valves_to_close || []).map((id) => (
                  <span key={id}>{id}</span>
                ))}
              </div>
              <div className='supply-chart'>
                <ResponsiveContainer width={54} height={54}>
                  <PieChart>
                    <Pie
                      data={pie}
                      dataKey='value'
                      innerRadius={19}
                      outerRadius={25}
                      startAngle={90}
                      endAngle={-270}
                      stroke='none'
                    >
                      <Cell fill='#10B981' />
                      <Cell fill='#263247' />
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <span>{number(supplied, 1)}%</span>
              </div>
              <div className='supplied-label'>
                WARD STILL
                <br />
                <b>SUPPLIED</b>
              </div>
            </div>
          </div>
          <div className='result-meta'>
            <span>
              <MapPinned size={12} />{' '}
              {showTruth
                ? `${number(result.localization_error_m || 0, 1)} m error`
                : 'Truth hidden'}
            </span>
            <span>
              <Activity size={12} /> {number(result.compute_time_ms, 0)} ms
            </span>
            <span>
              <Droplet size={12} />
              {number(result.injected_leak_area_cm2, 1)} cm²
            </span>
          </div>
        </>
      )}
    </aside>
  );
}
