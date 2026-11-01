// Settings and keys live only on this phone (localStorage of this site). Nothing is sent anywhere
// except to the service each key belongs to.
const PREFIX = 'jarvis.mobile.';
export const FIELDS = ['claudeKey', 'geminiKey', 'model', 'googleClientId', 'pcUrl', 'pcToken', 'city', 'speak'];
const DEFAULTS = { model: 'claude-sonnet-5-5', speak: '1', city: 'Москва' };

export function get(name) {
  try { return localStorage.getItem(PREFIX + name) ?? DEFAULTS[name] ?? ''; } catch (e) { return DEFAULTS[name] ?? ''; }
}
export function set(name, value) {
  try {
    if (value === '' || value === null || value === undefined) { localStorage.removeItem(PREFIX + name); }
    else { localStorage.setItem(PREFIX + name, String(value)); }
  } catch (e) { /* private mode: settings last for this session only */ }
}
export function wipe() {
  try { Object.keys(localStorage).filter((k) => k.startsWith(PREFIX)).forEach((k) => localStorage.removeItem(k)); } catch (e) { /* ignore */ }
}
export function json(name, fallback) {
  try { return JSON.parse(get(name)) ?? fallback; } catch (e) { return fallback; }
}
// A cloud service this phone's network cannot reach (no connection, "not available in your location"):
// skipped for a while so every question does not first wait for the same refusal.
export function off(name) { return Number(get('off.' + name) || 0) > Date.now(); }
export function setOff(name, ms = 3600000) { set('off.' + name, Date.now() + ms); }
