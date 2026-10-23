/*
MIT License - see ../LICENSE.txt. PC12 editor built on the PC4 editor by Peter Witzel.

PC12 dump format (same as pc12.py next to this folder, verified against firmware 1.04):
  every value is a nibble triplet  4X 2H 1L   X = command, value = H<<4 | L
  commands: 1 device (0x0A = PC12), 2 dump type (2 = one setup, 3 = all setups),
            3/4 firmware major/minor, 9/A page address hi/lo, D data byte,
            B/C 16-bit sum of the page data, F stop.
  a setup is 8 pages x 64 bytes at address setup*0x200 (setup 1..30, 31 = copy buffer).
  pages 0-5 = pot rows A-F, page 6 = keys 1-12, page 7 = encoder + encoder push.
  each control is 5 bytes: [channel-1, number, lower, upper, mode].
  pot mode byte: bits0-2 type, bit3 snap, bits5-6 display (01 normal, 10 bipolar, 00 off).
*/
'use strict';

const PC12 = {
  DEVICE: 0x0a,
  PAGE: 64,
  CTRL: 5,
  IMAGE: 512,
  SETUPS: 30,
  TYPES: ['note', 'cc', 'prgchange', 'pitchbend', 'aftertouch'],
  TYPE_LABELS: ['Note', 'CC', 'Prg chg', 'Pitch bend', 'Aftertouch'],
  // keys and encoder push: 0 is unused on the device
  KEY_TYPES: ['?', 'note', 'cc', 'prgchange', 'aftertouch'],
  KEY_TYPE_LABELS: ['?', 'Note', 'CC', 'Prg chg', 'Aftertouch'],
  ENC_TYPES: ['ccr1', 'ccr2', 'ccabs', 'prgchange', 'cc14bit', 'pitchbend', 'aftertouch', 'note'],
  ENC_TYPE_LABELS: ['CC rel 1', 'CC rel 2', 'CC abs', 'Prg chg', 'CC 14 bit', 'Pitch bend', 'Aftertouch', 'Note'],
  DISPLAY: { 1: 'normal', 2: 'bipolar', 0: 'off' },
  LED: { 1: 'std', 2: 'ext', 0: 'off' },
  NAMES: [],
};
for (const r of 'ABCDEF') for (let c = 1; c <= 12; c++) PC12.NAMES.push('P' + String(c).padStart(2, '0') + r);
for (let k = 1; k <= 12; k++) PC12.NAMES.push('K' + String(k).padStart(2, '0'));
PC12.NAMES.push('ENC', 'ENC2');

function ctrlAddr(i) {
  return Math.floor(i / 12) * PC12.PAGE + (i % 12) * PC12.CTRL;
}

function factoryImage(setup) {
  // device fills unused bytes with 0x00 in pages 0-6 and 0xFF after the encoders
  const img = new Uint8Array(PC12.IMAGE);
  img.fill(0xff, 7 * PC12.PAGE + 2 * PC12.CTRL);
  const ch = (setup - 1) % 16;
  PC12.NAMES.forEach((n, i) => {
    const a = ctrlAddr(i);
    img[a] = ch;
    img[a + 1] = i + 1;
    img[a + 2] = 0;
    img[a + 3] = 127;
    img[a + 4] = n[0] === 'P' ? 0x21 : n[0] === 'K' ? 0x2a : n === 'ENC' ? 0x3a : 0x02;
  });
  return img;
}

// ---------------------------------------------------------------- sysex <-> images

function parsePages(data) {
  const toks = [];
  for (const b of data) if (b !== 0xf0 && b !== 0xf7 && b !== 0x00) toks.push(b);
  const hdr = {};
  const pages = [];
  let cur = null;
  for (let j = 0; j + 2 < toks.length; j += 3) {
    const c = toks[j], hi = toks[j + 1], lo = toks[j + 2];
    if ((c & 0xf0) !== 0x40 || (hi & 0xf0) !== 0x20 || (lo & 0xf0) !== 0x10) {
      throw new Error('bad triplet ' + toHex(c) + ' ' + toHex(hi) + ' ' + toHex(lo) + ' at token ' + j);
    }
    const cmd = c & 0xf, v = ((hi & 0xf) << 4) | (lo & 0xf);
    if (cmd >= 1 && cmd <= 4) hdr[cmd] = v;
    else if (cmd === 9) cur = { addr: v << 8, data: [] };
    else if (cmd === 0xa) cur.addr |= v;
    else if (cmd === 0xd) cur.data.push(v);
    else if (cmd === 0xb) cur.ck = v << 8;
    else if (cmd === 0xc) {
      cur.ck |= v;
      const sum = cur.data.reduce((s, x) => s + x, 0) & 0xffff;
      if (sum !== cur.ck) throw new Error('checksum mismatch in page 0x' + toHex(cur.addr, 4));
      pages.push(cur);
      cur = null;
    }
  }
  if (hdr[1] !== PC12.DEVICE) throw new Error('not a PC12 dump (device 0x' + toHex(hdr[1] || 0) + ')');
  return { hdr, pages };
}

function splitMessages(data) {
  const msgs = [];
  let start = -1;
  for (let i = 0; i < data.length; i++) {
    if (data[i] === 0xf0) start = i;
    else if (data[i] === 0xf7 && start >= 0) {
      msgs.push(data.subarray(start, i + 1));
      start = -1;
    }
  }
  return msgs;
}

// any .syx (one setup, all setups, or several messages) -> { version, images: {setup: Uint8Array} }
function loadDump(data) {
  const byAddr = new Map();
  let version = null;
  const msgs = splitMessages(data);
  if (!msgs.length) throw new Error('no sysex messages found');
  for (const m of msgs) {
    const { hdr, pages } = parsePages(m);
    version = [hdr[3], hdr[4]];
    for (const p of pages) byAddr.set(p.addr, p.data);
  }
  const images = {};
  for (let setup = 1; setup <= 31; setup++) {
    const base = setup * 0x200;
    let ok = true;
    for (let i = 0; i < 8; i++) if (!byAddr.has(base + i * PC12.PAGE)) ok = false;
    if (!ok) continue;
    const img = new Uint8Array(PC12.IMAGE);
    for (let i = 0; i < 8; i++) img.set(byAddr.get(base + i * PC12.PAGE), i * PC12.PAGE);
    images[setup] = img;
  }
  if (!Object.keys(images).length) throw new Error('no complete setup in this dump');
  return { version, images };
}

function trip(out, cmd, v) {
  out.push(0x40 | cmd, 0x20 | (v >> 4), 0x10 | (v & 0xf));
}

function pushPage(out, addr, page) {
  trip(out, 9, addr >> 8);
  trip(out, 0xa, addr & 0xff);
  let sum = 0;
  for (const b of page) {
    trip(out, 0xd, b);
    sum += b;
  }
  sum &= 0xffff;
  trip(out, 0xb, sum >> 8);
  trip(out, 0xc, sum & 0xff);
  for (let i = 0; i < 30; i++) out.push(0); // stock padding
}

function buildSyx(setup, version, image) {
  const out = [0xf0, 0, 0, 0];
  trip(out, 1, PC12.DEVICE);
  trip(out, 2, 2);
  trip(out, 3, version[0]);
  trip(out, 4, version[1]);
  for (let i = 0; i < 8; i++) {
    pushPage(out, setup * 0x200 + i * PC12.PAGE, image.subarray(i * PC12.PAGE, (i + 1) * PC12.PAGE));
  }
  trip(out, 0xf, PC12.DEVICE);
  out.push(0xf7);
  return new Uint8Array(out);
}

// same page layout as a SndA dump from the device: 0x0200-0x4EFF, 308 pages
function buildAllSyx(version, images) {
  const out = [0xf0, 0, 0, 0];
  trip(out, 1, PC12.DEVICE);
  trip(out, 2, 3);
  trip(out, 3, version[0]);
  trip(out, 4, version[1]);
  const zero = new Uint8Array(PC12.PAGE);
  for (let addr = 0x0200; addr < 0x4f00; addr += PC12.PAGE) {
    const setup = addr >> 9;
    const img = images[setup];
    const page = img ? img.subarray(addr % 0x200, (addr % 0x200) + PC12.PAGE) : zero;
    pushPage(out, addr, page);
  }
  trip(out, 0xf, PC12.DEVICE);
  out.push(0xf7);
  return new Uint8Array(out);
}

// ---------------------------------------------------------------- control <-> plain object (same shape as pc12.py)

function kindOf(i) {
  const n = PC12.NAMES[i];
  return n[0] === 'P' ? 'pot' : n === 'ENC' ? 'enc' : 'key';
}

function getCtrl(img, i) {
  const a = ctrlAddr(i), m = img[a + 4], kind = kindOf(i);
  const d = { ch: img[a] + 1, num: img[a + 1], lo: img[a + 2], hi: img[a + 3] };
  const t = m & 7, scale = (m >> 5) & 3;
  let extra;
  if (kind === 'pot') {
    d.type = t < PC12.TYPES.length ? PC12.TYPES[t] : t;
    d.snap = !!(m & 0x08);
    d.display = scale in PC12.DISPLAY ? PC12.DISPLAY[scale] : scale;
    extra = m & 0x90;
  } else if (kind === 'enc') {
    d.type = PC12.ENC_TYPES[t];
    d.acc = (m >> 3) & 3;
    d.display = scale in PC12.DISPLAY ? PC12.DISPLAY[scale] : scale;
    extra = m & 0x80;
  } else {
    d.type = t >= 1 && t < PC12.KEY_TYPES.length ? PC12.KEY_TYPES[t] : t;
    d.toggle = !!(m & 0x08);
    d.led = scale in PC12.LED ? PC12.LED[scale] : scale;
    extra = m & 0x90;
  }
  if (extra) d.mode_extra = extra;
  return d;
}

// name -> code through a list or {code: name} table; numbers pass through
function lookup(table, v) {
  if (typeof v !== 'string') return v;
  const k = Array.isArray(table) ? table.indexOf(v) : Object.keys(table).find((k) => table[k] === v);
  if (k === undefined || k < 0) throw new Error('unknown value ' + v);
  return +k;
}

function setCtrl(img, i, d) {
  const a = ctrlAddr(i), kind = kindOf(i);
  let m;
  if ('mode' in d) {
    m = d.mode; // raw byte, as older json files have for keys
  } else if (kind === 'pot') {
    m = lookup(PC12.TYPES, d.type);
    if (d.snap) m |= 0x08;
    m |= lookup(PC12.DISPLAY, d.display === undefined ? 'normal' : d.display) << 5;
    m |= d.mode_extra || 0;
  } else if (kind === 'enc') {
    m = lookup(PC12.ENC_TYPES, d.type);
    m |= ((d.acc || 0) & 3) << 3;
    m |= lookup(PC12.DISPLAY, d.display === undefined ? 'normal' : d.display) << 5;
    m |= d.mode_extra || 0;
  } else {
    m = lookup(PC12.KEY_TYPES, d.type);
    if (d.toggle) m |= 0x08;
    m |= lookup(PC12.LED, d.led === undefined ? 'std' : d.led) << 5;
    m |= d.mode_extra || 0;
  }
  img[a] = clamp(d.ch, 1, 16) - 1;
  img[a + 1] = clamp(d.num, 0, 127);
  img[a + 2] = clamp(d.lo, 0, 127);
  img[a + 3] = clamp(d.hi, 0, 127);
  img[a + 4] = clamp(m, 0, 255);
}

function clamp(v, min, max) {
  v = typeof v === 'string' ? parseInt(v, v.trim().startsWith('0x') ? 16 : 10) : v;
  if (!isFinite(v)) return min;
  return Math.min(max, Math.max(min, Math.round(v)));
}

function imageToDoc(setup, version, img) {
  const controls = {};
  PC12.NAMES.forEach((n, i) => (controls[n] = getCtrl(img, i)));
  return { setup, version: version.slice(), controls };
}

function docToImage(doc, base) {
  const img = new Uint8Array(base || factoryImage(doc.setup));
  PC12.NAMES.forEach((n, i) => {
    if (doc.controls[n]) setCtrl(img, i, doc.controls[n]);
  });
  return img;
}

// ---------------------------------------------------------------- csv

// "mode2" is snap (pots, y/n), toggle (keys, y/n) or acceleration (encoder, 0-3).
// "display" is the display scale (pots, encoder) or LED mode (keys). "mode" is the raw byte.
const CSV_HEADER = ['setup', 'control', 'ch', 'num', 'lo', 'hi', 'type', 'mode2', 'display', 'mode'];

function toCsv(images) {
  const lines = [CSV_HEADER.join(',')];
  for (const setup of Object.keys(images).map(Number).sort((a, b) => a - b)) {
    const img = images[setup];
    PC12.NAMES.forEach((n, i) => {
      const d = getCtrl(img, i), kind = kindOf(i);
      const mode2 = kind === 'enc' ? d.acc : (kind === 'pot' ? d.snap : d.toggle) ? 'y' : 'n';
      lines.push([setup, n, d.ch, d.num, d.lo, d.hi, d.type, mode2, kind === 'key' ? d.led : d.display,
        '0x' + toHex(img[ctrlAddr(i) + 4])].join(','));
    });
  }
  return lines.join('\r\n') + '\r\n';
}

// returns {setup: image}; rows for a setup are applied on top of `existing[setup]` or factory
function fromCsv(text, existing) {
  const rows = text.split(/\r?\n/).filter((l) => l.trim() !== '');
  const head = rows.shift().split(',').map((s) => s.trim().toLowerCase());
  const col = {};
  CSV_HEADER.forEach((h) => (col[h] = head.indexOf(h)));
  if (col.setup < 0 || col.control < 0) throw new Error('csv needs at least "setup" and "control" columns');
  const images = {};
  for (const line of rows) {
    const f = line.split(',').map((s) => s.trim());
    const setup = +f[col.setup];
    const i = PC12.NAMES.indexOf(f[col.control].toUpperCase());
    if (!(setup >= 1 && setup <= 31) || i < 0) throw new Error('bad row: ' + line);
    if (!images[setup]) images[setup] = new Uint8Array(existing[setup] || factoryImage(setup));
    const img = images[setup];
    const cur = getCtrl(img, i);
    const get = (k) => (col[k] >= 0 && f[col[k]] !== '' ? f[col[k]] : undefined);
    const d = { ch: get('ch') ?? cur.ch, num: get('num') ?? cur.num, lo: get('lo') ?? cur.lo, hi: get('hi') ?? cur.hi };
    const kind = kindOf(i);
    const yes = (s) => /^(1|y|yes|true|on)$/i.test(s);
    if (get('type') !== undefined) {
      d.type = get('type');
      d.mode_extra = cur.mode_extra;
      if (kind === 'pot') {
        d.snap = get('mode2') === undefined ? cur.snap : yes(get('mode2'));
        d.display = get('display') ?? cur.display;
      } else if (kind === 'enc') {
        d.acc = get('mode2') === undefined ? cur.acc : clamp(get('mode2'), 0, 3);
        d.display = get('display') ?? cur.display;
      } else {
        d.toggle = get('mode2') === undefined ? cur.toggle : yes(get('mode2'));
        d.led = get('display') ?? cur.led;
      }
    } else if (get('mode') !== undefined) {
      d.mode = clamp(get('mode'), 0, 255); // raw mode byte
    } else {
      d.mode = img[ctrlAddr(i) + 4];
    }
    setCtrl(img, i, d);
  }
  return images;
}

// ---------------------------------------------------------------- editor

document.addEventListener('DOMContentLoaded', function () {
  const S = {
    title_receive: 'Receive from PC12',
    msg_receive:
      'Send a dump from the PC12 to this editor:\
      <ul><li>Hold the red shift key and press a blue key to enter setup mode.</li>\
      <li>Hold button 7 while the bars run. Pick <b>Sndc</b> (this setup) or <b>SndA</b> (all setups).</li></ul>\
      <p>A message appears here when the data has arrived.</p>',
    title_send: 'Send to PC12',
    msg_send:
      'Arm the PC12 first, or it ignores the dump:\
      <ul><li>Hold the red shift key and press a blue key to enter setup mode.</li>\
      <li>Hold button 8 until the display shows <b>rc00</b>.</li></ul>\
      <p>Then press <i>Send</i>. This sends setup <b>{}</b> ({} bytes).</p>',
    msg_send_all:
      'Arm the PC12 first (hold button 8 in setup mode until <b>rc00</b>).\
      <p>This sends <b>all 30 setups</b> in one SndA style dump ({} bytes), the same layout the PC12 itself sends. Setups you never loaded or edited go out as factory defaults.</p>\
      <p>Back up first with <i>Receive</i> + SndA.</p>',
    title_received: 'Data received',
    $msg_apply: 'Received setup(s) {} from the PC12. Apply to the editor? This replaces those setups here.',
    title_file: 'Open file',
    $msg_loaded: 'Loaded setup(s) {}.',
    title_error: 'Error',
  };

  const state = { version: [1, 4], images: {} }; // images: setup number -> Uint8Array(512)
  let current = 1;
  let clipboard = null;

  function image(setup) {
    setup = setup || current;
    if (!state.images[setup]) state.images[setup] = factoryImage(setup);
    return state.images[setup];
  }

  // -------- persistence (localStorage, hex strings)
  function save() {
    try {
      const o = { version: state.version, images: {} };
      for (const k in state.images) o.images[k] = Array.from(state.images[k], (b) => toHex(b)).join('');
      localStorage.setItem('pc12editor', JSON.stringify(o));
    } catch (e) {
      console.log('save failed', e);
    }
  }
  function restore() {
    try {
      const o = JSON.parse(localStorage.getItem('pc12editor'));
      if (!o) return;
      state.version = o.version || state.version;
      for (const k in o.images) {
        const s = o.images[k];
        const img = new Uint8Array(s.length / 2);
        for (let i = 0; i < img.length; i++) img[i] = parseInt(s.substr(i * 2, 2), 16);
        state.images[k] = img;
      }
    } catch (e) {
      console.log('restore failed', e);
    }
  }

  // -------- rendering
  const grid = DOM.element('#grid');
  const keys = DOM.element('#keys');
  const setupList = DOM.element('#setups');

  function options(list, sel) {
    return list.map((l, i) => `<option value="${i}"${i === sel ? ' selected' : ''}>${l}</option>`).join('');
  }
  function num(f, v, min, max) {
    return `<input data-f="${f}" type="number" min="${min}" max="${max}" value="${v}">`;
  }
  function potHtml(i, img) {
    const a = ctrlAddr(i), m = img[a + 4];
    const t = m & 7;
    return `<div class="ctrl pot t-${PC12.TYPES[t] || 'x'}" data-i="${i}">
      <div class="name">${PC12.NAMES[i]}</div>
      <select data-f="type">${options(PC12.TYPE_LABELS, t)}</select>
      <div class="row"><label>ch</label>${num('ch', img[a] + 1, 1, 16)}</div>
      <div class="row num"><label>#</label>${num('num', img[a + 1], 0, 127)}</div>
      <div class="row"><label>lo</label>${num('lo', img[a + 2], 0, 127)}</div>
      <div class="row"><label>hi</label>${num('hi', img[a + 3], 0, 127)}</div>
      <label class="snap"><input data-f="snap" type="checkbox"${m & 8 ? ' checked' : ''}>snap</label>
      <select data-f="display">${options(['off', 'normal', 'bipolar', '?'], (m >> 5) & 3)}</select>
    </div>`;
  }
  function keyHtml(i, img) {
    const a = ctrlAddr(i), n = PC12.NAMES[i], m = img[a + 4], kind = kindOf(i);
    const t = m & 7;
    let head, tail;
    if (kind === 'enc') {
      head = `<select data-f="type">${options(PC12.ENC_TYPE_LABELS, t)}</select>`;
      tail = `<div class="row" title="acceleration by faster turning"><label>acc</label>${num('acc', (m >> 3) & 3, 0, 3)}</div>
        <select data-f="display">${options(['off', 'normal', 'bipolar', '?'], (m >> 5) & 3)}</select>`;
    } else {
      head = `<select data-f="type">${options(PC12.KEY_TYPE_LABELS, t)}</select>`;
      tail = `<label class="snap" title="toggle: upper value = on, lower = off. momentary: upper = press, lower = release"><input data-f="toggle" type="checkbox"${m & 8 ? ' checked' : ''}>toggle</label>
        <select data-f="led" title="LED: off, by device + external, external only">${options(['led off', 'led std', 'led ext', '?'], (m >> 5) & 3)}</select>`;
    }
    return `<div class="ctrl ${n[0] === 'K' ? 'key' : 'enc'}" data-i="${i}">
      <div class="name">${n}</div>
      ${head}
      <div class="row"><label>ch</label>${num('ch', img[a] + 1, 1, 16)}</div>
      <div class="row num"><label>#</label>${num('num', img[a + 1], 0, 127)}</div>
      <div class="row"><label>lo</label>${num('lo', img[a + 2], 0, 127)}</div>
      <div class="row"><label>hi</label>${num('hi', img[a + 3], 0, 127)}</div>
      ${tail}
    </div>`;
  }
  function renderSetupList() {
    let h = '';
    for (let s = 1; s <= PC12.SETUPS; s++) {
      h += `<li data-s="${s}" class="${s === current ? 'selected ' : ''}${state.images[s] ? 'loaded' : ''}">${s}</li>`;
    }
    setupList.innerHTML = h;
  }
  function render() {
    const img = image();
    let h = '';
    for (let r = 0; r < 6; r++) {
      for (let c = 0; c < 12; c++) {
        if (c === 4 || c === 8) h += r === 0 ? '<div class="gap"></div>' : '';
        h += potHtml(r * 12 + c, img);
      }
    }
    grid.innerHTML = h;
    h = '';
    for (let k = 0; k < 12; k++) {
      if (k === 4 || k === 8) h += '<div class="gap"></div>';
      h += keyHtml(72 + k, img);
    }
    h += keyHtml(84, img) + keyHtml(85, img);
    keys.innerHTML = h;
    renderSetupList();
  }

  DOM.on(grid, 'change', onChange);
  DOM.on(keys, 'change', onChange);
  DOM.on('main', 'focusin', function (ev) {
    if (ev.target.select) ev.target.select();
  });

  function onChange(ev) {
    const el = ev.target;
    const cell = el.closest('[data-i]');
    if (!cell) return;
    const i = +cell.dataset.i, f = el.dataset.f;
    const img = image(), a = ctrlAddr(i);
    switch (f) {
      case 'ch': img[a] = clamp(el.value, 1, 16) - 1; el.value = img[a] + 1; break;
      case 'num': img[a + 1] = clamp(el.value, 0, 127); el.value = img[a + 1]; break;
      case 'lo': img[a + 2] = clamp(el.value, 0, 127); el.value = img[a + 2]; break;
      case 'hi': img[a + 3] = clamp(el.value, 0, 127); el.value = img[a + 3]; break;
      case 'type':
        img[a + 4] = (img[a + 4] & ~7) | +el.value;
        if (kindOf(i) === 'pot') cell.className = 'ctrl pot t-' + PC12.TYPES[+el.value];
        break;
      case 'snap': case 'toggle': img[a + 4] = (img[a + 4] & ~8) | (el.checked ? 8 : 0); break;
      case 'acc': img[a + 4] = (img[a + 4] & ~0x18) | (clamp(el.value, 0, 3) << 3); el.value = (img[a + 4] >> 3) & 3; break;
      case 'display': case 'led': img[a + 4] = (img[a + 4] & ~0x60) | (+el.value << 5); break;
    }
    save();
    renderSetupList();
  }

  DOM.on(setupList, 'click', function (ev) {
    const li = ev.target.closest('li[data-s]');
    if (!li) return;
    current = +li.dataset.s;
    render();
  });

  // -------- apply loaded data
  function applyImages(images, version) {
    Object.assign(state.images, images);
    if (version && version[0] !== undefined) state.version = version;
    const list = Object.keys(images).map(Number).sort((a, b) => a - b);
    if (!list.includes(current)) current = list[0];
    save();
    render();
    return list;
  }
  function setupsText(images) {
    return Object.keys(images).map((k) => (+k === 31 ? 'copy buffer' : k)).join(', ');
  }

  // -------- midi
  function sysexHandler(data) {
    try {
      const { version, images } = loadDump(data);
      MBox.show(S.title_received, STR.apply(S.$msg_apply, setupsText(images)), {
        confirmCallback: function () {
          MBox.hide();
          applyImages(images, version);
          MBox.show(S.title_received, 'Applied.', { hideAfter: 4000 });
        },
      });
    } catch (e) {
      MBox.show(S.title_received, 'Received invalid sysex data. ' + e.message, { hideAfter: 10000, type: 'error' });
    }
  }
  restore();
  render();
  const midi = new MIDI('PC12', sysexHandler);

  DOM.on('#btnreceive', 'click', function () {
    if (midi.hasInput()) MBox.show(S.title_receive, S.msg_receive);
    else MBox.show(STR.midictrl.title_error, STR.midictrl.noinputs, { type: 'error' });
  });
  DOM.on('#btnsend', 'click', function () {
    if (!midi.hasOutput()) return MBox.show(STR.midictrl.title_error, STR.midictrl.nooutputs, { type: 'error' });
    const data = buildSyx(current, state.version, image());
    MBox.show(S.title_send, STR.apply(S.msg_send, current, data.length), {
      buttonLabel: 'Send',
      confirmCallback: function () {
        MBox.hide();
        midi.sendSysex(data, 4000);
      },
    });
  });
  DOM.on('#btnsendall', 'click', function () {
    if (!midi.hasOutput()) return MBox.show(STR.midictrl.title_error, STR.midictrl.nooutputs, { type: 'error' });
    const data = buildAllSyx(state.version, allImages());
    MBox.show(S.title_send, STR.apply(S.msg_send_all, data.length), {
      buttonLabel: 'Send all',
      confirmCallback: function () {
        MBox.hide();
        midi.sendSysex(data, 15000);
      },
    });
  });

  function allImages() {
    const out = {};
    for (let s = 1; s <= PC12.SETUPS; s++) out[s] = image(s);
    if (state.images[31]) out[31] = state.images[31];
    return out;
  }
  function stamp() {
    return new Date().toISOString().slice(0, 10);
  }

  // -------- files
  DOM.on('#btnfileload', 'click', () => DOM.element('#fileinput').click());
  DOM.on('#fileinput', 'change', function (ev) {
    const file = ev.target.files[0];
    ev.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function () {
      try {
        const name = file.name.toLowerCase();
        let images, version;
        if (name.endsWith('.json')) {
          let docs = JSON.parse(reader.result);
          if (!Array.isArray(docs)) docs = [docs];
          images = {};
          for (const doc of docs) {
            if (!(doc.setup >= 1 && doc.setup <= 31) || !doc.controls) throw new Error('json needs "setup" and "controls"');
            images[doc.setup] = docToImage(doc, state.images[doc.setup]);
            version = doc.version;
          }
        } else if (name.endsWith('.csv')) {
          images = fromCsv(reader.result, state.images);
        } else {
          ({ version, images } = loadDump(new Uint8Array(reader.result)));
        }
        const list = applyImages(images, version);
        MBox.show(S.title_file, STR.apply(S.$msg_loaded, list.join(', ')), { hideAfter: 4000 });
      } catch (e) {
        MBox.show(S.title_error, e.message, { type: 'error' });
      }
    };
    if (file.name.toLowerCase().endsWith('.syx')) reader.readAsArrayBuffer(file);
    else reader.readAsText(file);
  });

  DOM.on('#btnsavesyx', 'click', function () {
    download(buildSyx(current, state.version, image()), `pc12_setup${String(current).padStart(2, '0')}_${stamp()}.syx`, 'application/octet-stream');
  });
  DOM.on('#btnsaveallsyx', 'click', function () {
    // one message per setup: pc12.py send and this editor both accept it, and the device can take them one at a time
    const parts = [];
    for (let s = 1; s <= PC12.SETUPS; s++) parts.push(buildSyx(s, state.version, image(s)));
    const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let off = 0;
    for (const p of parts) { all.set(p, off); off += p.length; }
    download(all, `pc12_all_${stamp()}.syx`, 'application/octet-stream');
  });
  DOM.on('#btnjson', 'click', function () {
    download(JSON.stringify(imageToDoc(current, state.version, image()), null, 1), `pc12_setup${String(current).padStart(2, '0')}_${stamp()}.json`, 'application/json');
  });
  DOM.on('#btnjsonall', 'click', function () {
    const docs = [];
    for (let s = 1; s <= PC12.SETUPS; s++) docs.push(imageToDoc(s, state.version, image(s)));
    download(JSON.stringify(docs, null, 1), `pc12_all_${stamp()}.json`, 'application/json');
  });
  DOM.on('#btncsv', 'click', function () {
    download(toCsv(allImages()), `pc12_all_${stamp()}.csv`, 'text/csv');
  });

  // -------- edit helpers
  DOM.on('#btnallchannels', 'click', function () {
    MBox.show('Set MIDI channel for all controls',
      `Apply one MIDI channel to every pot, key and encoder in setup ${current}.<br/><br/><div class="field"><label>Channel:</label>
       <input name="channel" type="text" size="3" value="" placeholder="1-16" /></div>`, {
      buttonLabel: 'Set channels',
      confirmCallback: function () {
        const v = DOM.element('#mbox input[name=channel]').value;
        if (v !== '') {
          const img = image(), ch = clamp(v, 1, 16) - 1;
          PC12.NAMES.forEach((n, i) => (img[ctrlAddr(i)] = ch));
          save();
          render();
        }
        MBox.hide();
      },
    });
  });
  DOM.on('#btncopy', 'click', function () {
    clipboard = new Uint8Array(image());
    MBox.show('Copy & Paste', `Setup ${current} copied.`, { hideAfter: 3000 });
  });
  DOM.on('#btnpaste', 'click', function () {
    if (!clipboard) return MBox.show('Copy & Paste', 'Nothing to paste.', { hideAfter: 3000 });
    MBox.show('Copy & Paste', `Paste over setup ${current}?`, {
      confirmCallback: function () {
        state.images[current] = new Uint8Array(clipboard);
        save();
        render();
        MBox.hide();
      },
    });
  });
  DOM.on('#btnfactory', 'click', function () {
    MBox.show('Reset', `Reset setup ${current} to factory values (CC 1-86 on channel ${((current - 1) % 16) + 1})?`, {
      confirmCallback: function () {
        state.images[current] = factoryImage(current);
        save();
        render();
        MBox.hide();
      },
    });
  });
  DOM.on('#btnforget', 'click', function () {
    MBox.show('Forget all', 'Remove every setup from this browser? Files you saved are not touched.', {
      confirmCallback: function () {
        state.images = {};
        localStorage.removeItem('pc12editor');
        render();
        MBox.hide();
      },
    });
  });
});
