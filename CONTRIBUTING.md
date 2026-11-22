# Contributing

## Adding a language

English is the source catalog: `dflash_eee/locales/en.json`. Spanish lives in `dflash_eee/locales/es.json`. A missing key is shown in English.

1. Copy `en.json` to `dflash_eee/locales/<code>.json`. Use a short code such as `pt`.
2. Translate the values and leave the keys alone. Keep profile ids, MCU names, hex (`0xFACF`), register names (`VIN`, `HW-NR`, `DFPART`, `ERPART`) and part numbers as they are.
3. Keep the same `%(name)s` placeholders, with the same names, in every language. The order in the sentence can change. A translation that drops a placeholder falls back to the English sentence.
4. Add the language to `LANGUAGES` in `dflash_eee/i18n.py`. The value is the name drawn in the **Language / Idioma** menu, written the way a speaker of that language writes it (`"pt": "Português"`).
5. `dflash_to_eee.spec` bundles the whole `dflash_eee/locales` directory into the one-file exe, and the release workflow checks that `en.json` and `es.json` are present. A new catalog file is picked up with no extra install step.
6. Run `python3 -m pytest -q`. `tests/test_i18n.py` checks that every key in the translation also exists in English, that the placeholders match, and that a missing key falls back to English.

Built-in profile sentences also live in `dflash_eee/profiles.py`. If you change one of those English sentences, copy it into `en.json` (`profile.<id>.<field>` and `profile.<id>.source.<n>`) and update the translation. The test fails when `en.json` and the Python text drift apart.

The journal lines compared with `ci/input/logs` stay in English (`Unknown CMD type detected`, `Input file too short`, the VIN block from the original two-argument command). They are the 2017 decoder log.

A shop JSON file dropped in `profiles/` is shown as written. It is not translated.
