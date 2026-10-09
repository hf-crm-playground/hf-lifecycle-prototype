import React, { useCallback, useMemo, useState } from 'react';
import { TRAVELLERS } from './engine/travellers.js';
import { DEFAULT_POLICY, runDecisions, summarize } from './engine/policy.js';
import { AppCtx, useHashRoute, usePersisted } from './store.js';
import { Icon } from './components/ui.jsx';
import Overview from './pages/Overview.jsx';
import Travellers from './pages/Travellers.jsx';
import Policy from './pages/Policy.jsx';
import Capture from './pages/Capture.jsx';
import Measurement from './pages/Measurement.jsx';
import Rollout from './pages/Rollout.jsx';
import DataKlaviyo from './pages/DataKlaviyo.jsx';

class PageBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidUpdate(prev) {
    if (prev.route !== this.props.route && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      return (
        <div className="card">
          <h2>This screen hit a problem</h2>
          <p className="muted">Try another screen, or reset the prototype from the sidebar.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

const NAV = [
  { id: 'overview', label: 'Overview', icon: 'summary', page: Overview },
  { id: 'travellers', label: 'Traveller explorer', icon: 'customers', page: Travellers },
  { id: 'policy', label: 'Decision policy', icon: 'optimizer', page: Policy },
  { id: 'capture', label: 'Last-click capture', icon: 'savings', page: Capture },
  { id: 'measurement', label: 'Measurement', icon: 'planner', page: Measurement },
  { id: 'rollout', label: 'Rollout and gaps', icon: 'drivers', page: Rollout },
  { id: 'data', label: 'Data and Klaviyo', icon: 'integrations', page: DataKlaviyo },
];

export default function App() {
  const route = useHashRoute('overview');
  const [storedPolicy, setPolicy] = usePersisted('hf.policy.v1', DEFAULT_POLICY);
  const [overrides, setOverrides] = usePersisted('hf.overrides.v1', {});
  const [gapStatus, setGapStatus] = usePersisted('hf.gaps.v1', {});
  const [phaseNow, setPhaseNow] = usePersisted('hf.phase.v1', 1);
  const [toast, setToast] = useState(null);

  const policy = useMemo(
    () => ({ ...DEFAULT_POLICY, ...storedPolicy, subcap: { ...DEFAULT_POLICY.subcap, ...(storedPolicy.subcap || {}) } }),
    [storedPolicy],
  );
  const decisions = useMemo(() => runDecisions(TRAVELLERS, policy, overrides), [policy, overrides]);
  const summary = useMemo(() => summarize(decisions), [decisions]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    window.clearTimeout(showToast.t);
    showToast.t = window.setTimeout(() => setToast(null), 4200);
  }, []);

  const resetDemo = () => {
    setPolicy(DEFAULT_POLICY);
    setOverrides({});
    setGapStatus({});
    setPhaseNow(1);
    showToast('Prototype reset to the design defaults.');
  };

  const ctx = {
    travellers: TRAVELLERS,
    policy,
    setPolicy: (patch) => setPolicy({ ...policy, ...patch }),
    overrides,
    setOverrides,
    decisions,
    summary,
    gapStatus,
    setGapStatus,
    phaseNow,
    setPhaseNow,
    showToast,
  };

  const current = NAV.find((n) => n.id === route) || NAV[0];
  const Page = current.page;

  return (
    <AppCtx.Provider value={ctx}>
      <a href="#main" className="skip">Skip to content</a>
      <div className="app">
        <aside className="sidebar">
          <a href="#/overview" className="brand" aria-label="Holafly Trip NBA home">
            <span className="brand-mark" aria-hidden="true">
              <svg width="30" height="30" viewBox="0 0 30 30">
                <circle cx="15" cy="15" r="14" fill="#d55460" />
                <path d="M8 18c3-6 11-6 14 0" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
                <circle cx="15" cy="11" r="2.4" fill="#fff" />
              </svg>
            </span>
            <span className="brand-text">
              <span className="brand-name">Trip NBA</span>
              <span className="brand-sub">Holafly · decision console</span>
            </span>
          </a>
          <nav aria-label="Main" className="nav">
            {NAV.map((n) => (
              <a key={n.id} href={`#/${n.id}`} className={`nav-link${current.id === n.id ? ' is-active' : ''}`} aria-current={current.id === n.id ? 'page' : undefined}>
                <Icon name={n.icon} />
                <span>{n.label}</span>
              </a>
            ))}
          </nav>
          <div className="sidebar-foot">
            <p>
              Prototype of the Holafly NBA System Design (23 Sep 2026) on {TRAVELLERS.length.toLocaleString('en-GB')} sample travellers. Nothing is sent to Klaviyo.
            </p>
            <button type="button" className="link-btn" onClick={resetDemo}>
              Reset prototype
            </button>
          </div>
        </aside>
        <main id="main" className="main" tabIndex={-1}>
          <PageBoundary route={current.id}>
            <Page />
          </PageBoundary>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" />
          <span>{toast}</span>
        </div>
      )}
    </AppCtx.Provider>
  );
}
