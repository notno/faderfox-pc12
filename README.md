# Faderfox PC12 tools

Tools for reading, editing and writing Faderfox PC12 setups over sysex.

- `pc12.py` — command line. Decode a `.syx` dump to JSON, encode JSON back, send and receive over MIDI (Windows). Run `python pc12.py --help`.
- `editor/` — web editor for all 30 setups in Chrome or Edge. Saves `.syx`, `.json` or `.csv`. See [editor/README.md](editor/README.md).
- `backups/` — dumps from a PC12 with firmware 1.04, used to verify both tools.
- `syx/` — single-change dumps used to work out the byte format.
- Other `*.py` files are earlier experiments.

The sysex format is documented at the top of `pc12.py`.
The editor's `lib/` folder is from [privatepublic-de/faderfox-editor](https://github.com/privatepublic-de/faderfox-editor) (MIT, see `editor/LICENSE.txt`).
