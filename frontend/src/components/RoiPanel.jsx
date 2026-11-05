import { useEffect, useMemo, useState } from 'react';
import {
  ArrowUpRight,
  Calculator,
  Droplet,
  IndianRupee,
  Truck,
  X,
} from 'lucide-react';
import { compactINR, number } from '../lib/format.js';
export default function RoiPanel({ open, onClose, lastResult }) {
  const [leaks, setLeaks] = useState(20),
    [lpm, setLpm] = useState(lastResult?.actual_loss_lpm || 482.1),
    [days, setDays] = useState(12),
    [cost, setCost] = useState(45);
  useEffect(() => {
    if (lastResult?.actual_loss_lpm != null) setLpm(lastResult.actual_loss_lpm);
  }, [lastResult?.actual_loss_lpm]);
  // Annual volume is leak count × 12 months × daily loss × recovery days.
  const litres = useMemo(
    () => leaks * 12 * lpm * 1440 * days,
    [leaks, lpm, days],
  );
  if (!open) return null;
  return (
    <div className='modal-backdrop' onClick={onClose}>
      <section className='roi-modal' onClick={(e) => e.stopPropagation()}>
        <div className='modal-heading'>
          <div className='roi-mark'>
            <Calculator size={18} />
          </div>
          <div>
            <div className='eyebrow'>OPERATIONAL IMPACT MODEL</div>
            <h2>Water saved. Value recovered.</h2>
          </div>
          <button
            className='icon-btn'
            onClick={onClose}
            aria-label='Close ROI calculator'
          >
            <X size={17} />
          </button>
        </div>
        <p className='roi-intro'>
          Estimate the annual benefit of faster leak localization across this
          district.
        </p>
        <div className='roi-fields'>
          <label>
            Leaks detected / month
            <input
              type='number'
              min='1'
              value={leaks}
              onChange={(e) => setLeaks(+e.target.value)}
            />
          </label>
          <label>
            Average water loss <small>LPM</small>
            <input
              type='number'
              min='0'
              value={lpm}
              onChange={(e) => setLpm(+e.target.value)}
            />
          </label>
          <label>
            Days saved per leak
            <input
              type='number'
              min='1'
              value={days}
              onChange={(e) => setDays(+e.target.value)}
            />
          </label>
          <label>
            Production cost <small>₹ / kL · EDITABLE ASSUMPTION</small>
            <input
              type='number'
              min='0'
              value={cost}
              onChange={(e) => setCost(+e.target.value)}
            />
          </label>
        </div>
        <div className='roi-results'>
          <div className='roi-result'>
            <span>
              <Droplet size={14} /> WATER RECOVERED / YEAR
            </span>
            <b>
              {number(litres / 1e6, 2)} <small>ML</small>
            </b>
            <small>{number(litres, 0)} litres</small>
          </div>
          <div className='roi-result'>
            <span>
              <Truck size={14} /> TANKERS SAVED / YEAR
            </span>
            <b>
              {number(litres / 6000, 0)} <small>trips</small>
            </b>
            <small>6,000 L capacity</small>
          </div>
          <div className='roi-result emphasis'>
            <span>
              <IndianRupee size={14} /> PRODUCTION VALUE / YEAR
            </span>
            <b>{compactINR((litres / 1000) * cost)}</b>
            <small>at ₹{number(cost)} per kL</small>
          </div>
        </div>
        <div className='roi-foot'>
          <span>Model based on leak volume × 12 months × recovery days.</span>
          <button onClick={onClose}>
            Done <ArrowUpRight size={14} />
          </button>
        </div>
      </section>
    </div>
  );
}
