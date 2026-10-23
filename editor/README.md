# Faderfox PC12 web editor

Edit all 30 PC12 setups in the browser. No build step, no dependencies.
Based on the PC4 editor from [privatepublic-de/faderfox-editor](https://github.com/privatepublic-de/faderfox-editor)
(MIT, see `LICENSE.txt`) by way of the [lsim fork](https://github.com/lsim/faderfox-editor).
Only `lib/` is taken from there (one line changed: MIDI port is matched by name substring).
`pc12/` is new and mirrors `../pc12.py`.

## Run

Open `pc12/index.html` in Chrome or Edge (Web MIDI with sysex). `file://` works.
Or serve the parent folder and open http://localhost:8765/editor/pc12/index.html :

```bash
python -m http.server 8765 --bind 127.0.0.1
```

## Device steps (manual p.11)

- Setup mode: hold the red shift key and press a blue key.
- Dump out: hold button 7 while the bars run. `Sndc` = this setup, `SndA` = all.
- Dump in: hold button 8 until the display shows `rc00`, then press Send in the editor.
  Without `rc00` the PC12 ignores the dump.

## Files

- `.syx` one setup (type 2) or all setups (type 3, SndA). Save "all .syx" writes 30 single-setup messages in one file; `pc12.py send` and this editor both read it.
- `.json` same shape as `pc12.py decode`: `{setup, version, controls:{P01A:{ch,num,lo,hi,type,snap,display}, K01:{ch,num,lo,hi,type,toggle,led}, ENC:{ch,num,lo,hi,type,acc,display}}}`. "all .json" is an array of those. A control may carry a raw `mode` byte instead.
- `.csv` one row per control: `setup,control,ch,num,lo,hi,type,mode2,display,mode`. `mode2` is snap (pots), toggle (keys) or acceleration 0-3 (encoder). `display` is the display scale, or the LED mode for keys. `mode` is the raw byte and is only used when `type` is empty. Rows may be a subset; missing columns keep their current value.

Key and encoder decoding (types, toggle, LED, acceleration) is inferred from dumps and the manual's option order. Pot decoding and the key toggle bit were verified on the device.

Setups are kept in localStorage, so a reload does not lose edits.

Verified against the 2026-10-22 backups: loading the SndA dump and rebuilding it gives the identical bytes.
Sending a type-3 (all setups) dump to the device has not been tried yet.
