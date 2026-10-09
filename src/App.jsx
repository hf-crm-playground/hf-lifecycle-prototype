import React, { useCallback, useMemo, useState } from 'react';
import { CUSTOMERS, DEFAULT_ACTIONS, DEFAULT_INTEGRATIONS, DEFAULT_SETTINGS } from './data.js';
import { aggregate, optimize, responseBaseline } from './model.js';
import { AppCtx, useHashRoute, usePersisted } from './store.js';
import { Icon } from './components/ui.jsx';
import PushDialog from './components/PushDialog.jsx';
import Summary from './pages/Summary.jsx';
import Customers from './pages/Customers.jsx';
import Optimizer from './pages/Optimizer.jsx';
import Savings from './pages/Savings.jsx';
import Planner from './pages/Planner.jsx';
import Drivers from './pages/Drivers.jsx';
import Integrations from './pages/Integrations.jsx';

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
  { id: 'summary', label: 'Executive Summary', icon: 'summary', page: Summary },
  { id: 'customers', label: 'Customer Explorer', icon: 'customers', page: Customers },
  { id: 'optimizer', label: 'Lifecycle Optimizer', icon: 'optimizer', page: Optimizer },
  { id: 'savings', label: 'Savings Ledger', icon: 'savings', page: Savings },
  { id: 'planner', label: 'Next-Euro Planner', icon: 'planner', page: Planner },
  { id: 'drivers', label: 'Drivers', icon: 'drivers', page: Drivers },
  { id: 'integrations', label: 'Integrations', icon: 'integrations', page: Integrations },
];

export default function App() {
  const route = useHashRoute('summary');
  const [storedActions, setActions] = usePersisted('gc.actions.v1', DEFAULT_ACTIONS);
  const [storedSettings, setSettings] = usePersisted('gc.settings.v1', DEFAULT_SETTINGS);
  const [pushLog, setPushLog] = usePersisted('gc.pushlog.v1', []);
  const [integrations, setIntegrations] = usePersisted('gc.integrations.v1', DEFAULT_INTEGRATIONS);
  const [push, setPush] = useState(null);
  const [toast, setToast] = useState(null);

  const actions = useMemo(
    () => DEFAULT_ACTIONS.map((d) => ({ ...d, ...(storedActions.find((a) => a.id === d.id) || {}) })),
    [storedActions],
  );
  const settings = useMemo(() => ({ ...DEFAULT_SETTINGS, ...storedSettings }), [storedSettings]);

  const planRows = useMemo(() => optimize(CUSTOMERS, actions, settings), [actions, settings]);
  const baseRows = useMemo(() => responseBaseline(CUSTOMERS, actions, settings), [actions, settings]);
  const plan = useMemo(() => aggregate(planRows), [planRows]);
  const base = useMemo(() => aggregate(baseRows), [baseRows]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    window.clearTimeout(showToast.t);
    showToast.t = window.setTimeout(() => setToast(null), 4200);
  }, []);

  const resetDemo = () => {
    setActions(DEFAULT_ACTIONS);
    setSettings(DEFAULT_SETTINGS);
    setPushLog([]);
    setIntegrations(DEFAULT_INTEGRATIONS);
    showToast('Prototype reset to its starting state.');
  };

  const ctx = {
    customers: CUSTOMERS,
    actions,
    setActions,
    settings,
    setSettings,
    planRows,
    baseRows,
    plan,
    base,
    pushLog,
    setPushLog,
    integrations,
    setIntegrations,
    openPush: setPush,
    showToast,
  };

  const current = NAV.find((n) => n.id === route) || NAV[0];
  const Page = current.page;

  return (
    <AppCtx.Provider value={ctx}>
      <a href="#main" className="skip">Skip to content</a>
      <div className="app">
        <aside className="sidebar">
          <a href="#/summary" className="brand" aria-label="GenComm Lifecycle home">
            <svg width="34" height="22" viewBox="0 0 34 22" aria-hidden="true">
              <circle cx="11" cy="11" r="8" fill="none" stroke="#8B7FE0" strokeWidth="3" />
              <circle cx="23" cy="11" r="8" fill="none" stroke="#B9B0F2" strokeWidth="3" />
            </svg>
            <span className="brand-text">
              <span className="brand-name">GENCOMM</span>
              <span className="brand-sub">lifecycle</span>
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
            <p>Prototype with sample data for a demo store of {CUSTOMERS.length.toLocaleString('en-GB')} customers. Nothing is sent anywhere.</p>
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
      {push && <PushDialog {...push} onClose={() => setPush(null)} />}
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" />
          <span>{toast}</span>
        </div>
      )}
    </AppCtx.Provider>
  );
}
