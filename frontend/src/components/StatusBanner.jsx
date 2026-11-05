import { AlertTriangle, RefreshCw } from 'lucide-react';

export default function StatusBanner({ mode, networkSource, onRetry }) {
  // Make connection state explicit because offline mode can use live snapshots or local demo data.
  if (mode === 'offline') {
    const sourceLabel =
      networkSource === 'recorded'
        ? 'running on recorded data'
        : networkSource === 'live'
          ? 'using the last network snapshot with local simulation'
          : 'running on local demo data';
    return (
      <div className='offline-banner' role='status'>
        <AlertTriangle size={15} />
        <span>
          <b>Backend offline:</b> {sourceLabel}
        </span>
        <button onClick={onRetry} aria-label='Retry backend connection'>
          <RefreshCw size={13} /> Retry connection
        </button>
      </div>
    );
  }
  if (mode === 'loading') {
    return (
      <div className='connecting-banner' role='status'>
        <span /> CONNECTING TO KENGERI NETWORK API
      </div>
    );
  }
  return null;
}
