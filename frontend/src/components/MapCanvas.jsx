import { memo, useEffect, useMemo, useState } from 'react';
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet';
import L from 'leaflet';
import { Crosshair, Layers2, MapPin, Waves } from 'lucide-react';
const reservoirIcon = L.divIcon({
  className: 'reservoir-marker',
  html: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.4c-1.7 3.1-6.2 7.7-6.2 12.2a6.2 6.2 0 0 0 12.4 0c0-4.5-4.5-9.1-6.2-12.2Z" fill="none" stroke="#79b6ff" stroke-width="1.8"/><path d="M8.6 14.2a3.5 3.5 0 0 0 3.3 3.2" fill="none" stroke="#79b6ff" stroke-width="1.4" stroke-linecap="round"/></svg>',
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});
const sensorPulseIcon = L.divIcon({
  className: 'sensor-pulse-marker',
  html: '<span aria-hidden="true"></span>',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
});
const leakRadarIcon = L.divIcon({
  className: 'leak-radar-marker',
  html: '<span aria-hidden="true"></span>',
  iconSize: [34, 34],
  iconAnchor: [17, 17],
});
function FlyTo({ node }) {
  const map = useMap();
  useEffect(() => {
    if (node) map.flyTo([node.lat, node.lng], 17, { duration: 0.65 });
  }, [node, map]);
  return null;
}
function Recenter({ center, revision }) {
  const map = useMap();
  useEffect(() => {
    if (center && revision > 0) map.flyTo([center.lat, center.lng], 16, { duration: 0.55 });
  }, [center, revision, map]);
  return null;
}
function ScanOverlay({ active }) {
  return active ? (
    <div className='scan-overlay'>
      <div className='scan-beam' />
      <div className='scan-caption'>
        <span className='scan-spinner' /> PRESSURE SIGNATURE ANALYSIS{' '}
        <b>IN PROGRESS</b>
      </div>
    </div>
  ) : null;
}
function isProviderErrorTile(tile) {
  try {
    // Some providers return an access-denied image with HTTP 200; detect its uniform watermark pixels.
    const canvas = document.createElement('canvas');
    canvas.width = tile.naturalWidth;
    canvas.height = tile.naturalHeight;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(tile, 0, 0);
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let uniform = 0,
      total = 0;
    for (let y = 12; y < canvas.height; y += 24)
      for (let x = 12; x < canvas.width; x += 24) {
        const i = (y * canvas.width + x) * 4,
          r = pixels[i],
          g = pixels[i + 1],
          b = pixels[i + 2];
        if (
          Math.abs(r - 38) < 2 &&
          Math.abs(g - 38) < 2 &&
          Math.abs(b - 38) < 2
        )
          uniform++;
        total++;
      }
    return uniform / total > 0.72;
  } catch {
    return true;
  }
}
function MapCanvas({
  network,
  networkSource,
  phase,
  result,
  selected,
  setSelected,
  judge,
  revealed,
  mode,
  onPick,
  focusNode,
}) {
  const [basemap, setBasemap] = useState(true),
    [tilesFailed, setTilesFailed] = useState(false);
  const [recenterRevision, setRecenterRevision] = useState(0);
  const [valvePulse, setValvePulse] = useState(false);
  // Index nodes once because every pipe and overlay resolves endpoints by id.
  const byId = useMemo(
    () => Object.fromEntries((network?.nodes || []).map((n) => [n.id, n])),
    [network],
  );
  const valving = useMemo(
    () =>
      new Set(
        phase === 'isolated' ? result?.recommended_valves_to_close || [] : [],
      ),
    [phase, result],
  );
  const isolated = useMemo(
    () => new Set(phase === 'isolated' ? result?.isolated_node_ids || [] : []),
    [phase, result],
  );
  const sensors = useMemo(
    () =>
      new Map((network?.sensor_ids || []).map((id, index) => [id, index + 1])),
    [network],
  );
  const top = result?.predicted_top_candidates || [];
  const heat = result?.node_heatmap || {};
  useEffect(() => {
    // Canvas-rendered valve paths ignore CSS animation, so pulse their opacity in React instead.
    if (
      phase !== 'isolated' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      setValvePulse(false);
      return undefined;
    }
    const timer = setInterval(() => setValvePulse((pulse) => !pulse), 520);
    return () => clearInterval(timer);
  }, [phase]);
  const fallback = !basemap || tilesFailed;
  return (
    <section className={`map-canvas ${fallback ? 'no-tiles' : ''}`}>
      <MapContainer
        center={[
          network?.center?.lat || 12.9081,
          network?.center?.lng || 77.4852,
        ]}
        zoom={16}
        zoomControl={false}
        preferCanvas
      >
        <TileLayer
          crossOrigin='anonymous'
          url='https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
          attribution='&copy; OpenStreetMap &copy; CARTO'
          eventHandlers={{
            // Fall back for both tile network errors and provider error images.
            tileerror: () => setTilesFailed(true),
            tileload: (e) => {
              if (isProviderErrorTile(e.tile)) setTilesFailed(true);
            },
          }}
          opacity={basemap && !tilesFailed ? 1 : 0}
        />
        <FlyTo node={focusNode} />
        <Recenter center={network?.center} revision={recenterRevision} />
        {(network?.pipes || []).map((p) => {
          const a = byId[p.source],
            b = byId[p.target];
          if (!a || !b) return null;
          const active = valving.has(p.id);
          const width =
            p.diameter_mm >= 400 ? 5 : p.diameter_mm >= 250 ? 3 : 1.5;
          return (
            <Polyline
              key={p.id}
              positions={[
                [a.lat, a.lng],
                [b.lat, b.lng],
              ]}
              pathOptions={{
                color: active ? '#F59E0B' : '#22D3EE',
                weight: width,
                opacity: active ? (valvePulse ? 0.42 : 1) : 0.72,
                dashArray: active ? '8 8' : null,
                className: active ? 'valve-flash' : '',
              }}
            >
              <Tooltip sticky>
                <b>{p.id}</b> · {p.diameter_mm} mm
                <br />
                {p.length_m} m · {p.flow_m3h} m³/h
                <br />
                {p.road_class}
              </Tooltip>
            </Polyline>
          );
        })}
        {Object.entries(heat).map(([id, value]) => {
          const n = byId[id];
          if (!n || value < 0.08) return null;
          const col =
            value > 0.72 ? '#EF4444' : value > 0.38 ? '#F59E0B' : '#1E3A8A';
          return (
            <CircleMarker
              key={`h${id}`}
              center={[n.lat, n.lng]}
              radius={4 + value * 10}
              pathOptions={{
                color: col,
                fillColor: col,
                fillOpacity: 0.12 + value * 0.3,
                weight: 1,
                opacity: 0.5,
              }}
            />
          );
        })}
        {/* HTML markers preserve CSS pulse animation when the network uses Leaflet canvas rendering. */}
        {(network?.nodes || []).filter((node) => sensors.has(node.id) || node.is_sensor).map((node) => (
          <Marker
            key={`sensor-pulse-${node.id}`}
            position={[node.lat, node.lng]}
            icon={sensorPulseIcon}
            interactive={false}
          />
        ))}
        {(network?.nodes || []).map((n) => {
          const sensor = sensors.has(n.id) || n.is_sensor;
          const topRank = top.findIndex((c) => c.node_id === n.id);
          const truth = judge && revealed && result?.injected_node_id === n.id;
          const hiddenTarget = judge && phase === 'idle' && selected === n.id;
          const picked = selected === n.id && !judge;
          const prob = Number(heat[n.id] || n.leak_probability || 0);
          const fill = isolated.has(n.id)
            ? '#64748B'
            : sensor
              ? '#10B981'
              : prob > 0.6
                ? '#EF4444'
                : '#8EA1BD';
          return (
            <CircleMarker
              key={n.id}
              center={[n.lat, n.lng]}
              radius={sensor ? 5.8 : topRank === 0 ? 9 : topRank > 0 ? 7 : 3.4}
              pathOptions={{
                color: truth ? '#fff' : picked ? '#fff' : fill,
                fillColor: hiddenTarget ? '#0D1424' : fill,
                fillOpacity: hiddenTarget ? 0.35 : 0.95,
                weight: truth || picked ? 2.5 : 1,
                opacity: isolated.has(n.id) ? 0.45 : 1,
                className: sensor ? 'sensor-core' : undefined,
              }}
              eventHandlers={{
                click: () => {
                  setSelected(n.id);
                  onPick?.(n);
                },
              }}
            >
              <Tooltip
                permanent={sensor || (result && topRank >= 0 && topRank < 5)}
                direction={topRank === 0 ? 'top' : sensor ? 'right' : 'top'}
                offset={topRank === 0 ? [0, -10] : sensor ? [8, 0] : [0, -8]}
                className={topRank === 0 ? 'leak-chip' : topRank > 0 && topRank < 5 ? 'rank-chip' : sensor ? 'sensor-chip' : ''}
              >
                {topRank === 0 ? `LEAK · ${n.id} · ${top[0].confidence_pct.toFixed(1)}%` : topRank > 0 && topRank < 5 ? String(topRank + 1) : sensor ? `S${sensors.get(n.id) || 1}` : n.id}
                {!sensor && !result ? <><br />{n.base_pressure_m?.toFixed(1)} m · {n.households} households</> : null}
              </Tooltip>
            </CircleMarker>
          );
        })}
        {/* Keep the revealed injected node distinct from its prediction rank marker. */}
        {judge && revealed && result?.injected_node_id && byId[result.injected_node_id] && (
          <CircleMarker
            center={[byId[result.injected_node_id].lat, byId[result.injected_node_id].lng]}
            radius={13}
            pathOptions={{ color: '#FFFFFF', fillOpacity: 0, weight: 2, dashArray: '5 4', className: 'truth-ring' }}
          >
            <Tooltip permanent direction='bottom' className='truth-chip'>INJECTED NODE</Tooltip>
          </CircleMarker>
        )}
        {result?.predicted_top_candidates?.[0] && byId[result.predicted_top_candidates[0].node_id] && (
          <Marker
            position={[byId[result.predicted_top_candidates[0].node_id].lat, byId[result.predicted_top_candidates[0].node_id].lng]}
            icon={leakRadarIcon}
            interactive={false}
          />
        )}
        {(network?.nodes || [])
          .filter((n) => n.type === 'reservoir' || n.type === 'tank')
          .map((n) => (
            <Marker
              key={`r${n.id}`}
              position={[n.lat, n.lng]}
              icon={reservoirIcon}
            >
              <Tooltip>
                {n.type.toUpperCase()} · {n.id}
              </Tooltip>
            </Marker>
          ))}
      </MapContainer>
      <div className='map-grid' />
      <ScanOverlay active={phase === 'simulating'} />
      <div className='map-toolbar'>
        <button
          className={`map-toggle ${basemap && !tilesFailed ? 'active' : ''}`}
          onClick={() => {
            setBasemap(tilesFailed ? true : !basemap);
            setTilesFailed(false);
          }}
          aria-label='Toggle basemap'
        >
          <Layers2 size={14} />
          Basemap <b>{basemap && !tilesFailed ? 'On' : 'Off'}</b>
        </button>
        <button
          className='map-toggle'
          aria-label='Center map'
          onClick={() => {
            setSelected(null);
            setRecenterRevision((revision) => revision + 1);
          }}
        >
          <Crosshair size={14} />
        </button>
      </div>
      {fallback && (
        <div className='map-fallback'>
          {tilesFailed
            ? 'BASEMAP UNAVAILABLE · LOCAL GRID'
            : 'LOCAL GRID · BASEMAP OFF'}
        </div>
      )}
      <div className='map-coordinate'>
        <MapPin size={12} /> KENGERI, BENGALURU <span>·</span> DMA 04
      </div>
      <div className='map-legend'>
        <div className='legend-title'>
          NETWORK LAYERS{' '}
          <span>● {mode === 'live' ? 'LIVE' : mode === 'offline' ? 'CACHED' : 'MOCK'}</span>
        </div>
        <div className='legend-item'>
          <i className='legend-pipe' />
          Active main
        </div>
        <div className='legend-item'>
          <i className='legend-sensor' />
          Sentinel sensor
        </div>
        <div className='legend-item'>
          <i className='legend-leak' />
          Leak probability
        </div>
        <div className='legend-item'>
          <i className='legend-valve' />
          Valve to isolate
        </div>
        <div className='legend-divider' />
        <div className='legend-note'>
          <Waves size={12} /> Network pressure monitored
        </div>
      </div>
      <div className='map-scale'>
        250 m <span />
      </div>
      <div className='map-status'>
        <span />
        NETWORK SYNCHRONIZED <b>·</b>{' '}
        {mode === 'live'
          ? 'REAL-TIME TELEMETRY'
          : networkSource === 'live'
            ? 'LAST LIVE SNAPSHOT'
            : networkSource === 'recorded'
            ? 'RECORDED FIXTURE'
            : 'SYNTHETIC DEMO NETWORK'}
      </div>
    </section>
  );
}
export default memo(MapCanvas);
