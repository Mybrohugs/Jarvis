// JARVIS Mobile: works on its own (Claude + Gemini + Google Calendar); the home PC is optional (files).
import * as store from './store.js';
import * as brain from './brain.js';
import * as voice from './voice.js';
import * as google from './google.js';
import * as pc from './pc.js';

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) { n.className = cls; } if (text !== undefined) { n.textContent = text; } return n; };
const orb = window.JarvisOrb;

const STATES = { standby: 'нажмите и говорите', listening: 'слушаю', thinking: 'думаю', speaking: 'говорю', error: 'сбой' };
let phase = 'standby';
function setState(name) { phase = name; $('state').textContent = STATES[name]; orb.setState(name); }
function show(text) { $('reply').textContent = text; }
function voiceNote() {  // say on screen why the phone's own voice was used instead of JARVIS's
  if (voice.lastError) {
    show(($('reply').textContent + ' [звук: ' + voice.lastError + ']').trim());
    pc.report('voice: ' + voice.lastError);
    voice.clearError();
  }
}

// ---------- one exchange: text in, spoken answer out ----------
let busy = false, exchange = null;
let askedAt = 0;  // when the question was finished (finger lifted / sent): the HUD shows the wait until JARVIS's first sound
function handle(text) {
  text = text.trim();
  if (!text || busy) { return Promise.resolve(); }
  busy = true;
  exchange = run(text);
  return exchange;
}
// after an interruption: wait for the stopped answer to wind down, but never for long
const answered = () => Promise.race([exchange || Promise.resolve(), new Promise((ok) => setTimeout(ok, 1500))]);
async function run(text) {
  $('extra').replaceChildren();
  setState('thinking');
  show('');
  if (!askedAt) { askedAt = performance.now(); }
  try {
    const briefing = /^(брифинг|доброе утро|что у меня сегодня)\b/i.test(text);
    let answer;
    const viaPC = async () => {
      $('state').textContent = 'спрашиваю ПК';
      const r = await pc.say(text);
      return r.speech || 'Компьютер не ответил.';
    };
    if (store.off('claude') && pc.knownAlive()) {
      answer = await viaPC();  // Claude was unreachable from this phone a moment ago: straight to the PC
    } else {
      try {
        answer = await brain.ask(briefing ? 'Сделай утренний брифинг: погода сегодня, мои встречи сегодня и завтра утром. ' + text : text);
      } catch (e) {
        // The phone's network may not reach api.anthropic.com ("Connection error"); the paired PC can: ask it.
        if (!pc.knownAlive() || !/connection|load failed|network|fetch/i.test(e.message || '')) { throw e; }
        store.setOff('claude', 30 * 60000);
        pc.report('answer via PC (phone Claude: ' + e.message + ')');
        answer = await viaPC();
      }
    }
    show(answer);
    for (const x of brain.takeExtras()) { if (x.kind === 'file') { fileButton(x.link); } }
    setState('speaking');
    await voice.speak(answer, (l) => orb.setLevel(l), () => { $('lat').textContent = String(Math.round(performance.now() - askedAt)); });
    voiceNote();
    setState('standby');
  } catch (e) {
    show('Сбой: ' + e.message);
    pc.report('answer: ' + e.message);
    setState('error');
  } finally {
    busy = false;
    askedAt = 0;
    orb.setLevel(0);
  }
}

$('ask').onsubmit = (e) => { e.preventDefault(); voice.unlock(); askedAt = performance.now(); const t = $('say').value; $('say').value = ''; handle(t); };

// ---------- push-to-talk: hold the orb or the mic; a short tap starts, the next tap sends ----------
let recording = null;
async function talkDown(e) {
  e.preventDefault();
  voice.unlock();  // inside the gesture (iOS)
  if (recording) { askedAt = performance.now(); voice.stopRecording(); return; }
  if (busy && phase !== 'speaking') { return; }  // still thinking: the answer is on its way
  if (busy) { voice.stop(); await answered(); busy = false; }  // a tap while JARVIS talks: stop and listen at once
  try {
    $('extra').replaceChildren(); show('');
    recording = voice.startRecording((l) => { orb.setLevel(l); $('lvl').textContent = String(Math.round(l * 99)).padStart(2, '0'); });
    setState('listening'); $('mic').classList.add('on');
    const blob = await recording;
    recording = null; $('mic').classList.remove('on'); $('lvl').textContent = '00'; orb.setLevel(0);
    if (blob.size < 2000) { setState('standby'); return; }
    setState('thinking'); $('state').textContent = 'распознаю';
    const text = await voice.transcribe(blob);
    if (!text) { show('Не расслышал, сэр.'); setState('standby'); return; }
    await handle(text);
  } catch (err) {
    recording = null; $('mic').classList.remove('on');
    show(err.name === 'NotAllowedError' ? 'Нет доступа к микрофону: разрешите его в настройках телефона.' : 'Сбой: ' + err.message);
    pc.report(($('state').textContent === 'распознаю' ? 'transcribe: ' : 'mic: ') + err.message);
    setState('error');
  }
}
function talkUp() { if (recording && voice.heldFor() > 600) { askedAt = performance.now(); voice.stopRecording(); } }
for (const id of ['reactor', 'mic']) {
  $(id).addEventListener('pointerdown', talkDown);
  $(id).addEventListener('pointerup', talkUp);
  $(id).addEventListener('pointercancel', talkUp);
  $(id).addEventListener('contextmenu', (e) => e.preventDefault());
}

// ---------- files ----------
function fileButton(link) {
  const b = el('button', 'wide', 'Сохранить файл с компьютера');
  b.type = 'button';
  b.onclick = async () => {
    b.textContent = 'Загрузка…';
    try {
      const { name, blob } = await pc.download(link);
      const file = new File([blob], name, { type: blob.type || 'application/octet-stream' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file] }); }  // iOS: «Сохранить в Файлы»
      else { const a = el('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); }
      b.textContent = 'Готово: ' + name;
    } catch (e) { b.textContent = 'Не удалось: ' + e.message; }
  };
  $('extra').append(b);
}

$('upload').onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  if (!files.length) { return; }
  if (!(await pc.reachable(true))) { $('upmsg').textContent = 'Компьютер недоступен: он выключен или на телефоне не включён Tailscale.'; return; }
  for (const f of files) {
    $('upmsg').textContent = 'Отправляю ' + f.name + '…';
    try { const r = await pc.upload(f); $('upmsg').textContent = 'Сохранено на компьютере: ' + r.saved_as; }
    catch (err) { $('upmsg').textContent = 'Не удалось (' + f.name + '): ' + err.message; return; }
  }
};

$('findform').onsubmit = async (e) => {
  e.preventDefault();
  const q = $('findq').value.trim();
  const ul = $('findlist');
  ul.replaceChildren(el('li', 'muted', 'Ищу…'));
  try {
    if (!(await pc.reachable(true))) { throw new Error('компьютер недоступен'); }
    const r = await pc.find(q);
    ul.replaceChildren();
    const lines = (r.text || '').split('\n').filter(Boolean);
    if (!lines.length) { ul.append(el('li', 'muted', r.speech || 'Ничего не нашёл.')); return; }
    lines.forEach((line, i) => {
      const li = el('li', '', line);
      const b = el('button', '', 'На телефон');
      b.type = 'button';
      b.onclick = async () => {
        b.textContent = 'Готовлю…';
        try { const s = await pc.send(i + 1); if (!s.link) { throw new Error(s.speech || 'нет ссылки'); }
          const { name, blob } = await pc.download(s.link);
          const file = new File([blob], name, { type: blob.type || 'application/octet-stream' });
          if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file] }); }
          else { const a = el('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); }
          b.textContent = 'Готово';
        } catch (err) { b.textContent = 'Не удалось: ' + err.message; }
      };
      li.append(el('br'), b);
      ul.append(li);
    });
  } catch (err) { ul.replaceChildren(el('li', 'muted', 'Не получилось: ' + err.message)); }
};

// ---------- calendar ----------
async function loadCalendar() {
  const ul = $('callist');
  if (!google.configured()) { ul.replaceChildren(el('li', 'muted', 'Укажите Client ID Google в настройках.')); return; }
  if (!google.signedIn()) { ul.replaceChildren(el('li', 'muted', 'Нажмите «Обновить», чтобы войти в Google.')); return; }
  ul.replaceChildren(el('li', 'muted', 'Загружаю…'));
  try {
    const from = new Date(); from.setHours(0, 0, 0, 0);
    const to = new Date(from.getTime() + 7 * 86400000);
    const items = await google.events(from.toISOString(), to.toISOString(), 50);
    ul.replaceChildren();
    if (!items.length) { ul.append(el('li', 'muted', 'На неделю встреч нет.')); return; }
    let day = '';
    for (const it of items) {
      const d = new Date(it.start);
      const label = d.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
      if (label !== day) { day = label; ul.append(el('li', 'day', label)); }
      const li = el('li');
      li.append(el('div', 'when', it.allDay ? 'весь день' : d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })),
        el('div', 'task', it.title));
      if (it.where) { li.append(el('div', 'muted small', it.where)); }
      ul.append(li);
    }
  } catch (e) { ul.replaceChildren(el('li', 'muted', 'Не получилось: ' + e.message)); }
}
$('calrefresh').onclick = async () => {
  if (google.configured() && !google.signedIn()) {
    try { await google.signIn(); } catch (e) { $('callist').replaceChildren(el('li', 'muted', 'Вход не выполнен: ' + e.message)); return; }
  }
  loadCalendar();
};

// ---------- tabs & settings ----------
document.querySelectorAll('.tab').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('.pane').forEach((p) => { p.hidden = p.id !== b.dataset.tab; });
    if (b.dataset.tab === 'calendar') { loadCalendar(); }
  };
});

const FORM = { claudekey: 'claudeKey', geminikey: 'geminiKey', model: 'model', gclient: 'googleClientId', pcurl: 'pcUrl', city: 'city' };
function fillSettings() {
  for (const [id, key] of Object.entries(FORM)) { $(id).value = store.get(key); }
  $('speak').checked = store.get('speak') === '1';
  $('gstate').textContent = !google.configured() ? 'Не настроено.' : google.signedIn() ? 'Вход выполнен.' : 'Нужно войти.';
  $('pcmsg').textContent = pc.configured() ? 'Компьютер подключён.' : 'Не подключён (нужен только для файлов).';
}
function settings(open) { if (open) { fillSettings(); } $('settings').hidden = !open; }
$('gear').onclick = () => settings(true);
$('close').onclick = () => settings(false);
$('settings').onclick = (e) => { if (e.target === $('settings')) { settings(false); } };
$('save').onclick = async () => {
  for (const [id, key] of Object.entries(FORM)) { if (id !== 'pcurl' || $(id).value.trim()) { store.set(key, $(id).value.trim()); } }
  store.set('speak', $('speak').checked ? '1' : '0');
  settings(false);
  status();
};
$('glogin').onclick = async () => {
  store.set('googleClientId', $('gclient').value.trim());
  $('gstate').textContent = 'Открываю Google…';
  try { await google.signIn(); }
  catch (e) { $('gstate').textContent = 'Не получилось: ' + e.message; }
};
$('pcpair').onclick = async () => {
  $('pcmsg').textContent = 'Подключаю…';
  try { await pc.pair($('pcurl').value, $('pccode').value); $('pccode').value = ''; $('pcmsg').textContent = 'Компьютер подключён.'; status(); }
  catch (e) { $('pcmsg').textContent = 'Не получилось: ' + e.message; }
};
$('soundtest').onclick = () => { voice.unlock(); settings(false); setState('speaking'); show(''); voice.speak('Звук работает. На связи JARVIS.', (l) => orb.setLevel(l)).then(() => { voiceNote(); setState('standby'); }); };
$('briefing').onclick = () => { voice.unlock(); settings(false); handle('брифинг'); };
$('forget').onclick = () => {
  if (!confirm('Стереть с этого телефона все ключи и настройки JARVIS?')) { return; }
  store.wipe(); google.signOut(); try { sessionStorage.clear(); } catch (e) { /* ignore */ }
  brain.reset(); fillSettings(); status();
};
let wakeLock = null;
$('awake').onchange = async (e) => {
  try { if (e.target.checked) { wakeLock = await navigator.wakeLock.request('screen'); } else if (wakeLock) { await wakeLock.release(); wakeLock = null; } }
  catch (err) { e.target.checked = false; }
};

// ---------- Siri: the Shortcut posts to the PC's /api/say with this phone's PC key ----------
$('siricopy').onclick = async () => {
  if (!pc.configured()) { $('sirimsg').textContent = 'Сначала подключите компьютер.'; return; }
  const url = store.get('pcUrl').replace(/\/+$/, '') + '/api/say';
  try {
    await navigator.clipboard.writeText(url + '\nBearer ' + store.get('pcToken'));
    $('sirimsg').textContent = 'Скопировано: первая строка — адрес, вторая — ключ. Это пароль к ПК, никому не пересылайте.';
  } catch (e) { $('sirimsg').textContent = 'Не удалось скопировать: ' + e.message; }
};

// ---------- Web Push from the PC ----------
function keyBytes(b64) {
  const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
$('pushon').onclick = async () => {
  const msg = (t) => { $('pushmsg').textContent = t; };
  try {
    if (!pc.configured()) { msg('Сначала подключите компьютер.'); return; }
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      msg('Уведомления работают, только если открыть JARVIS с экрана «Домой» (Поделиться → На экран «Домой»).');
      return;
    }
    if (await Notification.requestPermission() !== 'granted') { msg('Уведомления не разрешены в настройках iPhone.'); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(await pc.pushKey()) });
    await pc.pushSubscribe(sub.toJSON());
    msg('Уведомления включены.');
    pc.report('push subscribed');
  } catch (e) {
    msg('Не удалось включить: ' + e.message);
    pc.report('push: ' + e.message);
  }
};

// ---------- debug console (Eruda, bundled in vendor/, loaded only when switched on) ----------
function debugConsole(on) {
  if (on && !window.eruda) {
    const s = document.createElement('script');
    s.src = 'vendor/eruda.js';
    s.onload = () => { window.eruda.init({ tool: ['console', 'network', 'resources', 'info'] }); };
    document.head.appendChild(s);
  } else if (!on && window.eruda) {
    window.eruda.destroy();
    delete window.eruda;
  }
}
$('debug').checked = store.get('debug') === '1';
$('debug').onchange = (e) => { store.set('debug', e.target.checked ? '1' : ''); debugConsole(e.target.checked); };
debugConsole(store.get('debug') === '1');

// ---------- status line & clock ----------
async function status() {
  const up = await pc.reachable(true);
  $('pcstate').textContent = !pc.configured() ? 'не подключён' : up ? 'на связи' : 'нет связи';
  $('dot').classList.toggle('off', !store.get('claudeKey'));
  $('conn').textContent = store.get('claudeKey') ? 'в сети' : 'нет ключа';
}
function clock() {
  const d = new Date();
  $('clock').textContent = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  $('date').textContent = d.toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' });
}
clock(); setInterval(clock, 15000);
setInterval(status, 60000);
orb.mount($('orb'));
status();
if (google.lastError) { show('Вход в Google не выполнен: ' + google.lastError + '.'); }
else if (location.hash === '' && google.signedIn() && sessionStorage.getItem('jarvis.gshown') !== '1') {
  try { sessionStorage.setItem('jarvis.gshown', '1'); } catch (e) { /* ignore */ }
  show('Вход в Google выполнен, сэр. Календарь подключён.');
} else if (!store.get('claudeKey')) { show('Добро пожаловать, сэр. Откройте настройки (шестерёнка) и введите ключ Claude.'); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('sw.js').catch(() => {}); }
