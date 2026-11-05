import {
  AlertTriangle,
  Calendar,
  Compass,
  Database,
  Droplet,
  ExternalLink,
  Layers,
  MapPin,
  Ruler,
  TrendingDown,
  TrendingUp,
  Wind,
  X,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { TIME_SERIES_DATA } from '../lib/realWorldData.js';

export default function FeatureInspectorPanel({ feature, onClose }) {
  if (!feature) return null;

  const { id, assetType, name, properties = {}, provenance = {} } = feature;
  const timeSeries = TIME_SERIES_DATA[id] || null;

  return (
    <aside className='feature-inspector-pane clay-panel' aria-label='Feature Inspector'>
      <div className='inspector-header'>
        <div className='inspector-title-block'>
          <span className='studio-eyebrow'>FEATURE INSPECTOR</span>
          <h3>{name || id}</h3>
          <span className='inspector-asset-type'>
            {assetType.replace(/_/g, ' ').toUpperCase()}
          </span>
        </div>
        <button className='icon-btn' onClick={onClose} aria-label='Close Inspector'>
          <X size={16} />
        </button>
      </div>

      <div className='inspector-content'>
        {/* Provenance Banner */}
        <div className='inspector-provenance-box'>
          <div className='provenance-row'>
            <span>DATA STATUS</span>
            <span className='provenance-pill' data-status={provenance.dataStatus || 'source_mapped'}>
              {(provenance.dataStatus || 'source_mapped').replace('_', ' ').toUpperCase()}
            </span>
          </div>
          <div className='provenance-row'>
            <span>CONFIDENCE</span>
            <span className={`confidence-pill conf-${provenance.confidenceLevel || 'medium'}`}>
              {(provenance.confidenceLevel || 'medium').toUpperCase()}
            </span>
          </div>
          <div className='provenance-row'>
            <span>SOURCE DATASET</span>
            <b title={provenance.datasetId}>{provenance.datasetId || 'BWSSB Public GIS'}</b>
          </div>
          {provenance.sourceFeatureId && (
            <div className='provenance-row'>
              <span>SOURCE ASSET ID</span>
              <code>{provenance.sourceFeatureId}</code>
            </div>
          )}
        </div>

        {/* Engineering Properties */}
        <div className='inspector-section'>
          <span className='studio-group-title'>ENGINEERING ATTRIBUTES</span>
          <div className='inspector-attr-grid'>
            {properties.diameter_mm != null && (
              <div className='attr-item'>
                <span>DIAMETER</span>
                <b>{properties.diameter_mm} mm</b>
              </div>
            )}
            {properties.nominal_diameter_mm != null && (
              <div className='attr-item'>
                <span>NOMINAL BORE</span>
                <b>{properties.nominal_diameter_mm} mm</b>
              </div>
            )}
            {properties.length_m != null && (
              <div className='attr-item'>
                <span>LENGTH</span>
                <b>{properties.length_m} m</b>
              </div>
            )}
            {properties.material && (
              <div className='attr-item'>
                <span>MATERIAL</span>
                <b>{properties.material}</b>
              </div>
            )}
            {properties.valve_type && (
              <div className='attr-item'>
                <span>VALVE TYPE</span>
                <b>{properties.valve_type}</b>
              </div>
            )}
            {properties.operational_status && (
              <div className='attr-item'>
                <span>OPERATIONAL STATUS</span>
                <b>{properties.operational_status}</b>
              </div>
            )}
            {properties.installation_year && (
              <div className='attr-item'>
                <span>INSTALLATION</span>
                <b>{properties.installation_year}</b>
              </div>
            )}
            {properties.installed_capacity_mld != null && (
              <div className='attr-item'>
                <span>CAPACITY</span>
                <b>{properties.installed_capacity_mld} MLD</b>
              </div>
            )}
            {properties.water_level_mbgl != null && (
              <div className='attr-item'>
                <span>WATER TABLE</span>
                <b>{properties.water_level_mbgl} m bgl</b>
              </div>
            )}
            {properties.yield_lpm != null && (
              <div className='attr-item'>
                <span>YIELD</span>
                <b>{properties.yield_lpm} LPM</b>
              </div>
            )}
            {properties.effluent_bod_mg_l != null && (
              <div className='attr-item'>
                <span>EFFLUENT BOD</span>
                <b>{properties.effluent_bod_mg_l} mg/L</b>
              </div>
            )}
          </div>
        </div>

        {/* Depth / Elevation Caveat */}
        <div className='depth-caveat-box'>
          <AlertTriangle size={14} />
          <div>
            <b>Underground Depth:</b>
            <p>
              {properties.depth_label ||
                'Pipe invert depth not provided by utility GIS. Mapped illustratively at -2.5m nominal depth.'}
            </p>
          </div>
        </div>

        {/* Time-Series Charts (for Monitors) */}
        {timeSeries && (
          <div className='inspector-section'>
            <span className='studio-group-title'>
              MEASURED OBSERVATIONS & TELEMETRY
            </span>
            <div className='inspector-chart-wrap'>
              {assetType === 'groundwater_monitor' && (
                <>
                  <div className='chart-label-row'>
                    <span>Depth to Water Table (m below ground level)</span>
                    <b>GW-KA-BNG-KENGERI-01</b>
                  </div>
                  <ResponsiveContainer width='100%' height={160}>
                    <AreaChart data={timeSeries}>
                      <CartesianGrid strokeDasharray='3 3' stroke='#20324c' />
                      <XAxis dataKey='timestamp' stroke='#64748B' fontSize={10} />
                      <YAxis stroke='#64748B' fontSize={10} domain={[40, 48]} reversed />
                      <Tooltip
                        contentStyle={{ background: '#0D1424', borderColor: '#1E2A44', color: '#E2E8F0' }}
                        formatter={(val) => [`${val} m bgl`, 'Depth']}
                      />
                      <Area
                        type='monotone'
                        dataKey='value'
                        stroke='#22D3EE'
                        fill='#22D3EE22'
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </>
              )}

              {assetType === 'water_quality_monitor' && (
                <>
                  <div className='chart-label-row'>
                    <span>Dissolved Oxygen (DO) vs BOD (mg/L)</span>
                    <b>Dubasipalya Lake</b>
                  </div>
                  <ResponsiveContainer width='100%' height={160}>
                    <BarChart data={timeSeries}>
                      <CartesianGrid strokeDasharray='3 3' stroke='#20324c' />
                      <XAxis dataKey='timestamp' stroke='#64748B' fontSize={10} />
                      <YAxis stroke='#64748B' fontSize={10} />
                      <Tooltip contentStyle={{ background: '#0D1424', borderColor: '#1E2A44', color: '#E2E8F0' }} />
                      <Bar dataKey='do' name='DO (mg/L)' fill='#10B981' />
                      <Bar dataKey='bod' name='BOD (mg/L)' fill='#EF4444' />
                    </BarChart>
                  </ResponsiveContainer>
                </>
              )}

              {assetType === 'rain_gauge' && (
                <>
                  <div className='chart-label-row'>
                    <span>Monthly Precipitation (mm)</span>
                    <b>KSNDMC Kengeri</b>
                  </div>
                  <ResponsiveContainer width='100%' height={160}>
                    <BarChart data={timeSeries}>
                      <CartesianGrid strokeDasharray='3 3' stroke='#20324c' />
                      <XAxis dataKey='month' stroke='#64748B' fontSize={10} />
                      <YAxis stroke='#64748B' fontSize={10} />
                      <Tooltip contentStyle={{ background: '#0D1424', borderColor: '#1E2A44', color: '#E2E8F0' }} />
                      <Bar dataKey='rainfall_mm' name='Rainfall (mm)' fill='#3B82F6' />
                    </BarChart>
                  </ResponsiveContainer>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
