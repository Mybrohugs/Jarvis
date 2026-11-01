// Voice in and out. In: record -> PC Whisper (if the PC is known to be on) or Gemini transcription.
// Out: Gemini TTS streamed to the phone + the PC's robot-3 effect ported to JS -> the phone's own
// male voice as the last resort.
import * as store from './store.js';
import * as pc from './pc.js';

const G = 'https://generativelanguage.googleapis.com';
// each TTS model has its own free daily quota; a 429 moves on with the same voice (as on the PC)
const TTS = [['gemini-3.8-flash-tts', true], ['gemini-3.8-flash-lite-tts', true],
  ['gemini-3.1-flash-tts-preview', false], ['gemini-2.5-flash-preview-tts', false]];
const STT = ['gemini-3.5-flash', 'gemini-flash-latest'];
const VOICE = 'Schedar';
const STYLE = 'Спокойный, вежливый и уверенный британский дворецкий-ассистент: ровный бархатный голос, лёгкая ирония, без спешки, чёткая дикция.';
const INLINE = 'Say in a calm, warm, low British butler voice, in Russian: ';

// iPhone: 'playback' keeps the voice audible with the ring/silent switch on, but it cannot record,
// so the microphone switches to 'play-and-record' and back. Older iOS has no audioSession: no-op.
function session(type) {
  try { if (navigator.audioSession && navigator.audioSession.type !== type) { navigator.audioSession.type = type; } } catch (e) { /* ignore */ }
}

let ctx = null;
export function unlock() {  // call inside a tap: iOS allows sound only after a gesture
  const AC = window.AudioContext || window.webkitAudioContext;
  session('playback');
  unlockPlayer();
  if (!AC) { return; }
  if (!ctx) { ctx = new AC(); }
  ctx.resume();
  const src = ctx.createBufferSource();
  src.buffer = ctx.createBuffer(1, 1, 22050);
  src.connect(ctx.destination);
  src.start(0);
  if ('speechSynthesis' in window) { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(' '); u.volume = 0; speechSynthesis.speak(u); }
}
export const audio = () => ctx;

// ---------- recording ----------
let rec = null, stream = null, started = 0;
export const recording = () => !!rec;
export async function startRecording(onLevel) {
  session('play-and-record');
  // Push-to-talk needs no echo cancelling (JARVIS is silent while you speak), and on the iPhone it puts the
  // audio into call mode, which turns the following playback down (WebKit bug 218012).
  stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  const type = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  const chunks = [];
  const r = rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);  // stopRecording() clears rec before onstop
  r.ondataavailable = (e) => { if (e.data.size) { chunks.push(e.data); } };
  const done = new Promise((ok) => { r.onstop = () => ok(new Blob(chunks, { type: (r.mimeType || type || 'audio/mp4').split(';')[0] })); });
  r.start();
  started = Date.now();
  if (ctx && onLevel) {
    const an = ctx.createAnalyser();
    an.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Uint8Array(an.fftSize);
    const tick = () => {
      if (!rec) { onLevel(0); return; }
      an.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) { peak = Math.max(peak, Math.abs(v - 128)); }
      onLevel(Math.min(1, peak / 64));
      requestAnimationFrame(tick);
    };
    tick();
  }
  return done;
}
export function heldFor() { return Date.now() - started; }
export function stopRecording() {
  if (rec && rec.state !== 'inactive') { rec.stop(); }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  rec = null;
  session('playback');
}

const b64 = (buf) => { let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) { s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); } return btoa(s); };

export async function transcribe(blob) {
  if (pc.knownAlive()) {  // never waits for an offline PC
    try { return await pc.listen(blob); } catch (e) { /* fall through to Gemini */ }
  }
  const key = store.get('geminiKey');
  if (!key) { throw new Error('для голоса без компьютера нужен ключ Gemini (Настройки)'); }
  if (store.off('gemini')) { throw new Error('Gemini недоступен из этой сети, а компьютер не ответил'); }
  const data = b64(await blob.arrayBuffer());
  let last = null;
  for (const model of STT) {
    const r = await fetch(`${G}/v1beta/models/${model}:generateContent`, {
      method: 'POST', headers: { 'x-goog-api-key': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ contents: [{ parts: [{ inline_data: { mime_type: blob.type || 'audio/mp4', data } },
        { text: 'Запиши дословно, что сказано (русский язык). Только сам текст, без пояснений. Если речи нет — пустой ответ.' }] }],
        // dictation needs no reasoning: measured 3.8 s -> 2.4 s with the same text
        ...(model.startsWith('gemini-3') ? { generationConfig: { thinkingConfig: { thinkingLevel: 'minimal' } } } : {}) }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok) { return ((((d.candidates || [])[0] || {}).content || {}).parts || []).map((p) => p.text || '').join('').trim(); }
    last = new Error((d.error && d.error.message) || 'Gemini HTTP ' + r.status);
    if (geoRefused(last)) { break; }
    if (r.status !== 429 && r.status !== 503) { break; }
  }
  throw last || new Error('распознавание Gemini недоступно');
}

// ---------- speaking ----------
function rest() { try { return JSON.parse(localStorage.getItem('jarvis.mobile.ttsRest') || '{}'); } catch (e) { return {}; } }
function setRest(m) { const r = rest(); r[m] = Date.now() + 30 * 60000; try { localStorage.setItem('jarvis.mobile.ttsRest', JSON.stringify(r)); } catch (e) { /* ignore */ } }

// Gemini refuses this network outright ("not available in your current location"): no model will answer,
// so Gemini is skipped for an hour and the PC does the voice.
function geoRefused(e) { if (/location|region/i.test(e.message)) { store.setOff('gemini'); return true; } return false; }

export let lastError = '';  // why the phone fell back to its own voice (shown once on screen)
export function clearError() { lastError = ''; }
export async function geminiSpeech(text) {
  const key = store.get('geminiKey');
  if (!key) { throw new Error('no gemini key'); }
  if (store.off('gemini')) { throw new Error('Gemini недоступен из этой сети'); }
  let last = null;
  for (const [model, meta] of TTS) {
    if ((rest()[model] || 0) > Date.now()) { continue; }
    const content = meta ? { type: 'text', text, annotations: [{ type: 'speech_metadata', style: STYLE }] } : { type: 'text', text: INLINE + text };
    const r = await fetch(`${G}/v1beta/interactions`, {
      method: 'POST', headers: { 'x-goog-api-key': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, input: [{ type: 'user_input', content: [content] }], response_format: { type: 'audio' },
        generation_config: { speech_config: [{ voice: VOICE }] } }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok) {
      for (const step of d.steps || []) {
        for (const part of step.content || []) { if (part && part.data) { return pcmOrWav(part.data); } }
      }
      last = new Error('Gemini returned no audio');
      continue;
    }
    last = new Error((d.error && d.error.message) || 'Gemini HTTP ' + r.status);
    if (r.status === 429) { setRest(model); }
    if (geoRefused(last)) { break; }
  }
  throw last || new Error('голос Gemini на отдыхе: дневной лимит исчерпан');
}

export function pcmOrWav(data) {  // base64 from Gemini -> {samples: Float32Array, rate}
  const bin = atob(data), u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) { u[i] = bin.charCodeAt(i); }
  return parseWav(u);
}

export function parseWav(u) {  // WAV bytes (or raw 16-bit 24 kHz PCM) -> {samples, rate}
  let off = 0, end = u.length, rate = 24000;
  if (u[0] === 0x52 && u[1] === 0x49 && u[2] === 0x46 && u[3] === 0x46) {
    // RIFF: read only the "data" chunk. Gemini appends other chunks after it (a C2PA signature) that
    // would otherwise play as a loud burst and squash the speech when the level is normalised.
    const dv = new DataView(u.buffer, u.byteOffset, u.byteLength);
    let p = 12, found = false;
    while (p + 8 <= u.length) {
      const id = String.fromCharCode(u[p], u[p + 1], u[p + 2], u[p + 3]), size = dv.getUint32(p + 4, true);
      if (id === 'fmt ') { rate = dv.getUint32(p + 12, true); }
      if (id === 'data') { off = p + 8; end = Math.min(u.length, off + size); found = true; break; }
      p += 8 + size + (size & 1);
    }
    if (!found) { throw new Error('WAV without audio'); }
  }
  const n = Math.floor((end - off) / 2), out = new Float32Array(n), dv = new DataView(u.buffer, u.byteOffset + off);
  for (let i = 0; i < n; i++) { out[i] = dv.getInt16(i * 2, true) / 32768; }
  return { samples: out, rate };
}

// robot-3 from the PC (voice_dsp.film): metal combs + 60 Hz ring, EQ, compressor, small room,
// digital double, hint of metal, normalised to 0.89.
function biquad(x, rate, type, f, gainDb = 0, q = 0.707) {
  const A = Math.pow(10, gainDb / 40), w = 2 * Math.PI * f / rate, cs = Math.cos(w), sn = Math.sin(w), al = sn / (2 * q);
  let b0, b1, b2, a0, a1, a2;
  if (type === 'hp') { b0 = (1 + cs) / 2; b1 = -(1 + cs); b2 = (1 + cs) / 2; a0 = 1 + al; a1 = -2 * cs; a2 = 1 - al; }
  else if (type === 'peak') { b0 = 1 + al * A; b1 = -2 * cs; b2 = 1 - al * A; a0 = 1 + al / A; a1 = -2 * cs; a2 = 1 - al / A; }
  else { const s = 2 * Math.sqrt(A) * al; b0 = A * ((A + 1) + (A - 1) * cs + s); b1 = -2 * A * ((A - 1) + (A + 1) * cs); b2 = A * ((A + 1) + (A - 1) * cs - s);
    a0 = (A + 1) - (A - 1) * cs + s; a1 = 2 * ((A - 1) - (A + 1) * cs); a2 = (A + 1) - (A - 1) * cs - s; }  // high shelf
  const y = new Float32Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const v = (b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = v; y[i] = v;
  }
  return y;
}
export function robot3(x, rate) { return loud(robot3core(x, rate), rate); }

function robot3core(x, rate) {  // the effect without the final level (shared by whole phrases and stream pieces)
  const level = 1.4, amount = 1.1, n = x.length;
  let y = Float32Array.from(x);
  for (const [ms, g] of [[1.1, 0.30], [2.3, 0.22], [3.7, 0.15]]) {
    const d = Math.floor(rate * ms / 1000);
    for (const k of [1, 2]) { const gk = amount * g * Math.pow(0.55, k - 1); for (let i = d * k; i < n; i++) { y[i] += gk * x[i - d * k]; } }
  }
  for (let i = 0; i < n; i++) { y[i] = (1 - 0.18 * amount) * y[i] + 0.18 * amount * x[i] * Math.sin(2 * Math.PI * 60 * i / rate); }
  y = biquad(y, rate, 'hp', 90);
  y = biquad(y, rate, 'peak', 210, 1.0, 1.0);
  y = biquad(y, rate, 'peak', 3500, 2.0 * level, 0.9);
  y = biquad(y, rate, 'shelf', 9000, -2.0);
  const win = 160; let acc = 0; const env = new Float32Array(n);  // compressor (threshold 0.22, ratio 3)
  for (let i = 0; i < n; i++) { acc += Math.abs(y[i]) - (i >= win ? Math.abs(y[i - win]) : 0); env[i] = acc / win; }
  for (let i = 0; i < n; i++) { const over = Math.max(env[i] / 0.22, 1); y[i] /= Math.pow(over, 1 - 1 / 3); }
  const out = new Float32Array(n + Math.floor(rate * 0.05));  // 50 ms for the room echo to die out
  out.set(y);
  for (const [ms, g] of [[7, 0.22], [13, 0.16], [19, 0.12], [29, 0.08]]) {
    const d = Math.floor(rate * ms / 1000); for (let i = d; i < n + d && i < out.length; i++) { out[i] += level * g * y[i - d]; }
  }
  for (let i = 0; i < n; i++) {  // digital double: 11 ms +- 0.25 ms at 0.3 Hz
    const t = i - (11 + 0.25 * Math.sin(2 * Math.PI * 0.3 * i / rate)) * rate / 1000, j = Math.floor(t);
    if (j >= 0 && j + 1 < n) { out[i] += level * 0.14 * (y[j] + (y[j + 1] - y[j]) * (t - j)); }
  }
  const dm = Math.floor(rate * 0.0011); for (let i = dm; i < n; i++) { out[i] += level * 0.07 * y[i - dm]; }
  return out;
}

// A phone speaker needs loudness, not just peak level: lift the average speech level to LOUD_RMS and
// round off the peaks with a soft limiter (tanh), so the voice is as loud as the phone's own voice.
const LOUD_RMS = 0.24;
function gainFor(x) {
  let sum = 0, cnt = 0;
  for (let i = 0; i < x.length; i++) { if (Math.abs(x[i]) > 0.003) { sum += x[i] * x[i]; cnt++; } }  // speech, not pauses
  return cnt ? Math.min(12, LOUD_RMS / Math.sqrt(sum / cnt)) : 1;
}
function shape(x, g, rate, fadeMs = 12) {
  const m = x.length, fade = Math.max(1, Math.floor(rate * fadeMs / 1000)), out = new Float32Array(m);
  for (let i = 0; i < m; i++) {
    const f = Math.min(1, i / fade, (m - 1 - i) / fade);  // short fade in and out: no click
    out[i] = 0.97 * Math.tanh(x[i] * g) * f;  // linear for speech, peaks rounded below 0.97
  }
  return out;
}
export function loud(x, rate) { return shape(x, gainFor(x), rate); }

// One <audio> element, unlocked by a tap and reused. On the iPhone it keeps playing after the
// microphone was used; Web Audio is sometimes left suspended there and the phrase goes silent.
const player = typeof Audio === 'function' ? new Audio() : null;
let silence = '', current = null, stopped = false;

function wav(samples, rate) {  // Float32 -> 16-bit mono WAV
  const n = samples.length, b = new ArrayBuffer(44 + n * 2), v = new DataView(b);
  const w = (o, t) => { for (let i = 0; i < t.length; i++) { v.setUint8(o + i, t.charCodeAt(i)); } };
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) { v.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767), true); }
  return new Blob([b], { type: 'audio/wav' });
}
function unlockPlayer() {
  if (!player || !player.paused) { return; }  // already playing = already unlocked
  if (!silence) { silence = URL.createObjectURL(wav(new Float32Array(2400), 24000)); }
  player.src = silence;
  player.play().catch(() => { /* not inside a tap: the next tap unlocks */ });
}

function levels(samples, rate) {  // loudness 30 times a second, for the orb
  const step = Math.floor(rate / 30), out = new Float32Array(Math.ceil(samples.length / step));
  for (let k = 0; k < out.length; k++) {
    let p = 0;
    for (let i = k * step, e = Math.min(samples.length, i + step); i < e; i++) { p = Math.max(p, Math.abs(samples[i])); }
    out[k] = Math.min(1, p * 1.1);
  }
  return out;
}

function playElement(samples, rate, onLevel) {
  return new Promise((ok, fail) => {
    const url = URL.createObjectURL(wav(samples, rate)), env = levels(samples, rate);
    let on = true, guard = 0;
    const finish = (err) => {
      if (!on) { return; }
      on = false; current = null; onLevel(0); clearTimeout(guard);
      player.onended = player.onerror = null;
      URL.revokeObjectURL(url);
      if (err) { player.pause(); fail(err); } else { ok(); }
    };
    const follow = () => { if (!on) { return; } onLevel(env[Math.floor(player.currentTime * 30)] || 0); requestAnimationFrame(follow); };
    player.onended = () => finish();
    player.onerror = () => finish(new Error('плеер не смог воспроизвести'));
    current = { stop: () => { player.pause(); finish(); } };
    // never wait forever: iOS sometimes neither starts nor reports the end
    guard = setTimeout(() => finish(new Error('плеер не запустился')), 3000);
    player.src = url;
    player.play().then(() => {
      clearTimeout(guard);
      guard = setTimeout(() => finish(), samples.length / rate * 1000 + 3000);
      follow();
    }, (e) => finish(e || new Error('плеер отказал')));
  });
}

async function playContext(samples, rate, onLevel) {  // Web Audio, if the player is refused
  if (!ctx) { throw new Error('звук не разблокирован: нажмите на шар'); }
  if (ctx.state !== 'running') {  // iOS may never resume outside a tap: give up after 1.5 s
    await Promise.race([ctx.resume().catch(() => {}), new Promise((ok) => setTimeout(ok, 1500))]);
    if (ctx.state !== 'running') { throw new Error('звук на паузе у iPhone (' + ctx.state + ')'); }
  }
  return new Promise((ok) => {
    const buf = ctx.createBuffer(1, samples.length, rate);
    buf.copyToChannel(samples, 0);
    const src = ctx.createBufferSource(), env = levels(samples, rate), t0 = ctx.currentTime;
    src.buffer = buf;
    src.connect(ctx.destination);
    let on = true;
    const follow = () => { if (!on) { return; } onLevel(env[Math.floor((ctx.currentTime - t0) * 30)] || 0); requestAnimationFrame(follow); };
    src.onended = () => { on = false; current = null; onLevel(0); ok(); };
    current = { stop: () => { try { src.stop(); } catch (e) { /* ignore */ } } };
    src.start(0); follow();
  });
}

// The way that worked last is tried first, so a phone whose <audio> refuses pays the 3 s wait only once.
let viaElement = !!player;
async function play(samples, rate, onLevel) {
  const ways = viaElement ? [playElement, playContext] : [playContext, playElement];
  let last = null;
  for (const way of ways) {
    if (way === playElement && !player) { continue; }
    try { await way(samples, rate, onLevel); viaElement = way === playElement; return; }
    catch (e) { if (stopped) { return; } last = e; }
  }
  throw last || new Error('звук недоступен');
}

let kick = () => {}, abort = null;
export function stop() {  // stops the voice now: playback, the queue and the request to Gemini
  stopped = true;
  if (abort) { abort.abort(); }
  if (current) { current.stop(); }
  if ('speechSynthesis' in window) { speechSynthesis.cancel(); }
  kick();
}

function chunks(text, limit) {  // pieces of whole sentences, each at most `limit` characters
  const parts = text.replace(/\s+/g, ' ').match(/[^.!?…]+[.!?…]*\s*/g) || [text];
  const out = []; let cur = '';
  for (const p of parts) {
    if ((cur + p).length > limit && cur) { out.push(cur.trim()); cur = ''; }
    cur += p;
  }
  if (cur.trim()) { out.push(cur.trim().slice(0, limit + 20)); }
  return out;
}

// ---------- streaming voice ----------
// Gemini 3.x TTS streams raw 16-bit 24 kHz PCM while it speaks (first sound in ~1 s instead of 4-8 s for
// the whole phrase; measured). The stream is cut into pieces at pauses in the speech, each piece gets the
// robot-3 effect and the same loudness, and the pieces play one after another: the joins fall in pauses.
const RATE = 24000;

function pcm16(b64data) {
  const bin = atob(b64data), n = bin.length >> 1, out = new Float32Array(n);
  for (let i = 0; i < n; i++) { const v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); out[i] = (v >= 32768 ? v - 65536 : v) / 32768; }
  return out;
}

async function geminiStream(text, onPcm, signal) {  // resolves when the stream ends; throws if no audio came
  const key = store.get('geminiKey');
  if (!key) { throw new Error('нет ключа Gemini'); }
  let last = null;
  for (const [model, meta] of TTS) {
    if ((rest()[model] || 0) > Date.now()) { continue; }
    const part = meta ? { text, speech_metadata: { style: STYLE } } : { text: INLINE + text };
    let r, got = 0;
    try {
      r = await fetch(`${G}/v1beta/models/${model}:streamGenerateContent?alt=sse`, {
        method: 'POST', signal, headers: { 'x-goog-api-key': key, 'Content-Type': 'application/json' },
        body: JSON.stringify({ contents: [{ role: 'user', parts: [part] }],
          generationConfig: { responseModalities: ['AUDIO'], speechConfig: { voiceConfig: { voice: VOICE } } } }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        last = new Error((d.error && d.error.message) || 'Gemini HTTP ' + r.status);
        if (r.status === 429) { setRest(model); }
        if (geoRefused(last)) { break; }
        continue;
      }
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) { break; }
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line.startsWith('data:')) { continue; }
          let d;
          try { d = JSON.parse(line.slice(5)); } catch (e) { continue; }
          for (const p of (((d.candidates || [])[0] || {}).content || {}).parts || []) {
            if (p.inlineData && p.inlineData.data) { got++; onPcm(pcm16(p.inlineData.data)); }
          }
        }
      }
    } catch (e) {
      if (signal.aborted) { throw e; }
      if (got) { return; }  // broke off mid-phrase: keep what was said
      last = e;
      continue;
    }
    if (got) { return; }
    last = new Error('Gemini не прислал звук');
  }
  throw last || new Error('голос Gemini на отдыхе: дневной лимит исчерпан');
}

function quietAt(x, from, to) {  // middle of the latest 60 ms pause in x[from, to), or -1
  const w = Math.floor(RATE * 0.06);
  for (let e = to; e - w >= from; e -= 240) {
    let s = 0;
    for (let i = e - w; i < e; i++) { s += x[i] * x[i]; }
    if (Math.sqrt(s / w) < 0.008) { return e - (w >> 1); }
  }
  return -1;
}

// Collects streamed PCM and plays it: push(pcm) as it arrives, end() when the text is fully sent.
function voiceLine(onLevel, onStart) {
  // The first piece is cut as soon as there is a pause after 0.7 s of speech, so the voice starts early.
  // Each next piece is cut shortly before the previous one ends and takes everything received so far
  // (the stream runs ~3x faster than speech), so an answer plays in two or three pieces, not many joins.
  let raw = new Float32Array(RATE * 20), len = 0, cut = 0, gain = 0, ended = false, wait = null, want = true;
  const queue = [];
  const emit = (at, last) => {
    let seg = robot3core(raw.slice(cut, at), RATE);
    if (!last) { seg = seg.subarray(0, at - cut); }  // the room echo tail only after the very end
    if (!gain) { gain = gainFor(seg); }  // one loudness for the whole answer
    queue.push(shape(seg, gain, RATE, cut === 0 || last ? 12 : 4));
    cut = at;
    want = false;
    if (wait) { wait(); }
  };
  const slice = () => {
    if (!want || ended) { return; }
    const have = len - cut, first = cut === 0;
    const min = (first ? 0.7 : 0.5) * RATE, max = (first ? 2.5 : 5.0) * RATE;
    if (have < min) { return; }
    let at = quietAt(raw, cut + Math.floor(min * 0.6), len - 120);
    if (at < 0) { if (have < max) { return; } at = len; }  // no pause yet: cut hard only when long
    emit(at, false);
  };
  const done = (async () => {  // -> {played: pieces heard, error: why the sound failed, if it did}
    let i = 0;
    for (;;) {
      if (stopped) { return { played: i }; }
      if (i < queue.length) {
        if (i === 0) { onStart(); }
        const seg = queue[i];
        const soon = setTimeout(() => { want = true; slice(); }, Math.max(0, seg.length / RATE * 1000 - 400));
        try { await play(seg, RATE, onLevel); } catch (error) { return { played: i, error }; } finally { clearTimeout(soon); }
        i++;
        continue;
      }
      if (ended) { return { played: i }; }
      want = true;
      slice();
      if (i < queue.length) { continue; }
      await new Promise((ok) => { wait = ok; kick = ok; });
      wait = null;
    }
  })();
  return {
    push(pcm) {
      if (len + pcm.length > raw.length) { const n = new Float32Array(Math.max(raw.length * 2, len + pcm.length)); n.set(raw.subarray(0, len)); raw = n; }
      raw.set(pcm, len);
      len += pcm.length;
      slice();
    },
    end() { ended = true; if (len > cut) { emit(len, true); } if (wait) { wait(); } },
    done,
  };
}

function phoneVoice(text) {
  return new Promise((ok) => {
    if (!('speechSynthesis' in window)) { ok(); return; }
    const ru = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.startsWith('ru'));
    const male = ru.find((v) => /Yuri|Юрий|Maxim|Pavel|Dmitri/i.test(v.name));
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ru-RU';
    if (male || ru[0]) { u.voice = male || ru[0]; }
    u.pitch = male ? 0.9 : 0.6;
    // iOS does not always report the end (after cancel especially): stop() and a timer also finish it
    // ~14 characters a second; the old 4 s + 90 ms/char left a long answer "frozen" for over a minute
    const t = setTimeout(() => { speechSynthesis.cancel(); done(); }, 2000 + text.length * 80);
    const done = () => { clearTimeout(t); current = null; ok(); };
    u.onend = u.onerror = done;
    current = { stop: done };
    speechSynthesis.speak(u);
  });
}

// Speak an answer. onStart fires when the first sound plays. Order: Gemini streamed straight from the
// phone (faster than the PC's voice server, which makes whole phrases), Gemini whole phrases, and the
// phone's own voice as the last resort.
export async function speak(text, onLevel = () => {}, onStart = () => {}) {
  if (store.get('speak') !== '1' || !text) { return; }
  if (!rec) { session('playback'); }
  stopped = false;
  abort = new AbortController();
  let started = false;
  const begin = () => { if (!started) { started = true; onStart(); } };
  const parts = chunks(text, 1500);  // one request per answer as a rule: the free voice quota is per request
  const line = voiceLine(onLevel, begin);
  let failed = -1;
  const feeding = (async () => {  // Gemini fills the line while it plays
    try {
      if (store.off('gemini')) { failed = 0; return; }  // refused from this network: the PC voice below
      for (let i = 0; i < parts.length && !abort.signal.aborted; i++) {
        try { await geminiStream(parts[i], (pcm) => line.push(pcm), abort.signal); }
        catch (e) {
          if (abort.signal.aborted) { break; }
          try { const w = await geminiSpeech(parts[i]); line.push(w.samples); }  // streaming refused: whole phrase
          catch (e2) { lastError = (e2 && e2.message) || String(e2); failed = i; break; }
        }
      }
    } catch (e) {
      lastError = (e && e.message) || String(e); failed = Math.max(failed, 0);
    } finally {
      line.end();  // whatever happened, the line finishes: nothing waits forever
    }
  })();
  const heard = await line.done;
  if (heard.error) { lastError = heard.error.message || String(heard.error); abort.abort(); }  // no sound: stop the stream
  await feeding;
  if (stopped) { return; }
  let left = heard.error ? (heard.played ? '' : text) : failed >= 0 ? parts.slice(failed).join(' ') : '';
  // Gemini could not voice it (daily quota, offline): the PC's JARVIS voice, as loud as Gemini's.
  // Not after a playback error: then the phone itself refused sound and the PC voice would fail too.
  if (left && !heard.error && pc.knownAlive()) {
    try {
      const w = parseWav(await pc.speech(left));
      if (stopped) { return; }
      begin();
      await play(loud(w.samples, w.rate), w.rate, onLevel);
      left = '';
      lastError = '';  // JARVIS's voice was heard (the PC's): nothing to report
    } catch (e) { lastError = (e && e.message) || String(e); }
  }
  if (stopped) { return; }
  // the phone's own voice says what JARVIS's voice could not
  for (const p of left ? chunks(left, 380) : []) {
    if (stopped) { break; }
    begin();
    await phoneVoice(p);
  }
}
