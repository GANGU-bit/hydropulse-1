import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  Filter,
  Info,
  Layers,
  Search,
  Shield,
  X,
} from 'lucide-react';
import { DATA_CATALOG, KENGERI_AOI, filterCatalog } from '../lib/realWorldData.js';

const CATEGORIES = [
  'All',
  'Water Supply',
  'Sewerage',
  'Groundwater',
  'Water Quality',
  'Rainfall',
  'Surface Water',
  'Urban Context',
  'Terrain',
];

export default function DataCatalogModal({
  isOpen,
  onClose,
  activeLayerIds = [],
  onToggleLayer,
  onSelectDataset,
}) {
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('All');
  const [statusFilter, setStatusFilter] = useState('All');
  const [selectedItem, setSelectedItem] = useState(DATA_CATALOG[0]);

  const filtered = useMemo(
    () => filterCatalog(DATA_CATALOG, search, selectedCategory, statusFilter),
    [search, selectedCategory, statusFilter],
  );

  if (!isOpen) return null;

  return (
    <div
      className='studio-modal-backdrop'
      role='dialog'
      aria-modal='true'
      aria-labelledby='catalog-modal-title'
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <section className='data-catalog-modal clay-panel'>
        {/* Header */}
        <div className='catalog-header'>
          <div className='catalog-header-title'>
            <div className='catalog-icon-badge'>
              <Database size={20} />
            </div>
            <div>
              <span className='studio-eyebrow'>GEOSPATIAL ASSET REGISTRY</span>
              <h2 id='catalog-modal-title'>Real-World Data Catalog</h2>
            </div>
          </div>
          <div className='catalog-header-actions'>
            <span className='catalog-count-pill'>
              <b>{filtered.length}</b> datasets
            </span>
            <button
              className='icon-btn'
              onClick={onClose}
              aria-label='Close Data Catalog'
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Audit Disclaimer Banner */}
        <div className='catalog-audit-banner'>
          <AlertTriangle size={15} />
          <div>
            <b>Provenance & Audit Caveat (CAG 2022 Compliance Audit):</b>
            <p>{KENGERI_AOI.auditCaveat}</p>
          </div>
        </div>

        {/* Search & Category Filter Toolbar */}
        <div className='catalog-toolbar'>
          <div className='catalog-search-wrap'>
            <Search size={15} />
            <input
              type='text'
              placeholder='Search BWSSB, KSPCB, NWDP, Copernicus, roads, pipes...'
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label='Search datasets'
            />
            {search && (
              <button
                className='catalog-clear-search'
                onClick={() => setSearch('')}
                aria-label='Clear search'
              >
                <X size={13} />
              </button>
            )}
          </div>
          <div className='catalog-category-bar'>
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                className={`catalog-cat-pill ${selectedCategory === cat ? 'is-active' : ''}`}
                onClick={() => setSelectedCategory(cat)}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        {/* Content split: List on Left, Detail on Right */}
        <div className='catalog-body'>
          <div className='catalog-list'>
            {filtered.length === 0 ? (
              <div className='catalog-empty'>
                <Info size={24} />
                <b>No datasets match your search</b>
                <p>Try resetting filters or searching for BWSSB, sewer, or groundwater.</p>
              </div>
            ) : (
              filtered.map((item) => {
                const isActive = activeLayerIds.includes(item.id);
                const isSelected = selectedItem?.id === item.id;
                return (
                  <div
                    key={item.id}
                    className={`catalog-card ${isSelected ? 'is-selected' : ''}`}
                    onClick={() => setSelectedItem(item)}
                  >
                    <div className='catalog-card-top'>
                      <span className='catalog-card-cat'>{item.category}</span>
                      <span className={`freshness-badge freshness-${item.freshness.toLowerCase()}`}>
                        {item.freshness}
                      </span>
                    </div>
                    <h4>{item.displayName}</h4>
                    <p className='catalog-card-pub'>{item.publisher}</p>
                    <div className='catalog-card-foot'>
                      <span className='provenance-pill' data-status={item.dataStatus}>
                        {item.dataStatus.replace('_', ' ')}
                      </span>
                      <span className='catalog-feature-count'>
                        {item.featureCount} features
                      </span>
                      <button
                        className={`catalog-layer-toggle ${isActive ? 'is-active' : ''}`}
                        title={isActive ? 'Hide layer from map' : 'Show layer on map'}
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleLayer(item.id);
                        }}
                      >
                        {isActive ? <Eye size={13} /> : <EyeOff size={13} />}
                        <span>{isActive ? 'Visible' : 'Hidden'}</span>
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Dataset Details Inspector */}
          {selectedItem && (
            <aside className='catalog-detail-pane clay-panel'>
              <div className='detail-heading'>
                <div className='detail-title-block'>
                  <span className='studio-eyebrow'>{selectedItem.category}</span>
                  <h3>{selectedItem.displayName}</h3>
                  <p>{selectedItem.publisher} · {selectedItem.catalogue}</p>
                </div>
                <button
                  className={`studio-text-button ${activeLayerIds.includes(selectedItem.id) ? 'secondary' : 'primary'}`}
                  onClick={() => onToggleLayer(selectedItem.id)}
                >
                  {activeLayerIds.includes(selectedItem.id) ? (
                    <>
                      <EyeOff size={14} /> Remove layer
                    </>
                  ) : (
                    <>
                      <Eye size={14} /> Add to map
                    </>
                  )}
                </button>
              </div>

              <div className='detail-meta-grid'>
                <div className='meta-item'>
                  <span>PROVENANCE</span>
                  <b className='provenance-pill' data-status={selectedItem.dataStatus}>
                    {selectedItem.dataStatus.replace('_', ' ').toUpperCase()}
                  </b>
                </div>
                <div className='meta-item'>
                  <span>CONFIDENCE</span>
                  <b className={`confidence-pill conf-${selectedItem.confidenceLevel}`}>
                    {selectedItem.confidenceLevel.toUpperCase()}
                  </b>
                </div>
                <div className='meta-item'>
                  <span>FORMAT</span>
                  <b>{selectedItem.format}</b>
                </div>
                <div className='meta-item'>
                  <span>CRS</span>
                  <b>{selectedItem.targetCrs}</b>
                </div>
                <div className='meta-item'>
                  <span>SOURCE UPDATED</span>
                  <b>{selectedItem.sourceUpdatedAt || 'Static Reference'}</b>
                </div>
                <div className='meta-item'>
                  <span>DOWNLOADED AT</span>
                  <b>{selectedItem.downloadedAt?.split('T')[0] || '2026-11-05'}</b>
                </div>
                <div className='meta-item'>
                  <span>KENGERI FEATURES</span>
                  <b>{selectedItem.featureCount} in study area</b>
                </div>
                <div className='meta-item'>
                  <span>LICENSE</span>
                  <b>{selectedItem.licenseName}</b>
                </div>
              </div>

              <div className='detail-section'>
                <span className='studio-group-title'>DATASET INTEGRITY & SHA-256</span>
                <code className='sha256-block'>{selectedItem.sha256}</code>
              </div>

              <div className='detail-section'>
                <span className='studio-group-title'>LIMITATIONS & FIELD CAVEATS</span>
                <ul className='detail-limitations'>
                  {selectedItem.limitations.map((lim, i) => (
                    <li key={i}>
                      <AlertTriangle size={13} />
                      <span>{lim}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className='detail-section'>
                <span className='studio-group-title'>ORIGIN & ATTRIBUTION</span>
                <p className='detail-attribution'>
                  {selectedItem.attribution}
                </p>
                {selectedItem.sourceUrl && (
                  <a
                    href={selectedItem.sourceUrl}
                    target='_blank'
                    rel='noreferrer'
                    className='catalog-ext-link'
                  >
                    <span>View original catalogue record</span>
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>
            </aside>
          )}
        </div>
      </section>
    </div>
  );
}
