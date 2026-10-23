#!/usr/bin/env python3
"""Faderfox PC12 single-setup sysex <-> JSON.

Dump format (shared across Faderfox devices): every value is a nibble triplet
  4X 2H 1L   X = command, value = H<<4 | L
Commands: 1 device(0x0A=PC12) 2 dumptype(2=one setup) 3/4 version,
          9/A address hi/lo, D data byte, B/C 16-bit sum of page data, F stop.
A setup is 8 pages x 64 bytes at address setup*0x200.
Pages 0-5 = pot rows A-F, page 6 = keys 1-12, page 7 = encoder + encoder push.
All-setups dump (SndA, dumptype 3): setups 1-30 at 0x0200-0x3DFF, copy buffer at 0x3E00,
0x4000-0x4EFF presets (all zero when unused).
Each control is 5 bytes: [channel-1, number, lower, upper, mode].
Mode byte: bits0-2 type, bits3-4 command mode, bits5-6 display scale (manual p.8-9).
  pot:     type 0 note 1 cc 2 prgchange 3 pitchbend 4 aftertouch; bit3 snap; display 00 off 01 normal 10 bipolar
  key/ENC2: type 1 note 2 cc 3 prgchange 4 aftertouch; bit3 toggle (0 = momentary); led 00 off 01 std 10 ext
  ENC:     type 0 ccr1 1 ccr2 2 ccabs 3 prgchange 4 cc14bit 5 pitchbend 6 aftertouch 7 note; bits3-4 acc 0-3
Key and encoder decoding is inferred from dumps + manual order (2026-10-22); pot decoding is verified on the device.
Key toggle bit confirmed on the device: factory keys (0x2a) stay lit after one press.

Device side (manual p.11): setup mode = hold red shift + blue key.
  Dump out: hold button 7 until bars finish, Sndc = this setup, SndA = all.
  Dump in:  hold button 8 until display shows rc00, THEN run `send`. Without rc00 the dump is ignored.
Verified 2026-10-22: set -> send -> device -> receive round trip on firmware 1.04.
"""
import argparse, json, sys, time

DEVICE_PC12 = 0x0A
PAGE = 64
CTRL = 5
TYPES = ["note", "cc", "prgchange", "pitchbend", "aftertouch"]
KEY_TYPES = {1: "note", 2: "cc", 3: "prgchange", 4: "aftertouch"}
ENC_TYPES = ["ccr1", "ccr2", "ccabs", "prgchange", "cc14bit", "pitchbend", "aftertouch", "note"]
DISPLAY = {0b01: "normal", 0b10: "bipolar", 0b00: "off"}
LED = {0b01: "std", 0b10: "ext", 0b00: "off"}
ROWS = "ABCDEF"
STOCK_PADDING = 30


def names():
    out = [f"P{c:02d}{r}" for r in ROWS for c in range(1, 13)]
    out += [f"K{k:02d}" for k in range(1, 13)]
    out += ["ENC", "ENC2"]
    return out


def ctrl_addr(index):
    """Byte offset of control #index inside the 512-byte setup image."""
    page, slot = divmod(index, 12)
    return page * PAGE + slot * CTRL


# ---------------------------------------------------------------- sysex <-> image

def parse_pages(data):
    """Any PC12 dump -> (hdr, [page]). Checks triplets, device and checksums."""
    toks = [b for b in data if b not in (0xF0, 0xF7, 0x00)]
    hdr, pages, cur = {}, [], None
    for j in range(0, len(toks) - 2, 3):
        c, hi, lo = toks[j], toks[j + 1], toks[j + 2]
        if (c & 0xF0, hi & 0xF0, lo & 0xF0) != (0x40, 0x20, 0x10):
            raise ValueError(f"bad triplet {c:02x} {hi:02x} {lo:02x} at token {j}")
        cmd, v = c & 0xF, ((hi & 0xF) << 4) | (lo & 0xF)
        if cmd in (1, 2, 3, 4):
            hdr[cmd] = v
        elif cmd == 9:
            cur = {"addr": v << 8, "data": []}
        elif cmd == 0xA:
            cur["addr"] |= v
        elif cmd == 0xD:
            cur["data"].append(v)
        elif cmd == 0xB:
            cur["ck"] = v << 8
        elif cmd == 0xC:
            cur["ck"] |= v
            if cur["ck"] != sum(cur["data"]) & 0xFFFF:
                raise ValueError(f"checksum mismatch in page 0x{cur['addr']:04x}")
            pages.append(cur)
            cur = None
    if hdr.get(1) != DEVICE_PC12:
        raise ValueError(f"not a PC12 dump (device 0x{hdr.get(1, 0):02x})")
    return hdr, pages


def parse_syx(data):
    """Single-setup dump -> (setup, version, 512-byte image)."""
    hdr, pages = parse_pages(data)
    if hdr.get(2) == 3:
        raise ValueError("this is an all-setups dump; use `split` first")
    if hdr.get(2) != 2 or len(pages) != 8:
        raise ValueError("expected a single-setup dump with 8 pages")
    base = pages[0]["addr"]
    if base % 0x200 or any(p["addr"] != base + i * PAGE for i, p in enumerate(pages)):
        raise ValueError("unexpected page addresses")
    image = bytearray()
    for p in pages:
        image += bytes(p["data"])
    return base // 0x200, (hdr[3], hdr[4]), image


def split_all(data):
    """All-setups dump -> {setup: single-setup .syx bytes}. Slot 31 is the copy buffer."""
    hdr, pages = parse_pages(data)
    if hdr.get(2) != 3:
        raise ValueError("not an all-setups dump")
    version = (hdr[3], hdr[4])
    by_addr = {p["addr"]: p["data"] for p in pages}
    out = {}
    for setup in range(1, 32):
        base = setup * 0x200
        if all(base + i * PAGE in by_addr for i in range(8)):
            image = b"".join(bytes(by_addr[base + i * PAGE]) for i in range(8))
            out[setup] = build_syx(setup, version, image)
    return out


def trip(cmd, v):
    return bytes([0x40 | cmd, 0x20 | (v >> 4), 0x10 | (v & 0xF)])


def build_syx(setup, version, image):
    out = bytearray(b"\xF0\x00\x00\x00")
    out += trip(1, DEVICE_PC12) + trip(2, 2) + trip(3, version[0]) + trip(4, version[1])
    for i in range(8):
        addr = setup * 0x200 + i * PAGE
        page = image[i * PAGE:(i + 1) * PAGE]
        out += trip(9, addr >> 8) + trip(0xA, addr & 0xFF)
        for b in page:
            out += trip(0xD, b)
        s = sum(page) & 0xFFFF
        out += trip(0xB, s >> 8) + trip(0xC, s & 0xFF)
        out += bytes(STOCK_PADDING)
    out += trip(0xF, DEVICE_PC12) + b"\xF7"
    return bytes(out)


# ---------------------------------------------------------------- image <-> dict

def decode_ctrl(name, b):
    d = {"ch": b[0] + 1, "num": b[1], "lo": b[2], "hi": b[3]}
    m = b[4]
    t, scale = m & 7, (m >> 5) & 3
    if name.startswith("P"):
        d["type"] = TYPES[t] if t < len(TYPES) else t
        d["snap"] = bool(m & 0x08)
        d["display"] = DISPLAY.get(scale, scale)
        extra = m & 0x90
    elif name == "ENC":
        d["type"] = ENC_TYPES[t]
        d["acc"] = (m >> 3) & 3
        d["display"] = DISPLAY.get(scale, scale)
        extra = m & 0x80
    else:  # keys and encoder push
        d["type"] = KEY_TYPES.get(t, t)
        d["toggle"] = bool(m & 0x08)
        d["led"] = LED.get(scale, scale)
        extra = m & 0x90
    if extra:
        d["mode_extra"] = extra
    return d


def _lookup(table, v):
    """name -> code via a list or {code: name} dict; numbers pass through."""
    if not isinstance(v, str):
        return v
    if isinstance(table, dict):
        return {n: c for c, n in table.items()}[v]
    return table.index(v)


def encode_ctrl(name, d):
    if "mode" in d:  # raw byte, as older json files have for keys
        m = d["mode"]
    elif name.startswith("P"):
        m = _lookup(TYPES, d["type"])
        m |= 0x08 if d.get("snap") else 0
        m |= _lookup(DISPLAY, d.get("display", "normal")) << 5
        m |= d.get("mode_extra", 0)
    elif name == "ENC":
        m = _lookup(ENC_TYPES, d["type"])
        m |= (d.get("acc", 0) & 3) << 3
        m |= _lookup(DISPLAY, d.get("display", "normal")) << 5
        m |= d.get("mode_extra", 0)
    else:
        m = _lookup(KEY_TYPES, d["type"])
        m |= 0x08 if d.get("toggle") else 0
        m |= _lookup(LED, d.get("led", "std")) << 5
        m |= d.get("mode_extra", 0)
    return bytes([d["ch"] - 1, d["num"], d["lo"], d["hi"], m])


def image_to_dict(image):
    return {n: decode_ctrl(n, image[ctrl_addr(i):ctrl_addr(i) + CTRL]) for i, n in enumerate(names())}


def dict_to_image(ctrls):
    image = bytearray(b"\xFF" * 8 * PAGE)
    for i, n in enumerate(names()):
        a = ctrl_addr(i)
        image[a:a + CTRL] = encode_ctrl(n, ctrls[n])
    return bytes(image)


# ---------------------------------------------------------------- midi out (Windows winmm, no deps)

def midi_out_ports():
    import ctypes, ctypes.wintypes as w
    class CAPS(ctypes.Structure):
        _fields_ = [("wMid", w.WORD), ("wPid", w.WORD), ("vDriverVersion", w.UINT),
                    ("szPname", w.WCHAR * 32), ("wTechnology", w.WORD), ("wVoices", w.WORD),
                    ("wNotes", w.WORD), ("wChannelMask", w.WORD), ("dwSupport", w.DWORD)]
    winmm = ctypes.windll.winmm
    out = []
    for i in range(winmm.midiOutGetNumDevs()):
        c = CAPS()
        winmm.midiOutGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c))
        out.append(c.szPname)
    return out


def midi_send(port_index, messages, delay):
    import ctypes, ctypes.wintypes as w
    class MIDIHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_char_p), ("dwBufferLength", w.DWORD), ("dwBytesRecorded", w.DWORD),
                    ("dwUser", ctypes.c_void_p), ("dwFlags", w.DWORD), ("lpNext", ctypes.c_void_p),
                    ("reserved", ctypes.c_void_p), ("dwOffset", w.DWORD), ("dwReserved", ctypes.c_void_p * 8)]
    MHDR_DONE = 1
    winmm = ctypes.windll.winmm
    h = ctypes.c_void_p()
    if winmm.midiOutOpen(ctypes.byref(h), port_index, None, None, 0):
        sys.exit(f"could not open MIDI out port {port_index}")
    try:
        for msg in messages:
            buf = ctypes.create_string_buffer(msg, len(msg))
            hdr = MIDIHDR(lpData=ctypes.cast(buf, ctypes.c_char_p), dwBufferLength=len(msg), dwBytesRecorded=len(msg))
            if winmm.midiOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr)):
                sys.exit("midiOutPrepareHeader failed")
            if winmm.midiOutLongMsg(h, ctypes.byref(hdr), ctypes.sizeof(hdr)):
                sys.exit("midiOutLongMsg failed")
            while not hdr.dwFlags & MHDR_DONE:
                time.sleep(0.01)
            winmm.midiOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
            time.sleep(delay)
    finally:
        winmm.midiOutClose(h)


def midi_in_ports():
    import ctypes, ctypes.wintypes as w
    class CAPS(ctypes.Structure):
        _fields_ = [("wMid", w.WORD), ("wPid", w.WORD), ("vDriverVersion", w.UINT),
                    ("szPname", w.WCHAR * 32), ("dwSupport", w.DWORD)]
    winmm = ctypes.windll.winmm
    out = []
    for i in range(winmm.midiInGetNumDevs()):
        c = CAPS()
        winmm.midiInGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c))
        out.append(c.szPname)
    return out


def midi_receive(port_index, on_message, idle, timeout, verbose=False):
    """Collect sysex messages until `idle` seconds pass after the last one
    (or `timeout` seconds pass with nothing at all). Ctrl-C also stops."""
    MIM = {0x3C1: "OPEN", 0x3C2: "CLOSE", 0x3C3: "DATA", 0x3C4: "LONGDATA", 0x3C5: "ERROR",
           0x3C6: "LONGERROR", 0x3C7: "MOREDATA"}
    t0 = time.time()
    log = []

    def note(s):
        log.append(f"{time.time() - t0:8.3f}s  {s}")
    import ctypes, ctypes.wintypes as w
    class MIDIHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", w.DWORD), ("dwBytesRecorded", w.DWORD),
                    ("dwUser", ctypes.c_void_p), ("dwFlags", w.DWORD), ("lpNext", ctypes.c_void_p),
                    ("reserved", ctypes.c_void_p), ("dwOffset", w.DWORD), ("dwReserved", ctypes.c_void_p * 8)]
    CALLBACK_FUNCTION, MIM_LONGDATA, BUFSIZE, NBUF = 0x30000, 0x3C4, 1 << 20, 4
    winmm = ctypes.windll.winmm
    winmm.midiInAddBuffer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    got, last, closing = [], [time.time()], [False]

    @ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hin, msg, inst, p1, p2):
        if msg not in (MIM_LONGDATA, 0x3C6):
            if verbose:
                note(f"{MIM.get(msg, hex(msg))} p1=0x{(p1 or 0):x} p2=0x{(p2 or 0):x}")
            return
        hdr = ctypes.cast(p1, ctypes.POINTER(MIDIHDR)).contents
        n = hdr.dwBytesRecorded
        if verbose:
            d = ctypes.string_at(hdr.lpData, min(n, 12)) if n else b""
            tail = ctypes.string_at(hdr.lpData + n - 4, 4) if n >= 4 else b""
            note(f"{MIM[msg]} bytes={n} flags=0x{hdr.dwFlags:x} head={d.hex(' ')} tail={tail.hex(' ')}")
        if n:
            got.append(ctypes.string_at(hdr.lpData, n))
            last[0] = time.time()
        if not closing[0]:
            r = winmm.midiInAddBuffer(hin, p1, ctypes.sizeof(MIDIHDR))
            if r and verbose:
                note(f"midiInAddBuffer failed: {r}")

    h = ctypes.c_void_p()
    if winmm.midiInOpen(ctypes.byref(h), port_index, cb, None, CALLBACK_FUNCTION):
        sys.exit(f"could not open MIDI in port {port_index}")
    bufs, hdrs = [], []
    for _ in range(NBUF):
        b = ctypes.create_string_buffer(BUFSIZE)
        hd = MIDIHDR(lpData=ctypes.cast(b, ctypes.c_void_p), dwBufferLength=BUFSIZE)
        winmm.midiInPrepareHeader(h, ctypes.byref(hd), ctypes.sizeof(hd))
        winmm.midiInAddBuffer(h, ctypes.byref(hd), ctypes.sizeof(hd))
        bufs.append(b); hdrs.append(hd)
    winmm.midiInStart(h)
    start, n = time.time(), 0
    try:
        while True:
            time.sleep(0.05)
            while log:
                print("  [midi] " + log.pop(0))
            while n < len(got):
                on_message(got[n]); n += 1
            now = time.time()
            if got and now - last[0] > idle:
                break
            if not got and now - start > timeout:
                break
    except KeyboardInterrupt:
        pass
    finally:
        closing[0] = True
        winmm.midiInStop(h)
        winmm.midiInReset(h)
        for hd in hdrs:
            winmm.midiInUnprepareHeader(h, ctypes.byref(hd), ctypes.sizeof(hd))
        winmm.midiInClose(h)
        if verbose:
            for line in log:
                print("  [midi] " + line)
    return n


def split_sysex(data):
    """Split a .syx file into its F0..F7 messages."""
    msgs, start = [], None
    for i, b in enumerate(data):
        if b == 0xF0:
            start = i
        elif b == 0xF7 and start is not None:
            msgs.append(bytes(data[start:i + 1]))
            start = None
    if not msgs:
        sys.exit("no sysex messages in file")
    return msgs


# ---------------------------------------------------------------- cli

def load(path):
    setup, version, image = parse_syx(open(path, "rb").read())
    return {"setup": setup, "version": list(version), "controls": image_to_dict(image)}


def save(doc, path, setup=None):
    syx = build_syx(setup or doc["setup"], tuple(doc["version"]), dict_to_image(doc["controls"]))
    open(path, "wb").write(syx)


def cmd_show(a):
    doc = load(a.syx)
    print(f"setup {doc['setup']}  firmware {doc['version'][0]}.{doc['version'][1]:02d}")
    for n, c in doc["controls"].items():
        if n.startswith("P"):
            extra = f"{c['type']:<10} snap={'y' if c['snap'] else 'n'} disp={c['display']}"
        elif n == "ENC":
            extra = f"{c['type']:<10} acc={c['acc']} disp={c['display']}"
        else:
            extra = f"{c['type']:<10} toggle={'y' if c['toggle'] else 'n'} led={c['led']}"
        if "mode_extra" in c:
            extra += f" extra=0x{c['mode_extra']:02x}"
        print(f"{n:5} ch={c['ch']:<2} num={c['num']:<3} lo={c['lo']:<3} hi={c['hi']:<3} {extra}")


def cmd_decode(a):
    with open(a.json, "w") as f:
        json.dump(load(a.syx), f, indent=1)


def cmd_encode(a):
    with open(a.json) as f:
        save(json.load(f), a.syx, a.setup)


def cmd_set(a):
    doc = load(a.syx)
    for spec in a.edits:
        name, _, kv = spec.partition(":")
        if name not in doc["controls"]:
            sys.exit(f"unknown control {name}")
        for pair in kv.split(","):
            k, _, v = pair.partition("=")
            cur = doc["controls"][name][k]
            if isinstance(cur, bool):
                v = v.lower() in ("1", "y", "yes", "on", "true")
            elif isinstance(cur, int):
                v = int(v, 0)
            doc["controls"][name][k] = v
    save(doc, a.out, a.setup)


def cmd_send(a):
    ports = midi_out_ports()
    idx = pick_port(ports, a.port)
    with open(a.syx, "rb") as f:
        msgs = split_sysex(f.read())
    if not a.raw:
        try:
            for m in msgs:  # refuse to send a dump that does not decode cleanly
                parse_pages(m)
        except ValueError as e:
            sys.exit(f"not sending: {e} (use --raw to send anyway)")
    print(f"sending {len(msgs)} message(s), {sum(map(len, msgs))} bytes to {idx}: {ports[idx]}")
    midi_send(idx, msgs, a.delay)
    print("done")


def pick_port(ports, spec):
    if spec is None:
        hits = [i for i, n in enumerate(ports) if "PC12" in n]
        if len(hits) != 1:
            sys.exit(f"found {len(hits)} ports named PC12; pick one with --port. Ports: {ports}")
        return hits[0]
    if spec.isdigit():
        return int(spec)
    hits = [i for i, n in enumerate(ports) if spec.lower() in n.lower()]
    if len(hits) != 1:
        sys.exit(f"--port {spec!r} matched {len(hits)} ports: {ports}")
    return hits[0]


def cmd_receive(a):
    import os
    ports = midi_in_ports()
    idx = pick_port(ports, a.port)
    os.makedirs(a.outdir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    count = [0]

    def save_msg(label, msg):
        path = os.path.join(a.outdir, f"{stamp}_{label}.syx")
        if os.path.exists(path):
            path = os.path.join(a.outdir, f"{stamp}_{label}_{count[0]:02d}.syx")
        with open(path, "wb") as f:
            f.write(msg)
        print(f"  saved {len(msg)} bytes -> {path}")

    def on_message(msg):
        count[0] += 1
        try:
            hdr, pages = parse_pages(msg)
        except ValueError as e:
            print(f"  (bad dump: {e}; first bytes {msg[:16].hex(' ')})")
            save_msg(f"msg{count[0]:02d}", msg)
            return
        if hdr.get(2) == 3:
            save_msg("all", msg)
            for setup, syx in split_all(msg).items():
                save_msg("copybuf" if setup == 31 else f"setup{setup:02d}", syx)
            return
        try:
            setup, _, _ = parse_syx(msg)
            save_msg(f"setup{setup:02d}", msg)
        except ValueError as e:
            print(f"  ({e})")
            save_msg(f"msg{count[0]:02d}", msg)

    print(f"listening on {idx}: {ports[idx]}")
    print(f"now send the dump from the PC12. stops {a.idle}s after the last message, Ctrl-C to stop early")
    n = midi_receive(idx, on_message, a.idle, a.timeout, a.verbose)
    print(f"received {n} message(s)")


def cmd_split(a):
    import os
    with open(a.syx, "rb") as f:
        data = f.read()
    os.makedirs(a.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.syx))[0]
    for setup, syx in split_all(data).items():
        label = "copybuf" if setup == 31 else f"setup{setup:02d}"
        path = os.path.join(a.outdir, f"{stem}_{label}.syx")
        with open(path, "wb") as f:
            f.write(syx)
        print(f"  {path}")


def cmd_ports(a):
    print("out:")
    for i, n in enumerate(midi_out_ports()):
        print(f"  {i}: {n}")
    print("in:")
    for i, n in enumerate(midi_in_ports()):
        print(f"  {i}: {n}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ports", help="list MIDI ports")
    s.set_defaults(f=cmd_ports)
    s = sub.add_parser("split", help="split an all-setups dump (SndA) into one .syx per setup")
    s.add_argument("syx")
    s.add_argument("outdir", nargs="?", default=".")
    s.set_defaults(f=cmd_split)
    s = sub.add_parser("receive", help="wait for dumps from the PC12 and save them with a date stamp (Windows only)")
    s.add_argument("outdir", nargs="?", default="backups")
    s.add_argument("--port", help="port index or name substring (default: the port named PC12)")
    s.add_argument("--idle", type=float, default=15, help="stop this many seconds after the last dump (default 15)")
    s.add_argument("--timeout", type=float, default=300, help="give up after this many seconds with nothing (default 300)")
    s.add_argument("--verbose", "-v", action="store_true", help="log every MIDI driver event")
    s.set_defaults(f=cmd_receive)
    s = sub.add_parser("send", help="send a .syx file to the PC12 (Windows only)")
    s.add_argument("syx")
    s.add_argument("--port", help="port index or name substring (default: the port named PC12)")
    s.add_argument("--delay", type=float, default=0.1, help="seconds between messages (default 0.1)")
    s.add_argument("--raw", action="store_true", help="send without checking that the file is a valid setup dump")
    s.set_defaults(f=cmd_send)
    s = sub.add_parser("show", help="print a setup dump as a table")
    s.add_argument("syx")
    s.set_defaults(f=cmd_show)
    s = sub.add_parser("decode", help="syx -> json")
    s.add_argument("syx")
    s.add_argument("json")
    s.set_defaults(f=cmd_decode)
    s = sub.add_parser("encode", help="json -> syx")
    s.add_argument("json")
    s.add_argument("syx")
    s.add_argument("--setup", type=int, help="target setup slot 1-30 (default: the one in the json)")
    s.set_defaults(f=cmd_encode)
    s = sub.add_parser("set", help="edit controls in place, e.g. P03C:num=74,ch=2 K01:num=60")
    s.add_argument("syx")
    s.add_argument("out")
    s.add_argument("edits", nargs="+")
    s.add_argument("--setup", type=int)
    s.set_defaults(f=cmd_set)
    a = p.parse_args()
    try:
        a.f(a)
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
