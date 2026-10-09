import { createContext, useContext, useEffect, useState } from 'react';

// Per-viewer storage for a prototype: falls back to defaults when storage is unavailable.
export function usePersisted(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage unavailable: keep in memory */
    }
  }, [key, value]);
  return [value, setValue];
}

export const AppCtx = createContext(null);
export const useApp = () => useContext(AppCtx);

export function useHashRoute(defaultRoute) {
  const read = () => (window.location.hash.replace(/^#\/?/, '') || defaultRoute).split('?')[0];
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => {
      setRoute(read());
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}
