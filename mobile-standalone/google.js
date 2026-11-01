// Google Calendar through browser OAuth (short-lived token, no secret). Sign-in is a full-page redirect
// to Google and back: pop-ups do not work in an iPhone home-screen app.
// Scope: calendar.events only (read and create events; nothing else in the account).
import * as store from './store.js';

const SCOPE = 'https://www.googleapis.com/auth/calendar.events';
const API = 'https://www.googleapis.com/calendar/v3/calendars/primary/events';
const AUTH = 'https://accounts.google.com/o/oauth2/v2/auth';
let token = '', expires = 0;
export let lastError = '';

export const redirectUri = () => location.origin + location.pathname.replace(/[^/]*$/, '');
export const configured = () => /\.apps\.googleusercontent\.com$/.test(store.get('googleClientId'));
export const signedIn = () => !!token && Date.now() < expires - 60000;

function keep() {
  try { localStorage.setItem('jarvis.mobile.gtoken', JSON.stringify({ token, expires })); } catch (e) { /* ignore */ }
}

try {  // a token survives restarts of the app within its hour
  const saved = JSON.parse(localStorage.getItem('jarvis.mobile.gtoken') || 'null');
  if (saved && saved.expires > Date.now()) { token = saved.token; expires = saved.expires; }
} catch (e) { /* ignore */ }

// Coming back from Google: the answer is in the address fragment. Take it and clean the address at once.
(function takeRedirect() {
  if (!/(^|&)(access_token|error)=/.test(location.hash.slice(1))) { return; }
  const p = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, '', location.pathname + location.search);
  let want = '';
  try { want = localStorage.getItem('jarvis.mobile.gstate') || ''; localStorage.removeItem('jarvis.mobile.gstate'); } catch (e) { /* ignore */ }
  if (!want || p.get('state') !== want) { lastError = 'ответ Google не совпал с запросом, попробуйте ещё раз'; return; }
  if (p.get('error')) {
    lastError = p.get('error') === 'access_denied' ? 'доступ не разрешён (добавьте свою почту в Test users)' : p.get('error');
    return;
  }
  if (!String(p.get('scope') || '').includes(SCOPE)) { lastError = 'не дано разрешение на календарь'; return; }
  token = p.get('access_token');
  expires = Date.now() + (Number(p.get('expires_in')) || 3600) * 1000;
  keep();
  store.set('googleConsented', '1');
})();

export async function prepare() { /* nothing to load: kept for callers */ }

// Leaves the app for Google's sign-in page; the app opens again signed in.
export function signIn() {
  if (!configured()) { return Promise.reject(new Error('сначала укажите Client ID и сохраните')); }
  const state = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
  try { localStorage.setItem('jarvis.mobile.gstate', state); } catch (e) { /* ignore */ }
  const q = new URLSearchParams({ client_id: store.get('googleClientId'), redirect_uri: redirectUri(), response_type: 'token',
    scope: SCOPE, include_granted_scopes: 'true', state });
  if (!store.get('googleConsented')) { q.set('prompt', 'consent'); }
  location.assign(AUTH + '?' + q);
  return new Promise(() => {});  // the page is leaving
}

export function signOut() {
  token = ''; expires = 0;
  try { localStorage.removeItem('jarvis.mobile.gtoken'); } catch (e) { /* ignore */ }
}

async function api(url, opts = {}) {
  if (!signedIn()) { throw new Error('нужно войти в Google: Настройки → «Войти в Google»'); }
  const r = await fetch(url, { ...opts, headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', ...(opts.headers || {}) } });
  if (r.status === 401) { signOut(); throw new Error('вход в Google истёк: нажмите «Войти в Google»'); }
  const data = r.status === 204 ? {} : await r.json();
  if (!r.ok) { throw new Error((data.error && data.error.message) || 'Google HTTP ' + r.status); }
  return data;
}

const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export async function events(fromIso, toIso, max = 25) {
  const q = new URLSearchParams({ timeMin: new Date(fromIso).toISOString(), timeMax: new Date(toIso).toISOString(),
    singleEvents: 'true', orderBy: 'startTime', maxResults: String(Math.min(50, max)) });
  const data = await api(API + '?' + q);
  return (data.items || []).map((e) => ({
    title: e.summary || '(без названия)',
    start: e.start && (e.start.dateTime || e.start.date), end: e.end && (e.end.dateTime || e.end.date),
    allDay: !!(e.start && e.start.date), where: e.location || '', link: e.hangoutLink || '',
  }));
}

// A reminder is an event with a popup alert: Google's own app/notifications deliver it on any phone.
export async function addEvent({ title, start, end, remindMinutes, notes }) {
  const s = new Date(start);
  if (!title || isNaN(s)) { throw new Error('нужны название и время'); }
  const e = end ? new Date(end) : new Date(s.getTime() + 30 * 60000);
  const body = {
    summary: String(title).slice(0, 200), description: notes ? String(notes).slice(0, 2000) : undefined,
    start: { dateTime: s.toISOString(), timeZone: tz() }, end: { dateTime: e.toISOString(), timeZone: tz() },
    reminders: remindMinutes === undefined || remindMinutes === null ? { useDefault: true }
      : { useDefault: false, overrides: [{ method: 'popup', minutes: Math.max(0, Math.min(40320, Math.round(remindMinutes))) }] },
  };
  const made = await api(API, { method: 'POST', body: JSON.stringify(body) });
  return { title: made.summary, start: made.start && made.start.dateTime, link: made.htmlLink };
}
