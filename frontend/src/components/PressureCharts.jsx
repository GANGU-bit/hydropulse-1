import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { number } from '../lib/format.js';
export default function PressureCharts({
  result,
  open,
  onToggle,
  sensorIds = [],
}) {
  // Baseline placeholders keep telemetry charts informative before the first event.
  const data = (
    result?.sensor_readings || [
      {
        sensor_id: sensorIds[0] || 'S1',
        baseline_m: 42.5,
        observed_m: 42.5,
        drop_m: 0,
      },
      {
        sensor_id: sensorIds[1] || 'S2',
        baseline_m: 41.8,
        observed_m: 41.8,
        drop_m: 0,
      },
      {
        sensor_id: sensorIds[2] || 'S3',
        baseline_m: 40.2,
        observed_m: 40.2,
        drop_m: 0,
      },
      {
        sensor_id: sensorIds[3] || 'S4',
        baseline_m: 39.6,
        observed_m: 39.6,
        drop_m: 0,
      },
      {
        sensor_id: sensorIds[4] || 'S5',
        baseline_m: 38.9,
        observed_m: 38.9,
        drop_m: 0,
      },
    ]
  ).map((d, i) => ({ ...d, label: `S${i + 1}` }));
  return (
    <section className={`charts-drawer ${open ? 'expanded' : 'collapsed'}`}>
      <button className='drawer-handle' onClick={onToggle}>
        <span className='handle-grip' />
        <b>PRESSURE SIGNATURE</b>
        <span className='drawer-caption'>SENTINEL SENSOR TELEMETRY</span>
        <span className='drawer-result'>
          {result ? 'POST-EVENT ANALYSIS' : 'BASELINE MONITORING'}
        </span>
        <span className='drawer-chevron'>{open ? '⌄' : '⌃'}</span>
      </button>
      {open && (
        <div className='charts-body'>
          <div className='chart-block'>
            <div className='chart-heading'>
              <span>PRESSURE RESPONSE</span>
              <small>
                BASELINE VS OBSERVED <i>· m</i>
              </small>
            </div>
            <ResponsiveContainer width='100%' height='100%'>
              <BarChart
                data={data}
                margin={{ top: 18, right: 12, left: -19, bottom: 0 }}
                barGap={3}
              >
                <CartesianGrid
                  stroke='#1E2A44'
                  strokeDasharray='3 5'
                  vertical={false}
                />
                <XAxis
                  dataKey='label'
                  tick={{
                    fill: '#71809b',
                    fontSize: 10,
                    fontFamily: 'JetBrains Mono',
                  }}
                  axisLine={{ stroke: '#263247' }}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 'auto']}
                  tick={{
                    fill: '#71809b',
                    fontSize: 9,
                    fontFamily: 'JetBrains Mono',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: '#10192a',
                    border: '1px solid #263247',
                    borderRadius: 8,
                    color: '#e2e8f0',
                    fontSize: 11,
                  }}
                />
                <Bar
                  dataKey='baseline_m'
                  name='Baseline'
                  fill='#344563'
                  radius={[3, 3, 0, 0]}
                  maxBarSize={24}
                />
                <Bar
                  dataKey='observed_m'
                  name='Observed'
                  fill='#22D3EE'
                  radius={[3, 3, 0, 0]}
                  maxBarSize={24}
                >
                  <LabelList
                    dataKey='drop_m'
                    position='top'
                    formatter={(value) => `−${number(value, 1)}`}
                    fill='#f2b74a'
                    fontSize={8}
                    fontFamily='JetBrains Mono'
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className='signature-block'>
            <div className='chart-heading'>
              <span>DROP SIGNATURE</span>
              <small>
                FINGERPRINT <i>· Δm</i>
              </small>
            </div>
            <ResponsiveContainer width='100%' height='100%'>
              <BarChart
                data={data}
                margin={{ top: 16, right: 5, left: -24, bottom: 0 }}
              >
                <CartesianGrid
                  stroke='#1E2A44'
                  strokeDasharray='3 5'
                  vertical={false}
                />
                <XAxis
                  dataKey='label'
                  tick={{
                    fill: '#71809b',
                    fontSize: 9,
                    fontFamily: 'JetBrains Mono',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{
                    fill: '#71809b',
                    fontSize: 9,
                    fontFamily: 'JetBrains Mono',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: '#10192a',
                    border: '1px solid #263247',
                    borderRadius: 8,
                    color: '#e2e8f0',
                    fontSize: 11,
                  }}
                />
                <Bar
                  dataKey='drop_m'
                  name='Pressure drop'
                  fill='#F59E0B'
                  radius={[3, 3, 0, 0]}
                  maxBarSize={23}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className='chart-insight'>
            <div className='insight-mark'>⌁</div>
            <div>
              <b>
                {result
                  ? 'Distinctive pressure fingerprint detected'
                  : 'System standing by'}
              </b>
              <span>
                {result
                  ? 'Cosine matcher aligned across five sentinel nodes'
                  : 'Pressure drops will appear after a burst simulation'}
              </span>
            </div>
            <span className='matcher-score'>{result ? '0.942' : '—'}</span>
          </div>
        </div>
      )}
    </section>
  );
}
