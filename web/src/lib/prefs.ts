// Small display preferences kept in localStorage. Storage can be unavailable
// (private windows, blocked cookies); fall back to the default quietly.

const PREFIX = 'airspace:';

export function loadPref(key: string, fallback: boolean): boolean {
  try {
    const value = localStorage.getItem(PREFIX + key);
    return value === null ? fallback : value === '1';
  } catch {
    return fallback;
  }
}

export function savePref(key: string, value: boolean): void {
  try {
    localStorage.setItem(PREFIX + key, value ? '1' : '0');
  } catch {
    // Not persisted; the setting still applies for this page
  }
}
