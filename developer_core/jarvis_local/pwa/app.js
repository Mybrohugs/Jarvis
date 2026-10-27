'use strict';
// JARVIS phone PWA. Read + queue only; all output via textContent (no HTML from data).
const $ = (id) => document.getElementById(id);
const KEY = 'jarvis.pwa.key';
let token = '';
let wakeLock = null;

function stored() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } }
function store(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) { /* private mode */ } }

async function api(path, body) {
  const opts = { headers: { Authorization: 'Bearer ' + token }, cache: 'no-store' };
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) { throw new Error('auth'); }
  const data = await r.json();
  if (!r.ok) { throw new Error(data.error || ('HTTP ' + r.status)); }
  return data;
}

function show(screen) {
  $('setup').hidden = screen !== 'setup';
  $('app').hidden = screen !== 'app';
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined) { n.textContent = text; }
  return n;
}

function badge(status) {
  const known = ['PASS', 'done', 'pending', 'CANCELLED'];
  return el('span', 'st ' + (known.includes(status) ? status : 'other'), status);
}

function when(utc) {
  const d = new Date(utc);
  return isNaN(d) ? '' : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

const STATES = { standby: 'ожидание', thinking: 'обработка', speaking: 'говорю', error: 'сбой' };
function setState(name) {
  const r = $('reactor');
  r.classList.remove('thinking', 'speaking', 'error');
  if (name !== 'standby') { r.classList.add(name); }
  $('state').textContent = STATES[name];
}

// iOS only allows speech that starts inside a user gesture. unlockSpeech() is called
// synchronously from the tap/submit handler; the real answer is spoken after the fetch.
let ruVoice = null;
function pickVoice() {
  if (!('speechSynthesis' in window)) { return; }
  const voices = speechSynthesis.getVoices();
  ruVoice = voices.find((v) => v.lang === 'ru-RU' && /Milena|Yuri|Katya/i.test(v.name))
    || voices.find((v) => v.lang && v.lang.startsWith('ru')) || null;
}
if ('speechSynthesis' in window) {
  pickVoice();
  speechSynthesis.addEventListener('voiceschanged', pickVoice);
}
// JARVIS voice (the user's own cloned voice, played via Web Audio). Falls back to iOS speech.
let audioCtx = null;
function unlockAudio() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) { return; }
  if (!audioCtx) { audioCtx = new AC(); }
  audioCtx.resume();
  const src = audioCtx.createBufferSource();
  src.buffer = audioCtx.createBuffer(1, 1, 22050);
  src.connect(audioCtx.destination);
  src.start(0);
}
async function playJarvisVoice(text) {
  const r = await fetch('/api/speech', {
    method: 'POST', cache: 'no-store',
    headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!r.ok || !audioCtx) { throw new Error('voice ' + r.status); }
  const buf = await audioCtx.decodeAudioData(await r.arrayBuffer());
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);
  setState('speaking');
  src.onended = () => setState('standby');
  src.start(0);
}

function unlockSpeech() {
  if ($('jarvisvoice').checked) { unlockAudio(); }
  if (!$('speak').checked || !('speechSynthesis' in window)) { return; }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(' ');
  u.volume = 0;
  speechSynthesis.speak(u);
}

function say(text, isError) {
  $('reply').textContent = text;
  if ($('speak').checked && $('jarvisvoice').checked && !isError && token) {
    setState('thinking');
    playJarvisVoice(text).catch(() => iosSay(text, isError));  // voice server off -> iOS voice
    return;
  }
  iosSay(text, isError);
}

function iosSay(text, isError) {
  if (!$('speak').checked || !('speechSynthesis' in window)) { setState(isError ? 'error' : 'standby'); return; }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'ru-RU';
  if (!ruVoice) { pickVoice(); }
  if (ruVoice) { u.voice = ruVoice; }
  u.onstart = () => setState(isError ? 'error' : 'speaking');
  u.onend = u.onerror = () => setState(isError ? 'error' : 'standby');
  speechSynthesis.speak(u);
}

function online(ok, text) {
  $('dot').classList.toggle('off', !ok);
  $('conn').textContent = text;
}

async function connect() {
  try {
    const s = await api('/api/summary');
    show('app');
    online(true, 'в сети · ' + when(s.server_utc));
    return true;
  } catch (e) {
    if (e.message === 'auth') { token = ''; store(''); }
    show('setup');
    $('setup-msg').textContent = e.message === 'auth' ? 'Ключ не подошёл.' : 'Нет связи с JARVIS: ' + e.message;
    return false;
  }
}

async function loadSummary() {
  const s = await api('/api/summary');
  const box = $('summary-body');
  box.replaceChildren();
  const total = Math.max(1, s.actions_24h);
  const rows = [
    ['действия · 24ч', s.actions_24h, 100, 'ok'],
    ['внимание', s.needs_attention_24h, 100 * s.needs_attention_24h / total, s.needs_attention_24h ? 'bad' : 'ok'],
    ['в очереди', s.pending_requests, Math.min(100, s.pending_requests * 10), s.pending_requests ? 'warn' : 'ok'],
  ];
  for (const [label, value, pct, tone] of rows) {
    const g = el('div', 'gauge frame ' + tone);
    const wrap = el('div', 'dialwrap');
    const dial = el('div', 'dial');
    dial.style.setProperty('--p', String(Math.round(pct)));
    wrap.append(dial, el('b', '', String(value)));
    g.append(wrap, el('span', '', label));
    box.append(g);
  }
  box.append(el('p', 'hud note', 'телефон не выполняет действия · подтверждение только за компьютером'));
}

async function loadList(path, listId, render) {
  const data = await api(path);
  const ul = $(listId);
  ul.replaceChildren();
  const items = data.items || data.entries || [];
  if (!items.length) { ul.append(el('li', 'muted', 'Пусто')); }
  for (const item of items) { ul.append(render(item)); }
}

const kind = (s) => 'k-' + (['PASS', 'done', 'pending', 'CANCELLED'].includes(s) ? s : 'other');
function row(main, mainCls, status, utc) {
  const li = el('li', kind(status));
  const meta = el('div', 'meta');
  meta.append(el('span', 'hud', when(utc)), badge(status));
  li.append(el('div', mainCls, main), meta);
  return li;
}
const renderQueue = (i) => row(i.text, '', i.status, i.utc);
const renderAudit = (e) => row(e.task || '?', 'task', e.status || '?', e.utc);

async function openTab(name) {
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.pane').forEach((p) => { p.hidden = p.id !== name; });
  try {
    if (name === 'summary') { await loadSummary(); }
    if (name === 'queue') { await loadList('/api/queue', 'queue-list', renderQueue); }
    if (name === 'audit') { await loadList('/api/audit', 'audit-list', renderAudit); }
  } catch (e) {
    if (e.message === 'auth') { show('setup'); }
  }
}

// Features 7/9/10: one-time file links (fetched with the device key) and PC clipboard text.
function showExtra(r) {
  const box = $('extra');
  if (r.text) {
    box.append(el('pre', '', r.text));
    const copy = el('button', 'soundtest', 'Скопировать на iPhone');
    copy.type = 'button';
    copy.onclick = () => navigator.clipboard.writeText(r.text).then(() => { copy.textContent = 'Скопировано'; });
    box.append(copy);
  }
  if (r.link && /^\/api\/file\/[A-Za-z0-9_-]{20,40}$/.test(r.link)) {
    const open = el('button', 'primary', 'Открыть файл');
    open.type = 'button';
    open.onclick = async () => {
      open.textContent = 'Загрузка…';
      try {
        const res = await fetch(r.link, { headers: { Authorization: 'Bearer ' + token }, cache: 'no-store' });
        if (!res.ok) { throw new Error(res.status === 410 ? 'ссылка уже использована или истекла' : 'HTTP ' + res.status); }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        if (blob.type === 'image/png') {
          const img = el('img');
          img.src = url;
          box.append(img);
        } else {
          const a = el('a', 'soundtest', 'Сохранить файл');
          const cd = res.headers.get('Content-Disposition') || '';
          a.href = url;
          a.download = decodeURIComponent((cd.match(/filename\*=UTF-8''(.+)$/) || [])[1] || 'file');
          box.append(a);
          a.click();
        }
        open.remove();
      } catch (e) { open.textContent = 'Не удалось: ' + e.message; }
    };
    box.append(open);
  }
}

async function ask(ev) {
  ev.preventDefault();
  const text = $('say').value.trim();
  if (!text) { return; }
  unlockSpeech();  // must stay before the first await
  setState('thinking');
  $('extra').replaceChildren();
  try {
    const r = await api('/api/say', { text });
    $('say').value = '';
    online(true, 'в сети');
    showExtra(r);
    say(r.speech);
  } catch (e) {
    online(false, 'нет связи');
    say(e.message === 'auth' ? 'Ключ больше не действует.' : 'Нет связи: ' + e.message, true);
  }
}

async function setAwake(on) {
  try {
    if (on && 'wakeLock' in navigator) { wakeLock = await navigator.wakeLock.request('screen'); }
    if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch (e) { $('awake').checked = false; }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && $('awake').checked) { setAwake(true); }
});

async function pair(code) {
  const r = await fetch('/api/pair', {
    method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, name: 'iPhone' }),
  });
  const data = await r.json();
  if (!r.ok) { throw new Error(data.error || ('HTTP ' + r.status)); }
  return data.token;
}

$('save').onclick = async () => {
  const value = $('key').value.trim();
  $('key').value = '';
  try {
    if (/^\d{6}$/.test(value)) {
      token = await pair(value);  // 6-digit code from the PC console -> own device key
    } else if (value.length >= 32) {
      token = value;  // advanced: master key
    } else {
      $('setup-msg').textContent = 'Введите 6 цифр кода с компьютера.';
      return;
    }
  } catch (e) {
    $('setup-msg').textContent = 'Сопряжение не удалось: ' + e.message;
    return;
  }
  if (await connect()) { store($('remember').checked ? token : ''); }
};
$('forget').onclick = () => { token = ''; store(''); show('setup'); $('setup-msg').textContent = 'Ключ удалён с устройства.'; };
$('ask').onsubmit = ask;
function keyBytes(b64) {
  const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
$('pushon').onclick = async () => {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      say('Уведомления работают, только если открыть JARVIS с экрана «Домой».', true);
      return;
    }
    if (await Notification.requestPermission() !== 'granted') { say('Уведомления не разрешены.', true); return; }
    const reg = await navigator.serviceWorker.ready;
    const { key } = await api('/api/push/key');
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
    await api('/api/push/subscribe', { subscription: sub.toJSON() });
    say('Уведомления включены.');
  } catch (e) {
    say('Не удалось включить уведомления: ' + e.message, true);
  }
};
$('briefing').onclick = async () => {
  unlockSpeech();
  try {
    const b = await api('/api/briefing');
    say(b.text || 'Брифинг ещё не готов.');
  } catch (e) { say('Нет связи: ' + e.message, true); }
};
if (new URLSearchParams(location.search).get('briefing') === '1') {
  // Opened from the morning notification: show it; audio needs one tap on «Брифинг» (iOS rule).
  setTimeout(() => api('/api/briefing').then((b) => { $('reply').textContent = b.text; }).catch(() => {}), 800);
}

$('soundtest').onclick = () => {
  $('speak').checked = true;
  unlockSpeech();
  say('Звук работает. На связи JARVIS.');
};
$('awake').onchange = (e) => setAwake(e.target.checked);
document.querySelectorAll('.tab').forEach((b) => { b.onclick = () => openTab(b.dataset.tab); });

if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/sw.js').catch(() => {}); }
token = stored();
if (token) { connect(); } else { show('setup'); }
