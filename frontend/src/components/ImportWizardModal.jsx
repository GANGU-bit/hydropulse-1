import { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  FileCheck,
  FileSpreadsheet,
  FileText,
  FileUp,
  Layers,
  ShieldAlert,
  Upload,
  X,
} from 'lucide-react';
import { validateImportedFile } from '../lib/realWorldData.js';

const ASSET_TYPES = [
  { id: 'potable_water_pipe', label: 'Potable Water Pipeline (Mains / Feeder)' },
  { id: 'sewer_pipe', label: 'Sewerage Gravity Line (Non-Potable)' },
  { id: 'valve', label: 'Valves (Isolation / Air / Sluice)' },
  { id: 'pumping_station', label: 'Pumping / Booster Station' },
  { id: 'borewell', label: 'Groundwater Borewell' },
  { id: 'groundwater_monitor', label: 'Groundwater Telemetry Station' },
  { id: 'water_quality_monitor', label: 'Water Quality Observation Point' },
  { id: 'rain_gauge', label: 'Rain Gauge / Hydrological Monitor' },
  { id: 'water_body', label: 'Surface Waterbody / Lake / Stream' },
  { id: 'road', label: 'Urban Road / Street Centerline' },
];

export default function ImportWizardModal({ isOpen, onClose, onImportStaged }) {
  const [step, setStep] = useState(1);
  const [file, setFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [assetType, setAssetType] = useState('potable_water_pipe');
  const [validation, setValidation] = useState(null);
  const [importName, setImportName] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);

  if (!isOpen) return null;

  const handleFileChange = async (e) => {
    const selected = e.target.files?.[0];
    if (!selected) return;
    setFile(selected);
    setImportName(selected.name.replace(/\.[^/.]+$/, ''));
    setIsProcessing(true);

    try {
      const text = await selected.text();
      setFileContent(text);
      const res = validateImportedFile(selected.name, text, assetType);
      setValidation(res);
      setStep(2);
    } catch (err) {
      setValidation({
        valid: false,
        errors: [`Failed to read file: ${err.message}`],
        warnings: [],
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleAssetTypeChange = (type) => {
    setAssetType(type);
    if (file && fileContent) {
      const res = validateImportedFile(file.name, fileContent, type);
      setValidation(res);
    }
  };

  const handleConfirm = () => {
    const stagedLayer = {
      id: `imported_${Date.now()}`,
      displayName: importName || file?.name || 'Imported Layer',
      assetType,
      fileName: file?.name,
      featureCount: validation?.featureCount || 0,
      stagedAt: new Date().toISOString(),
      format: validation?.detectedFormat || 'Unknown',
      dataStatus: 'source_mapped',
      isStagedOnly: true,
      features: validation?.features || [],
    };
    onImportStaged?.(stagedLayer);
    onClose();
  };

  const reset = () => {
    setStep(1);
    setFile(null);
    setFileContent('');
    setValidation(null);
  };

  return (
    <div
      className='studio-modal-backdrop'
      role='dialog'
      aria-modal='true'
      aria-labelledby='import-wizard-title'
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <section className='import-wizard-modal clay-panel'>
        {/* Wizard Header */}
        <div className='catalog-header'>
          <div className='catalog-header-title'>
            <div className='catalog-icon-badge'>
              <FileUp size={20} />
            </div>
            <div>
              <span className='studio-eyebrow'>STAGING PIPELINE</span>
              <h2 id='import-wizard-title'>Import Real-World Layer</h2>
            </div>
          </div>
          <button className='icon-btn' onClick={onClose} aria-label='Close Import Wizard'>
            <X size={18} />
          </button>
        </div>

        {/* Step Indicator */}
        <div className='wizard-steps'>
          <div className={`wizard-step ${step >= 1 ? 'is-active' : ''} ${step > 1 ? 'is-done' : ''}`}>
            <span>1</span> File Upload
          </div>
          <div className={`wizard-step ${step >= 2 ? 'is-active' : ''} ${step > 2 ? 'is-done' : ''}`}>
            <span>2</span> Asset Classification
          </div>
          <div className={`wizard-step ${step >= 3 ? 'is-active' : ''} ${step > 3 ? 'is-done' : ''}`}>
            <span>3</span> Validation & Diagnostics
          </div>
          <div className={`wizard-step ${step >= 4 ? 'is-active' : ''}`}>
            <span>4</span> Staging Confirmation
          </div>
        </div>

        {/* Wizard Steps Content */}
        <div className='wizard-content'>
          {/* STEP 1: Upload */}
          {step === 1 && (
            <div className='wizard-step-pane'>
              <div className='upload-dropzone'>
                <Upload size={36} />
                <h3>Choose a geospatial file to import</h3>
                <p>Supported: GeoJSON (.geojson, .json), KML (.kml), CSV with lat/lon, EPANET (.inp)</p>
                <label className='studio-text-button primary upload-btn'>
                  <FileUp size={15} /> Browse File
                  <input
                    type='file'
                    accept='.geojson,.json,.kml,.csv,.inp'
                    hidden
                    onChange={handleFileChange}
                  />
                </label>
              </div>
              <div className='wizard-note'>
                <ShieldAlert size={15} />
                <p>
                  Security boundary: Files are processed client-side into local draft staging.
                  Imported files will <b>never</b> overwrite the committed EPANET benchmark model.
                </p>
              </div>
            </div>
          )}

          {/* STEP 2: Classification */}
          {step === 2 && (
            <div className='wizard-step-pane'>
              <div className='wizard-field'>
                <label>Layer Display Name</label>
                <input
                  type='text'
                  value={importName}
                  onChange={(e) => setImportName(e.target.value)}
                  placeholder='e.g., BWSSB Ward 159 Surveyed Mains'
                />
              </div>

              <div className='wizard-field'>
                <label>Select Asset Classification</label>
                <div className='asset-type-grid'>
                  {ASSET_TYPES.map((t) => (
                    <button
                      key={t.id}
                      className={`asset-type-chip ${assetType === t.id ? 'is-selected' : ''}`}
                      onClick={() => handleAssetTypeChange(t.id)}
                    >
                      <span>{t.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              {assetType === 'sewer_pipe' && (
                <div className='wizard-warning-box'>
                  <AlertTriangle size={15} />
                  <span>
                    Sewerage assets are strictly quarantined from the potable-water hydraulic network.
                  </span>
                </div>
              )}

              <div className='wizard-actions'>
                <button className='studio-text-button secondary' onClick={() => setStep(1)}>
                  Back
                </button>
                <button className='studio-text-button primary' onClick={() => setStep(3)}>
                  Next: Validate Geometry <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {/* STEP 3: Validation */}
          {step === 3 && (
            <div className='wizard-step-pane'>
              <div className='validation-report clay-panel'>
                <div className='validation-top'>
                  <div className='valid-icon'>
                    {validation?.valid ? <CheckCircle2 size={24} className='green' /> : <AlertTriangle size={24} className='amber' />}
                  </div>
                  <div>
                    <h4>{validation?.valid ? 'Format & Topology Validated' : 'Validation Issues Detected'}</h4>
                    <p>{validation?.fileName} · {validation?.detectedFormat} · {validation?.detectedCrs}</p>
                  </div>
                </div>

                <div className='validation-stats-grid'>
                  <div>
                    <span>DETECTED FEATURES</span>
                    <b>{validation?.featureCount || 0}</b>
                  </div>
                  <div>
                    <span>HYDRAULIC STATUS</span>
                    <b className={validation?.modelReady ? 'green' : 'amber'}>
                      {validation?.modelReady ? 'MODEL READY' : 'GEOGRAPHIC ONLY'}
                    </b>
                  </div>
                  <div>
                    <span>ERRORS</span>
                    <b className={validation?.errors.length ? 'red' : 'green'}>{validation?.errors.length || 0}</b>
                  </div>
                  <div>
                    <span>WARNINGS</span>
                    <b>{validation?.warnings.length || 0}</b>
                  </div>
                </div>

                {validation?.blockingReasons?.length > 0 && (
                  <div className='blocking-reasons'>
                    <span className='studio-group-title'>HYDRAULIC SIMULATION READINESS GATE</span>
                    <ul>
                      {validation.blockingReasons.map((r, i) => (
                        <li key={i}>
                          <AlertTriangle size={13} />
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <div className='wizard-actions'>
                <button className='studio-text-button secondary' onClick={() => setStep(2)}>
                  Back
                </button>
                <button
                  className='studio-text-button primary'
                  disabled={!validation?.valid}
                  onClick={() => setStep(4)}
                >
                  Proceed to Staging <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {/* STEP 4: Confirm */}
          {step === 4 && (
            <div className='wizard-step-pane'>
              <div className='staging-confirm-box'>
                <FileCheck size={40} className='green' />
                <h3>Ready to Stage Real-World Layer</h3>
                <p>
                  <b>{importName}</b> will be committed to your draft workspace with{' '}
                  <b>{validation?.featureCount}</b> features.
                </p>
                <div className='staging-commit-notice'>
                  <Check size={14} /> Staged as reference overlay in Geographic Map and 3D Terrain view.
                  <br />
                  <Check size={14} /> WNTR hydraulic model remains untouched and 100% frozen.
                </div>
              </div>

              <div className='wizard-actions'>
                <button className='studio-text-button secondary' onClick={() => setStep(3)}>
                  Back
                </button>
                <button className='studio-text-button primary' onClick={handleConfirm}>
                  <Check size={14} /> Commit to Staging
                </button>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
