'use strict';
// JARVIS particle orb, ported from the PC HUD (ui.py, _paint_core): a Fibonacci-lattice sphere of
// dots joined into a mesh, packets running along the edges, satellites with fading trails, a
// twinkling sky and a slowly turning bezel. Topology is built once; a frame is a rotation and
// a few batched canvas paths. The dots swell with the voice (mic level or JARVIS speaking).
(function () {
  const N = 340, EDGE = 0.28;
  const SATS = [[1.30, 0.55, 0.00, 0.75, 0.0, 3.4], [1.18, -0.80, 2.10, -0.95, 2.0, 3.0],
    [1.42, 1.25, 4.00, 0.52, 4.1, 2.8], [1.56, -0.35, 5.20, 0.66, 1.3, 2.6]];
  const COL = { pri: [0, 212, 255], dim: [0, 122, 153], acc: [255, 107, 0], acc2: [255, 204, 0],
    green: [0, 255, 136], white: [216, 248, 255], warn: [255, 181, 71] };

  // fixed seed, so the sky does not reshuffle between launches
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  const ga = Math.PI * (3 - Math.sqrt(5));
  const pts = [], phs = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - 2 * (i + 0.5) / N, r = Math.sqrt(Math.max(0, 1 - y * y)), th = ga * i;
    pts.push([Math.cos(th) * r, y, Math.sin(th) * r]);
    phs.push(rnd() * 6.2832);
  }
  const edges = [], edgeY = [];
  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      const dx = pts[i][0] - pts[j][0], dy = pts[i][1] - pts[j][1], dz = pts[i][2] - pts[j][2];
      if (dx * dx + dy * dy + dz * dz < EDGE * EDGE) { edges.push([i, j]); edgeY.push((pts[i][1] + pts[j][1]) / 2); }
    }
  }
  const stars = [];
  for (let i = 0; i < 70; i++) { stars.push([rnd() * 2 - 1, rnd() * 2 - 1, rnd() < 0.16, rnd() * 6.2832, 0.6 + rnd() * 1.6, 0.3 + rnd() * 0.7]); }
  const pulses = [];
  for (let i = 0; i < 14; i++) { pulses.push([Math.floor(Math.random() * edges.length), Math.random(), 0.5 + Math.random() * 0.7]); }

  const S = { state: 'standby', level: 0, amp: 0, t: 0, spin: 0, last: 0, canvas: null, ctx: null, w: 0, h: 0 };
  const rgba = (c, a) => 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + Math.max(0, Math.min(1, a)).toFixed(3) + ')';
  const lighter = (c) => c.map((v) => Math.min(255, Math.round(v * 1.4 + 30)));

  function colours() {
    if (S.state === 'error') { return [COL.warn, COL.acc]; }
    if (S.state === 'speaking') { return [COL.pri, COL.acc]; }
    if (S.state === 'thinking') { return [COL.pri, COL.acc2]; }
    if (S.state === 'listening') { return [COL.pri, COL.green]; }
    return [COL.pri, COL.acc];
  }

  function resize() {
    const c = S.canvas, dpr = Math.min(3, window.devicePixelRatio || 1);
    const box = c.getBoundingClientRect();
    S.w = box.width; S.h = box.height;
    c.width = Math.round(box.width * dpr); c.height = Math.round(box.height * dpr);
    S.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden || !S.ctx) { S.last = now; return; }
    if (S.canvas.clientWidth !== S.w || S.canvas.clientHeight !== S.h) { resize(); }  // shown after a hidden screen
    if (!S.w) { S.last = now; return; }
    const dt = Math.min(0.1, Math.max(0, (now - (S.last || now)) / 1000));
    S.last = now;
    // voice level: real mic/voice level when we have it, a gentle synthetic one while speaking otherwise
    const target = S.state === 'speaking' && S.level < 0.02 ? 0.35 + 0.3 * Math.sin(S.t * 9) * Math.sin(S.t * 3.7) : S.level;
    S.amp += (Math.max(0, target) - S.amp) * Math.min(1, dt * 12);
    const amp = S.amp, speaking = S.state === 'speaking', thinking = S.state === 'thinking';
    S.t += dt;
    // spin is integrated, never time x rate: a rate change must not make the sphere jump
    S.spin += dt * (0.42 + (speaking ? 0.30 : 0) + (thinking ? 0.6 : 0) + 0.6 * amp);
    const spd = 1 + 1.6 * amp + (speaking ? 0.6 : 0) + (thinking ? 1.2 : 0);
    for (const pl of pulses) {
      pl[1] += dt * spd * pl[2];
      if (pl[1] >= 1) { pl[0] = Math.floor(Math.random() * edges.length); pl[1] = 0; pl[2] = 0.5 + Math.random() * 0.7; }
    }
    draw(amp);
  }

  function draw(amp) {
    const g = S.ctx, w = S.w, h = S.h, t = S.t;
    const [main, acc] = colours();
    g.clearRect(0, 0, w, h);
    const R = Math.min(w, h) * 0.27, cx = w / 2, cy = h / 2;

    // stars: a fixed sky that twinkles and drifts a few pixels
    for (const [u, v, big, sph, sps, dep] of stars) {
      const tw = 0.5 + 0.5 * Math.sin(t * sps + sph);
      g.fillStyle = rgba(COL.white, 0.15 + 0.65 * tw * tw);
      const sx = (u * 0.5 + 0.5) * w + Math.sin(t * 0.08 * dep + sph) * 4 * dep;
      const sy = (v * 0.5 + 0.5) * h + Math.cos(t * 0.06 * dep + sph) * 3 * dep;
      g.beginPath(); g.arc(sx, sy, big ? 1.3 : 0.7, 0, 6.2832); g.fill();
    }

    // instrument bezel: fine ticks turning slowly around the orb
    const Rz = Math.min(R * 1.75, Math.min(w, h) / 2 - 4), a0 = t * 0.045;
    for (const major of [false, true]) {
      g.beginPath();
      for (let i = 0; i < 120; i++) {
        if ((i % 10 === 0) !== major) { continue; }
        const ang = a0 + i * 2 * Math.PI / 120, c = Math.cos(ang), s = Math.sin(ang), r0 = Rz - (major ? 7 : 3.5);
        g.moveTo(cx + c * r0, cy + s * r0); g.lineTo(cx + c * Rz, cy + s * Rz);
      }
      g.strokeStyle = rgba(main, major ? 0.47 : 0.18); g.lineWidth = major ? 1.4 : 1; g.stroke();
    }

    const ry = S.spin, rx = -0.28 + Math.sin(t * 0.27) * 0.16 + Math.sin(t * 0.11) * 0.05;
    const cyr = Math.cos(ry), syr = Math.sin(ry), cxr = Math.cos(rx), sxr = Math.sin(rx), persp = 0.28;

    // satellites (heads + dotted trails), split behind / in front of the sphere
    const back = [], front = [];
    for (const [ro, inc, nd, sp, ph0, head] of SATS) {
      const base = ph0 + t * sp, sgn = sp > 0 ? 1 : -1;
      for (let k = 0; k < 10; k++) {
        const a = base - sgn * 0.08 * k, sx0 = Math.cos(a) * ro, sz0 = Math.sin(a) * ro;
        const sy1 = -sz0 * Math.sin(inc), sz1 = sz0 * Math.cos(inc);
        const x2 = sx0 * Math.cos(nd) + sz1 * Math.sin(nd), z2 = -sx0 * Math.sin(nd) + sz1 * Math.cos(nd);
        const y3 = sy1 * cxr - z2 * sxr, z3 = sy1 * sxr + z2 * cxr, d = 1 / Math.max(0.6, 1 - persp * z3);
        const fade = 1 - k / 10, behind = z3 < 0;
        (behind ? back : front).push([cx + x2 * R * d, cy + y3 * R * d, head * (1 - 0.07 * k) * (behind ? 0.8 : 1),
          0.92 * fade * fade * (behind ? 0.45 : 1), k === 0]);
      }
    }
    const drawSats = (items) => {
      for (const [x, y, r, al, isHead] of items) {
        if (isHead) {
          g.fillStyle = rgba(acc, al * 0.22); g.beginPath(); g.arc(x, y, r * 2.8, 0, 6.2832); g.fill();
          g.fillStyle = rgba(COL.white, al);
        } else { g.fillStyle = rgba(acc, al); }
        g.beginPath(); g.arc(x, y, r * 0.75, 0, 6.2832); g.fill();
      }
    };
    drawSats(back);

    // ambient glow + glass body (light from the upper left)
    const lift = 1 + 0.9 * amp + (S.state === 'speaking' ? 0.25 : 0) + 0.12 * Math.sin(t * 1.15);
    let grd = g.createRadialGradient(cx, cy, 0, cx, cy, R * 2.1);
    grd.addColorStop(0, rgba(main, 0.235 * lift)); grd.addColorStop(0.5, rgba(main, 0.07 * lift)); grd.addColorStop(1, rgba(main, 0));
    g.fillStyle = grd; g.beginPath(); g.arc(cx, cy, R * 2.1, 0, 6.2832); g.fill();
    const Rb = R * (1 + 0.018 * Math.sin(t * 1.15) + amp * 0.05);
    grd = g.createRadialGradient(cx - Rb * 0.3, cy - Rb * 0.35, 0, cx - Rb * 0.3, cy - Rb * 0.35, Rb * 1.3);
    grd.addColorStop(0, rgba(main, 0.18)); grd.addColorStop(0.6, rgba(main, 0.055)); grd.addColorStop(1, rgba(main, 0.024));
    g.fillStyle = grd; g.beginPath(); g.arc(cx, cy, Rb, 0, 6.2832); g.fill();

    // project the dots (breathing, voice wobble, a ripple travelling up the sphere)
    const px = new Float32Array(N), py = new Float32Array(N), pz = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const [x0, y0, z0] = pts[i], ph = phs[i];
      const k = 1 + 0.022 * Math.sin(t * 1.3 + ph) + amp * 0.2 * (0.5 + 0.5 * Math.sin(t * 5 + ph * 2))
        + (0.016 + 0.045 * amp) * Math.sin(y0 * 6 - t * 2.8 + ph * 0.2);
      const x = x0 * k, y = y0 * k, z = z0 * k;
      const x1 = x * cyr + z * syr, z1 = -x * syr + z * cyr, y1 = y * cxr - z1 * sxr, z2 = y * sxr + z1 * cxr;
      const d = 1 / Math.max(0.6, 1 - persp * z2);
      px[i] = cx + x1 * Rb * d; py[i] = cy + y1 * Rb * d; pz[i] = z2;
    }

    // mesh edges in four depth buckets; the scan band lights the edges it crosses
    const ea = [0.086, 0.19, 0.34, 0.53], ew = [0.7, 0.8, 1.0, 1.2], elift = 1 + 0.8 * amp;
    const scan = Math.sin(t * 0.85) * 0.92;
    const paths = [new Path2D(), new Path2D(), new Path2D(), new Path2D()], hot = new Path2D();
    for (let e = 0; e < edges.length; e++) {
      const [a, b] = edges[e], dn = (pz[a] + pz[b]) * 0.25 + 0.5;
      const bi = dn < 0 ? 0 : (dn >= 1 ? 3 : Math.floor(dn * 4));
      const p = bi >= 2 && Math.abs(edgeY[e] - scan) < 0.07 ? hot : paths[bi];
      p.moveTo(px[a], py[a]); p.lineTo(px[b], py[b]);
    }
    for (let bi = 0; bi < 4; bi++) { g.strokeStyle = rgba(main, ea[bi] * elift); g.lineWidth = ew[bi]; g.stroke(paths[bi]); }
    g.strokeStyle = rgba(acc, (120 + 70 * amp) / 255); g.lineWidth = 1.1; g.stroke(hot);

    // the dots themselves
    const da = [0.27, 0.47, 0.75, 1], ds = [0.8, 1.1, 1.5, 2.0], bright = lighter(main);
    const dots = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
    for (let i = 0; i < N; i++) {
      const dn = (pz[i] + 1) * 0.5, bi = dn < 0 ? 0 : (dn >= 1 ? 3 : Math.floor(dn * 4)), r = ds[bi] * (1 + 0.25 * amp);
      dots[bi].moveTo(px[i] + r, py[i]); dots[bi].arc(px[i], py[i], r, 0, 6.2832);
    }
    for (let bi = 0; bi < 4; bi++) { g.fillStyle = rgba(bi === 3 ? bright : main, da[bi]); g.fill(dots[bi]); }

    // data packets running along the mesh
    for (const [e, pr] of pulses) {
      const [a, b] = edges[e], z = pz[a] + (pz[b] - pz[a]) * pr;
      if (z < -0.25) { continue; }
      for (let k = 0; k < 4; k++) {
        const q = pr - 0.07 * k;
        if (q < 0) { break; }
        const x = px[a] + (px[b] - px[a]) * q, y = py[a] + (py[b] - py[a]) * q;
        if (k === 0) {
          g.fillStyle = rgba(acc, 0.27); g.beginPath(); g.arc(x, y, 4, 0, 6.2832); g.fill();
          g.fillStyle = rgba(COL.white, 0.92); g.beginPath(); g.arc(x, y, 1.7, 0, 6.2832); g.fill();
        } else {
          const fd = 1 - k * 0.25;
          g.fillStyle = rgba(acc, 0.63 * fd); g.beginPath(); g.arc(x, y, 1 + 0.6 * fd, 0, 6.2832); g.fill();
        }
      }
    }
    drawSats(front);
  }

  window.JarvisOrb = {
    mount(canvas) {
      S.canvas = canvas; S.ctx = canvas.getContext('2d');
      resize();
      window.addEventListener('resize', resize);
      requestAnimationFrame(frame);
    },
    setState(name) { S.state = name; },
    setLevel(x) { S.level = Math.max(0, Math.min(1, Number(x) || 0)); },
  };
}());
