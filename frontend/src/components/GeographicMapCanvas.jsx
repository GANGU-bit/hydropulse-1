import { useEffect, useRef, useState } from 'react';
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polygon,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet';
import L from 'leaflet';
import {
  AlertTriangle,
  Compass,
  Database,
  Eye,
  EyeOff,
  Filter,
  Info,
  Layers,
  MapPin,
  Maximize2,
  Sliders,
} from 'lucide-react';
import { KENGERI_AOI, REAL_WORLD_FEATURES } from '../lib/realWorldData.js';

// Custom Map Bounds Fitter
function MapBoundsManager({ center, zoom }) {
  const map = useMap();
  useEffect(() => {
    map.setView([center.lat, center.lng], zoom);
  }, [map, center, zoom]);
  return null;
}

export default function GeographicMapCanvas({
  selectedFeature,
  onSelectFeature,
  visibleLayers = {},
  onToggleLayer,
  stagedLayers = [],
}) {
  const [basemapOn, setBasemapOn] = useState(true);
  const [tileError, setTileError] = useState(false);
  const [layersOpen, setLayersOpen] = useState(true);

  return (
    <div className='geo-map-wrapper'>
      <MapContainer
        center={[KENGERI_AOI.center.lat, KENGERI_AOI.center.lng]}
        zoom={KENGERI_AOI.zoom}
        className='geo-leaflet-map'
        zoomControl={false}
        preferCanvas={true}
        attributionControl={false}
      >
        <MapBoundsManager center={KENGERI_AOI.center} zoom={KENGERI_AOI.zoom} />

        {/* Basemap Tiles with Offline Grid Fallback */}
        {basemapOn && !tileError && (
          <TileLayer
            url='https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
            subdomains='abcd'
            maxZoom={19}
            eventHandlers={{
              tileerror: () => setTileError(true),
            }}
          />
        )}

        {/* 1. Administrative Boundary (Ward 159) */}
        {(visibleLayers.admin_boundary ?? true) &&
          REAL_WORLD_FEATURES.boundaries.map((b) => (
            <Polygon
              key={b.id}
              positions={b.coordinates}
              pathOptions={{
                color: '#64748B',
                weight: 2,
                dashArray: '6 6',
                fillColor: '#3B82F6',
                fillOpacity: 0.04,
              }}
              eventHandlers={{
                click: () => onSelectFeature(b),
              }}
            >
              <Tooltip sticky>{b.name}</Tooltip>
            </Polygon>
          ))}

        {/* 2. Water Bodies (Kengeri Lake & Vrishabhavathi Channel) */}
        {(visibleLayers.surface_water ?? true) &&
          REAL_WORLD_FEATURES.waterBodies.map((wb) => {
            if (wb.type === 'polygon') {
              return (
                <Polygon
                  key={wb.id}
                  positions={wb.coordinates}
                  pathOptions={{
                    color: '#38BDF8',
                    weight: 2,
                    fillColor: '#0284C7',
                    fillOpacity: 0.45,
                  }}
                  eventHandlers={{ click: () => onSelectFeature(wb) }}
                >
                  <Tooltip sticky>{wb.name} (Rejuvenated 2021)</Tooltip>
                </Polygon>
              );
            }
            return (
              <Polyline
                key={wb.id}
                positions={wb.coordinates}
                pathOptions={{
                  color: '#38BDF8',
                  weight: 5,
                  opacity: 0.8,
                }}
                eventHandlers={{ click: () => onSelectFeature(wb) }}
              >
                <Tooltip sticky>{wb.name}</Tooltip>
              </Polyline>
            );
          })}

        {/* 3. Sewerage Lines (Violet, Strictly Non-Potable) */}
        {(visibleLayers.sewerage ?? true) &&
          REAL_WORLD_FEATURES.sewerPipes.map((pipe) => {
            const isSelected = selectedFeature?.id === pipe.id;
            return (
              <Polyline
                key={pipe.id}
                positions={pipe.coordinates}
                pathOptions={{
                  color: isSelected ? '#F59E0B' : '#A855F7',
                  weight: isSelected ? 5.5 : 3.5,
                  dashArray: '7 6',
                  opacity: 0.9,
                }}
                eventHandlers={{ click: () => onSelectFeature(pipe) }}
              >
                <Tooltip sticky>
                  {pipe.name} · Ø{pipe.properties.diameter_mm} mm (SEWER NON-POTABLE)
                </Tooltip>
              </Polyline>
            );
          })}

        {/* 4. Sewage Treatment Plant (Doddabele STP) */}
        {(visibleLayers.sewerage ?? true) &&
          REAL_WORLD_FEATURES.treatmentPlants.map((stp) => (
            <CircleMarker
              key={stp.id}
              center={stp.coordinates}
              radius={9}
              pathOptions={{
                color: '#C084FC',
                fillColor: '#7E22CE',
                fillOpacity: 0.9,
                weight: 2.5,
              }}
              eventHandlers={{ click: () => onSelectFeature(stp) }}
            >
              <Tooltip sticky>
                <b>{stp.name}</b>
                <br />
                Capacity: {stp.properties.installed_capacity_mld} MLD
              </Tooltip>
            </CircleMarker>
          ))}

        {/* 5. Potable Water Pipelines (BWSSB Mains, Cyan) */}
        {(visibleLayers.potable_water ?? true) &&
          REAL_WORLD_FEATURES.potablePipes.map((pipe) => {
            const isSelected = selectedFeature?.id === pipe.id;
            const dia = pipe.properties.diameter_mm;
            const weight = isSelected ? 6 : dia >= 450 ? 5 : dia >= 300 ? 3.5 : 2.5;
            return (
              <Polyline
                key={pipe.id}
                positions={pipe.coordinates}
                pathOptions={{
                  color: isSelected ? '#F59E0B' : '#22D3EE',
                  weight,
                  opacity: 0.92,
                }}
                eventHandlers={{ click: () => onSelectFeature(pipe) }}
              >
                <Tooltip sticky>
                  <b>{pipe.name}</b>
                  <br />
                  Ø{dia} mm · {pipe.properties.material}
                  <br />
                  Status: {pipe.properties.operational_status}
                </Tooltip>
              </Polyline>
            );
          })}

        {/* 6. Valves (Amber Points) */}
        {(visibleLayers.valves ?? true) &&
          REAL_WORLD_FEATURES.valves.map((v) => (
            <CircleMarker
              key={v.id}
              center={v.coordinates}
              radius={5.5}
              pathOptions={{
                color: '#FFFFFF',
                fillColor: '#F59E0B',
                fillOpacity: 1.0,
                weight: 1.5,
              }}
              eventHandlers={{ click: () => onSelectFeature(v) }}
            >
              <Tooltip sticky>
                <b>{v.name}</b>
                <br />
                Type: {v.properties.valve_type} (Ø{v.properties.nominal_diameter_mm} mm)
              </Tooltip>
            </CircleMarker>
          ))}

        {/* 7. Pumping Stations (Blue Squares) */}
        {(visibleLayers.infrastructure ?? true) &&
          REAL_WORLD_FEATURES.pumpingStations.map((ps) => (
            <CircleMarker
              key={ps.id}
              center={ps.coordinates}
              radius={7.5}
              pathOptions={{
                color: '#FFFFFF',
                fillColor: '#3B82F6',
                fillOpacity: 1.0,
                weight: 2,
              }}
              eventHandlers={{ click: () => onSelectFeature(ps) }}
            >
              <Tooltip sticky>
                <b>{ps.name}</b>
                <br />
                Capacity: {ps.properties.installed_capacity_mld} MLD · Head: {ps.properties.rated_head_m} m
              </Tooltip>
            </CircleMarker>
          ))}

        {/* 8. Borewells (Teal Circles) */}
        {(visibleLayers.groundwater ?? true) &&
          REAL_WORLD_FEATURES.borewells.map((bw) => (
            <CircleMarker
              key={bw.id}
              center={bw.coordinates}
              radius={5}
              pathOptions={{
                color: '#0D1424',
                fillColor: '#10B981',
                fillOpacity: 0.95,
                weight: 1.5,
              }}
              eventHandlers={{ click: () => onSelectFeature(bw) }}
            >
              <Tooltip sticky>
                <b>{bw.name}</b>
                <br />
                Water Table: {bw.properties.water_level_mbgl} m bgl · Yield: {bw.properties.yield_lpm} LPM
              </Tooltip>
            </CircleMarker>
          ))}

        {/* 9. Environmental Observation Stations */}
        {(visibleLayers.monitors ?? true) && (
          <>
            {/* Groundwater Monitor */}
            {REAL_WORLD_FEATURES.groundwaterStations.map((st) => (
              <CircleMarker
                key={st.id}
                center={st.coordinates}
                radius={8}
                pathOptions={{
                  color: '#22D3EE',
                  fillColor: '#0E7490',
                  fillOpacity: 0.9,
                  weight: 2,
                }}
                eventHandlers={{ click: () => onSelectFeature(st) }}
              >
                <Tooltip sticky>
                  <b>{st.name}</b>
                  <br />
                  6-Hourly Telemetry: {st.properties.latest_water_level_mbgl} m bgl
                </Tooltip>
              </CircleMarker>
            ))}

            {/* Lake Water Quality Monitor */}
            {REAL_WORLD_FEATURES.waterQualityStations.map((st) => (
              <CircleMarker
                key={st.id}
                center={st.coordinates}
                radius={8}
                pathOptions={{
                  color: '#34D399',
                  fillColor: '#059669',
                  fillOpacity: 0.9,
                  weight: 2,
                }}
                eventHandlers={{ click: () => onSelectFeature(st) }}
              >
                <Tooltip sticky>
                  <b>{st.name}</b>
                  <br />
                  DO: {st.properties.dissolved_oxygen_mg_l} mg/L · BOD: {st.properties.biochemical_oxygen_demand_mg_l} mg/L
                </Tooltip>
              </CircleMarker>
            ))}

            {/* Rain Gauge */}
            {REAL_WORLD_FEATURES.rainGauges.map((st) => (
              <CircleMarker
                key={st.id}
                center={st.coordinates}
                radius={8}
                pathOptions={{
                  color: '#60A5FA',
                  fillColor: '#1D4ED8',
                  fillOpacity: 0.9,
                  weight: 2,
                }}
                eventHandlers={{ click: () => onSelectFeature(st) }}
              >
                <Tooltip sticky>
                  <b>{st.name}</b>
                  <br />
                  Annual Rainfall: {st.properties.annual_rainfall_2025_mm} mm
                </Tooltip>
              </CircleMarker>
            ))}
          </>
        )}
      </MapContainer>

      {/* Floating Layer Control Drawer */}
      <aside className={`geo-layer-control clay-panel ${layersOpen ? 'is-open' : 'is-collapsed'}`}>
        <div className='geo-layer-header' onClick={() => setLayersOpen((o) => !o)}>
          <div className='geo-layer-title'>
            <Layers size={15} />
            <b>Geographic Layers</b>
          </div>
          <span className='geo-layer-toggle-btn'>{layersOpen ? 'Hide' : 'Show'}</span>
        </div>

        {layersOpen && (
          <div className='geo-layer-list'>
            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.potable_water ?? true}
                onChange={() => onToggleLayer('potable_water')}
              />
              <span className='layer-color-dot' style={{ background: '#22D3EE' }} />
              <span className='layer-name'>Potable Mains (BWSSB)</span>
              <span className='layer-count'>{REAL_WORLD_FEATURES.potablePipes.length}</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.valves ?? true}
                onChange={() => onToggleLayer('valves')}
              />
              <span className='layer-color-dot' style={{ background: '#F59E0B' }} />
              <span className='layer-name'>Valves (Sluice & Air)</span>
              <span className='layer-count'>{REAL_WORLD_FEATURES.valves.length}</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.infrastructure ?? true}
                onChange={() => onToggleLayer('infrastructure')}
              />
              <span className='layer-color-dot' style={{ background: '#3B82F6' }} />
              <span className='layer-name'>Pumping Stations</span>
              <span className='layer-count'>{REAL_WORLD_FEATURES.pumpingStations.length}</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.groundwater ?? true}
                onChange={() => onToggleLayer('groundwater')}
              />
              <span className='layer-color-dot' style={{ background: '#10B981' }} />
              <span className='layer-name'>Public Borewells</span>
              <span className='layer-count'>{REAL_WORLD_FEATURES.borewells.length}</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.sewerage ?? true}
                onChange={() => onToggleLayer('sewerage')}
              />
              <span className='layer-color-dot' style={{ background: '#A855F7' }} />
              <span className='layer-name'>Sewerage (Non-Potable)</span>
              <span className='layer-count'>{REAL_WORLD_FEATURES.sewerPipes.length + 1}</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.surface_water ?? true}
                onChange={() => onToggleLayer('surface_water')}
              />
              <span className='layer-color-dot' style={{ background: '#38BDF8' }} />
              <span className='layer-name'>Hydrology & Lake</span>
              <span className='layer-count'>{REAL_WORLD_FEATURES.waterBodies.length}</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.monitors ?? true}
                onChange={() => onToggleLayer('monitors')}
              />
              <span className='layer-color-dot' style={{ background: '#34D399' }} />
              <span className='layer-name'>Telemetry & Monitors</span>
              <span className='layer-count'>3</span>
            </label>

            <label className='layer-check-item'>
              <input
                type='checkbox'
                checked={visibleLayers.admin_boundary ?? true}
                onChange={() => onToggleLayer('admin_boundary')}
              />
              <span className='layer-color-dot' style={{ background: '#64748B' }} />
              <span className='layer-name'>Ward 159 Boundary</span>
              <span className='layer-count'>1</span>
            </label>

            <div className='geo-basemap-toggle-row'>
              <span>CARTO Dark Basemap</span>
              <button
                className={`basemap-toggle-btn ${basemapOn ? 'is-active' : ''}`}
                onClick={() => setBasemapOn((b) => !b)}
              >
                {basemapOn ? 'Enabled' : 'Off (Grid)'}
              </button>
            </div>
          </div>
        )}
      </aside>

      {/* Audit Caution Legend Pill */}
      <div className='geo-audit-pill'>
        <AlertTriangle size={13} />
        <span>Source-Mapped GIS · Positional accuracy caveat per CAG 2022 Audit</span>
      </div>
    </div>
  );
}
