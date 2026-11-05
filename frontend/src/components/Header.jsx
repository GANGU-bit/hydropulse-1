import { useEffect, useState } from 'react';
import {
  Activity,
  Droplets,
  Clock3,
  CircleHelp,
  Calculator,
  GitBranch,
} from 'lucide-react';
export default function Header({ mode, health, onHelp, onRoi, onStudio }) {
  return (
    <header className='topbar'>
      <div className='brand'>
        <div className='brand-mark'>
          <Droplets size={19} />
          <Activity size={11} className='brand-pulse' />
        </div>
        <div>
          <div className='brand-name'>
            Hydro<span>Pulse</span>
          </div>
          <div className='brand-sub'>
            KENGERI DMA <b>·</b> BWSSB DIGITAL TWIN
          </div>
        </div>
      </div>
      <div className='header-context'>
        <span className='district-dot' />
        Bengaluru <span className='context-divider' />{' '}
        <span className='mono'>12.9081° N&nbsp; 77.4852° E</span>
      </div>
      <div className='header-right'>
        <div className='network-counts'>
          <span>
            <b>{health?.num_junctions || 196}</b> junctions
          </span>
          <i />{' '}
          <span>
            <b>{health?.num_pipes || 364}</b> pipes
          </span>
          <i />{' '}
          <span>
            <b>05</b> sensors
          </span>
        </div>
        <span className={`status-pill ${mode}`}>
          <span className='status-dot' />
          {mode === 'live'
            ? 'LIVE API'
            : mode === 'mock'
              ? 'MOCK MODE'
              : mode === 'offline'
                ? 'OFFLINE'
                : 'CONNECTING'}
        </span>
        <div className='clock'>
          <Clock3 size={13} />
          <LiveClock />
        </div>
        <button
          className='icon-btn'
          aria-label='Open Network Studio'
          title='Network Studio'
          onClick={onStudio}
        >
          <GitBranch size={16} />
        </button>
        <button
          className='icon-btn'
          aria-label='Open ROI calculator'
          title='ROI calculator'
          onClick={onRoi}
        >
          <Calculator size={16} />
        </button>
        <button
          className='icon-btn'
          aria-label='Keyboard shortcuts'
          title='Keyboard shortcuts'
          onClick={onHelp}
        >
          <CircleHelp size={17} />
        </button>
      </div>
    </header>
  );
}
function LiveClock() {
  const [time, set] = useState('');
  useEffect(() => {
    // Pin the operational clock to IST regardless of the browser's local timezone.
    const up = () =>
      set(
        new Intl.DateTimeFormat('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        }).format(new Date()),
      );
    up();
    const id = setInterval(up, 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span>
      {time} <small>IST</small>
    </span>
  );
}
import React from 'react';
