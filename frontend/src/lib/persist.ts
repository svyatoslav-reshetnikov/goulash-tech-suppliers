import { useCallback, useState } from "react";

/** State kept in sessionStorage (survives tab switches and reloads of the page, not a new browser session). Storage may be unavailable. */
export function usePersistedState<T>(key: string, initial: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = window.sessionStorage.getItem(key);
      return stored === null ? initial : (JSON.parse(stored) as T);
    } catch {
      return initial;
    }
  });
  const update = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const resolved = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
        try {
          window.sessionStorage.setItem(key, JSON.stringify(resolved));
        } catch {
          // storage unavailable: keep working in memory
        }
        return resolved;
      });
    },
    [key],
  );
  return [value, update];
}
