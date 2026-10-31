'use strict';
// JARVIS phone PWA. Read + queue only; all output via textContent (no HTML from data).
const $ = (id) => document.getElementById(id);
const KEY = 'jarvis.pwa.key';
let token = '';
let wakeLock = null;

function stored() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } }
function store(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) { /* private mode */ } }

async function api(path, body) {
  const ctl = new AbortController();  // never hang forever: the PC waits at most 60 s for an answer
  const timer = setTimeout(() => ctl.abort(), 75000);
  const opts = { headers: { Authorization: 'Bearer ' + token }, cache: 'no-store', signal: ctl.signal };
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let r;
  try { r = await fetch(path, opts); } catch (e) {
    throw new Error(e.name === 'AbortError' ? 'компьютер не ответил за 75 с' : e.message);
  } finally { clearTimeout(timer); }
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

const STATES = { standby: 'нажмите и говорите', listening: 'слушаю', thinking: 'обработка', speaking: 'говорю', error: 'сбой' };
function setState(name) {
  const r = $('reactor');
  r.classList.remove('listening', 'thinking', 'speaking', 'error');
  if (name !== 'standby') { r.classList.add(name); }
  $('state').textContent = STATES[name];
  if (window.JarvisOrb) { JarvisOrb.setState(name); }
}

// iOS only allows speech that starts inside a user gesture. unlockSpeech() is called
// synchronously from the tap/submit handler; the real answer is spoken after the fetch.
let ruVoice = null;
function pickVoice() {
  if (!('speechSynthesis' in window)) { return; }
  const voices = speechSynthesis.getVoices();
  // a male voice when the JARVIS voice is unavailable: Yuri first (iOS: Settings > Accessibility >
  // Spoken Content > Voices > Russian > Yuri), then any Russian voice, pitched down a little
  const ru = voices.filter((v) => v.lang && v.lang.startsWith('ru'));
  ruVoice = ru.find((v) => /Yuri|Юрий|Maxim|Pavel|Dmitri/i.test(v.name) && /enhanced|premium|улучш/i.test(v.name))
    || ru.find((v) => /Yuri|Юрий|Maxim|Pavel|Dmitri/i.test(v.name)) || ru[0] || null;
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
// Long answers are spoken sentence by sentence: the voice server takes <= 400 characters and iOS
// speechSynthesis silently stops long utterances. Each part is fetched while the previous one plays.
function splitSpeech(text, max = 220) {
  const parts = [];
  for (const s of String(text).replace(/\s+/g, ' ').trim().match(/[^.!?…]+[.!?…]*\s*/g) || []) {
    let rest = s.trim();
    while (rest.length > max) {  // one very long sentence: cut at the last comma/space before max
      let cut = Math.max(rest.lastIndexOf(', ', max), rest.lastIndexOf(' ', max));
      if (cut < max / 2) { cut = max; }
      parts.push(rest.slice(0, cut + 1).trim());
      rest = rest.slice(cut + 1).trim();
    }
    const last = parts[parts.length - 1];
    if (rest && last && last.length + rest.length < 120) { parts[parts.length - 1] = last + ' ' + rest; }  // glue short bits
    else if (rest) { parts.push(rest); }
  }
  return parts;
}
let speechRun = 0;  // a new answer (or barge-in) stops the old one
async function fetchVoice(text, ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch('/api/speech', {
      method: 'POST', cache: 'no-store', signal: ctl.signal,
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok || !audioCtx) { throw new Error('voice ' + r.status); }
    return await audioCtx.decodeAudioData(await r.arrayBuffer());
  } finally { clearTimeout(timer); }
}
function playBuffer(buf, run) {
  return new Promise((resolve) => {
    if (run !== speechRun) { resolve(); return; }
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    const an = audioCtx.createAnalyser();
    an.fftSize = 512;
    src.connect(an);
    an.connect(audioCtx.destination);
    const data = new Uint8Array(an.fftSize);
    let playing = true;
    const follow = () => {
      if (!playing) { return; }
      if (run !== speechRun) { playing = false; try { src.stop(); } catch (e) { /* already stopped */ } return; }
      an.getByteTimeDomainData(data);
      let peak = 0;
      for (const v of data) { peak = Math.max(peak, Math.abs(v - 128)); }
      if (window.JarvisOrb) { JarvisOrb.setLevel(Math.min(1, peak / 70)); }
      requestAnimationFrame(follow);
    };
    src.onended = () => { playing = false; if (window.JarvisOrb) { JarvisOrb.setLevel(0); } resolve(); };
    setState('speaking');
    audioMode('playback');
    audioCtx.resume();  // iOS suspends the context during a long wait for the answer
    src.start(0);
    follow();
  });
}
// The whole answer is one WAV: per-sentence requests left silent gaps (Gemini needs 7-13 s per
// sentence) and burnt the free daily quota. Long answers are voiced by Piper on the PC in ~5 s.
// Errors are reported to the PC log (%LOCALAPPDATA%\JARVIS\gateway.log) as a short label, so a
// failure on the phone can be diagnosed without guessing.
function report(event) {
  if (!token) { return; }
  fetch('/api/clientlog', {
    method: 'POST', cache: 'no-store',
    headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
    body: JSON.stringify({ event: String(event).slice(0, 120) }),
  }).catch(() => {});
}
// iOS routes sound to the quiet earpiece while the page may record; switch back for speaking.
function audioMode(type) {
  try { if (navigator.audioSession) { navigator.audioSession.type = type; } } catch (e) { /* older iOS */ }
}
async function playJarvisVoice(text) {
  const run = ++speechRun;
  let buf;
  try { buf = await fetchVoice(text, 60000); } catch (e) {
    report('voice failed: ' + (e.name === 'AbortError' ? 'timeout' : e.message));
    if (run === speechRun) { iosSay(text); }  // voice server off -> iOS voice, sentence by sentence
    return;
  }
  await playBuffer(buf, run);
  if (run === speechRun) { setState('standby'); }
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
  if (isError) { report('error shown: ' + text); }
  if ($('speak').checked && $('jarvisvoice').checked && !isError && token) {
    setState('thinking');
    playJarvisVoice(text).catch(() => iosSay(text, isError));  // voice server off -> iOS voice
    return;
  }
  iosSay(text, isError);
}

function iosSay(text, isError) {
  if (!$('speak').checked || !('speechSynthesis' in window)) { setState(isError ? 'error' : 'standby'); return; }
  const run = ++speechRun;
  speechSynthesis.cancel();
  audioMode('playback');
  if (!ruVoice) { pickVoice(); }
  const parts = splitSpeech(text, 160);
  // iOS sometimes never fires onend: give up after a generous estimate so the app never hangs
  setTimeout(() => {
    if (run === speechRun && (speechSynthesis.speaking || speechSynthesis.pending)) {
      speechSynthesis.cancel();
      report('ios speech stuck');
      setState(isError ? 'error' : 'standby');
    }
  }, 8000 + String(text).length * 110);
  // one utterance per sentence, queued: a single long utterance is cut off by iOS
  parts.forEach((part, i) => {
    const u = new SpeechSynthesisUtterance(part);
    u.lang = 'ru-RU';
    if (ruVoice) { u.voice = ruVoice; }
    u.pitch = ruVoice && /Yuri|Юрий|Maxim|Pavel|Dmitri/i.test(ruVoice.name) ? 0.9 : 0.6;
    u.onstart = () => { if (run === speechRun) { setState(isError ? 'error' : 'speaking'); } };
    if (i === parts.length - 1) { u.onend = () => { if (run === speechRun) { setState(isError ? 'error' : 'standby'); } }; }
    u.onerror = () => { if (run === speechRun) { setState(isError ? 'error' : 'standby'); } };
    speechSynthesis.speak(u);
  });
  if (!parts.length) { setState(isError ? 'error' : 'standby'); }
}
function stopSpeech() {  // barge-in: pressing the reactor silences JARVIS at once
  speechRun++;
  if ('speechSynthesis' in window) { speechSynthesis.cancel(); }
  if (window.JarvisOrb) { JarvisOrb.setLevel(0); }
}

function online(ok, text) {
  $('dot').classList.toggle('off', !ok);
  $('conn').textContent = text;
}

async function connect() {
  try {
    const s = await api('/api/summary');
    show('app');
    online(true, 'в сети');
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

let asking = false;  // one question at a time: a slow answer must not be sent again
async function ask(ev) {
  ev.preventDefault();
  const text = $('say').value.trim();
  if (!text || asking) { return; }
  asking = true;
  unlockSpeech();  // must stay before the first await
  setState('thinking');
  $('reply').textContent = 'Думаю… (обычный вопрос — до минуты)';
  $('extra').replaceChildren();
  try {
    const t0 = performance.now();
    const r = await api('/api/say', { text });
    $('lat').textContent = String(Math.round(performance.now() - t0));
    $('say').value = '';
    online(true, 'в сети');
    showExtra(r);
    say(r.speech);
  } catch (e) {
    online(false, 'нет связи');
    say(e.message === 'auth' ? 'Ключ больше не действует.' : 'Нет связи: ' + e.message, true);
  } finally { asking = false; }
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
$('forget').onclick = () => { token = ''; store(''); settings(false); show('setup'); $('setup-msg').textContent = 'Ключ удалён с устройства.'; };
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
  settings(false);
  try {
    const b = await api('/api/briefing');
    say(b.text || 'Брифинг ещё не готов.');
  } catch (e) { say('Нет связи: ' + e.message, true); }
};
if (new URLSearchParams(location.search).get('briefing') === '1') {
  // Opened from the morning notification: show it; audio needs one tap on «Брифинг» (iOS rule).
  setTimeout(() => api('/api/briefing').then((b) => { $('reply').textContent = b.text; }).catch(() => {}), 800);
}

function settings(open) { $('settings').hidden = !open; }
$('gear').onclick = () => settings(true);
$('close').onclick = () => settings(false);
$('settings').onclick = (e) => { if (e.target === $('settings')) { settings(false); } };
document.querySelectorAll('.chips [data-phrase]').forEach((b) => {
  b.onclick = () => { settings(false); $('say').value = b.dataset.phrase; ask({ preventDefault() {} }); };
});

// Phone -> PC: picked files go one by one to /api/upload (raw body); the PC saves them to
// «Загрузки\JARVIS с телефона» and never opens them.
const MAX_UPLOAD = 50 * 1000 * 1000;
async function uploadOne(file) {
  if (!file.size || file.size > MAX_UPLOAD) { throw new Error(file.name + ': размер от 1 байта до 50 МБ'); }
  const r = await fetch('/api/upload', {
    method: 'POST', body: file, cache: 'no-store',
    headers: { Authorization: 'Bearer ' + token, 'X-Filename': encodeURIComponent(file.name || 'file'),
               'Content-Type': 'application/octet-stream' },
  });
  if (r.status === 401) { throw new Error('auth'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { throw new Error(data.error || ('HTTP ' + r.status)); }
  return data.saved_as || file.name;
}
async function uploadPicked() {
  const files = Array.from($('pickfile').files || []);
  $('pickfile').value = '';
  if (!files.length) { return; }
  unlockSpeech();
  setState('thinking');
  $('extra').replaceChildren();
  const done = [];
  try {
    for (const [i, f] of files.entries()) {
      $('reply').textContent = 'Отправляю на ПК ' + (i + 1) + ' из ' + files.length + '…';
      done.push(await uploadOne(f));
    }
    online(true, 'в сети');
    say(done.length === 1 ? 'Файл «' + done[0] + '» на компьютере, в папке «JARVIS с телефона».'
                          : 'Файлов на компьютере: ' + done.length + ', в папке «JARVIS с телефона».');
  } catch (e) {
    say(e.message === 'auth' ? 'Ключ больше не действует.'
        : 'Отправлено ' + done.length + ' из ' + files.length + '. Ошибка: ' + e.message, true);
  }
}
$('pickfile').onchange = uploadPicked;
$('topc').onclick = () => $('pickfile').click();
$('topc2').onclick = () => { settings(false); $('pickfile').click(); };
$('findpc').onclick = () => { settings(false); $('say').value = 'найди на компьютере '; $('say').focus(); };
// Share sheet: an iOS Shortcut posts the shared item to the same /api/upload with this device's key.
function copyTo(btn, text, label) {
  navigator.clipboard.writeText(text).then(() => { btn.textContent = label; }, () => { btn.textContent = 'не удалось'; });
}
$('copyurl').onclick = () => copyTo($('copyurl'), location.origin + '/api/upload', 'адрес скопирован');
$('copysay').onclick = () => copyTo($('copysay'), location.origin + '/api/say', 'адрес скопирован');
// A YouTube link copied on the iPhone -> the TV. iOS asks once to allow pasting.
$('totv').onclick = async () => {
  settings(false);
  let text = '';
  try { text = await navigator.clipboard.readText(); } catch (e) { /* not allowed: type it */ }
  const link = (text.match(/https?:\/\/\S+/) || [])[0];
  $('say').value = 'на телевизор ' + (link || '');
  if (link) { ask({ preventDefault() {} }); } else { $('say').focus(); }
};
$('copykey').onclick = () => copyTo($('copykey'), 'Bearer ' + token, 'ключ скопирован');

// Push-to-talk: the phone records, the PC transcribes (/api/listen, text only), then it is sent like typed text.
// Hold the reactor or the mic to talk and release to send; a short tap starts, the next tap sends.
let rec = null, recStream = null, recStart = 0, recTimer = 0, meter = 0;
function micUI(on) {
  $('mic').classList.toggle('on', on);
  if (!on) { if (window.JarvisOrb) { JarvisOrb.setLevel(0); } $('lvl').textContent = '00'; }
}
async function startListen() {
  if (!navigator.mediaDevices || !window.MediaRecorder) { say('Этот iPhone не даёт записывать звук в приложении.', true); return; }
  stopSpeech();  // talking over JARVIS silences it at once (both voices)
  audioMode('play-and-record');
  unlockSpeech();  // must stay before the first await (iOS gesture rule)
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch (e) { say('Нет доступа к микрофону: разрешите его в настройках iPhone.', true); return; }
  const type = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  const chunks = [];
  rec = new MediaRecorder(recStream, type ? { mimeType: type } : undefined);
  rec.ondataavailable = (e) => { if (e.data.size) { chunks.push(e.data); } };
  rec.onstop = () => sendAudio(new Blob(chunks, { type: (rec.mimeType || type || 'audio/mp4').split(';')[0] }));
  rec.start();
  recStart = Date.now();
  recTimer = setTimeout(stopListen, 20000);
  $('reply').textContent = '';
  $('extra').replaceChildren();
  setState('listening');
  micUI(true);
  const AC = window.AudioContext || window.webkitAudioContext;
  if (AC) {
    if (!audioCtx) { audioCtx = new AC(); }
    const an = audioCtx.createAnalyser();
    an.fftSize = 512;
    audioCtx.createMediaStreamSource(recStream).connect(an);
    const buf = new Uint8Array(an.fftSize);
    const tick = () => {
      if (!rec) { return; }
      an.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) { peak = Math.max(peak, Math.abs(v - 128)); }
      const lvl = Math.min(1, peak / 64);
      if (window.JarvisOrb) { JarvisOrb.setLevel(lvl); }
      $('lvl').textContent = String(Math.round(lvl * 99)).padStart(2, '0');
      meter = requestAnimationFrame(tick);
    };
    tick();
  }
}
function stopListen() {
  clearTimeout(recTimer);
  cancelAnimationFrame(meter);
  if (rec && rec.state !== 'inactive') { rec.stop(); }
  if (recStream) { recStream.getTracks().forEach((t) => t.stop()); recStream = null; }
  audioMode('playback');  // back to the loud speaker for the answer
  micUI(false);
}
async function sendAudio(blob) {
  rec = null;
  if (blob.size < 2000) { setState('standby'); return; }  // a stray tap, nothing said
  setState('thinking');
  try {
    const r = await fetch('/api/listen', {
      method: 'POST', cache: 'no-store', body: blob,
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': blob.type || 'audio/mp4' },
    });
    const data = await r.json();
    if (!r.ok) { throw new Error(r.status === 429 ? 'JARVIS ещё слушает прошлую фразу' : (data.error || 'HTTP ' + r.status)); }
    if (!data.text) { say('Не расслышал, сэр.', true); return; }
    $('say').value = data.text;
    ask({ preventDefault() {} });
  } catch (e) { say('Не получилось распознать: ' + e.message, true); }
}
function talkDown(e) {
  e.preventDefault();
  if (rec) { stopListen(); return; }  // second tap of tap-to-talk
  startListen();
}
function talkUp() { if (rec && Date.now() - recStart > 600) { stopListen(); } }  // released after holding
for (const id of ['reactor', 'mic']) {
  $(id).addEventListener('pointerdown', talkDown);
  $(id).addEventListener('pointerup', talkUp);
  $(id).addEventListener('pointercancel', talkUp);
  $(id).addEventListener('contextmenu', (e) => e.preventDefault());
}

function clock() {
  const d = new Date();
  $('clock').textContent = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  $('date').textContent = d.toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' });
}
clock();
setInterval(clock, 15000);

$('soundtest').onclick = () => {
  $('speak').checked = true;
  unlockSpeech();
  settings(false);
  say('Звук работает. На связи JARVIS.');
};
$('awake').onchange = (e) => setAwake(e.target.checked);
document.querySelectorAll('.tab').forEach((b) => { b.onclick = () => openTab(b.dataset.tab); });

if (window.JarvisOrb) { JarvisOrb.mount($('orb')); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/sw.js').catch(() => {}); }
token = stored();
if (token) { connect(); } else { show('setup'); }
