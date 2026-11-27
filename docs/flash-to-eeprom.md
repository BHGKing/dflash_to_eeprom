# D-Flash to EEPROM

How this project rebuilds a Freescale/NXP S12XE emulated EEPROM from a raw D-Flash dump, and the code to copy into another program.

The conversion is a replay of a circular journal. D-Flash is the log. The EEPROM file is the log applied from oldest sector to newest, so the latest write to each word wins. The desktop window, the command line, and `webserver/convert.php` all call the same replay.

BSD-3-Clause. Copyright 2017-2018 Ben van Leeuwen Autotechniek. Copyright 2026 Jose. A copy of this code has to keep that notice, the conditions, and the disclaimer in `LICENSE`.

## What to copy

| Stay in Python and import this package | Paste a standalone function |
| --- | --- |
| `convert_bytes` and `get_profile` from `dflash_eee` | The function at the bottom of this file |
| Geometry lives in `dflash_eee/profiles.py` | Same sector size, tags, and word base |
| Warnings, VIN, and part numbers come along | Image bytes only |

`dflash_eee/converter.py` is the replay. `dflash_eee/profiles.py` holds the sector constants and the per-module sizes. The GUI, compare view, virginize, and web form are callers. They are not part of the conversion.

The standalone function was checked against `convert_bytes` on every 32 KB sample in `ci/input/dflash`. All 25 files that produce an EEPROM image matched byte for byte. The three short files raise `Input file too short` on both paths.

## Sector layout

D-Flash on the MC9S12XE series is 32 KB: 128 sectors of 256 bytes. A sector that holds EEPROM updates is a 4-byte header plus 63 records. Every multi-byte field is big-endian.

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 2 bytes | Sector tag. `0xFACF` is a data sector. `0xFFFF` is erased. |
| 2 | 2 bytes | Second header word. `0xFFFF` means the sector is still erased. Any other value (the FRM dumps use `0xFFFE`) means the sector was prepared as the next one to write. The replay does not decode this word. |
| 4 | 63 × 4 bytes | Each record is a 16-bit address word and a 16-bit data word. |

Constants from `dflash_eee/profiles.py`:

```python
SECTOR_SIZE = 256
HEADER_SIZE = 4
RECORD_SIZE = 4
RECORDS_PER_SECTOR = 63

BLOCK_VALID = 0xFACF
BLOCK_EMPTY = 0xFFFF
CMD_MASK = 0xF800
CMD_VALID = 0xB800
CMD_EMPTY = 0xF800

DFLASH_BYTES = 32 * 1024
DFLASH_SECTORS = 128
```

The address word is split with `0xF800`:

- `0xB800 | word_index` is a programmed record. The low 11 bits (`raw & 0x07FF`) are the word index in the 4 KB buffer at global address `0x13_F000`.
- `0xF800` is an empty slot. It marks the sector as the one currently being filled. The scan keeps reading the rest of the sector. Later records in that sector are still applied. The sample dumps depend on this.
- Any other top bits are an unknown command. The library marks the image corrupt and continues.

A word in the rebuilt image starts as `0xFFFF` and changes when a later record names it.

After the header pass, each sector is one of:

| State | Header | Role |
| --- | --- | --- |
| `EMPTY` | `FFFF FFFF` | Erased, never prepared. |
| `NEW` | `FFFF` and a second word other than `FFFF` | Prepared, waiting to be written. |
| `VALID` | tag `0xFACF`, every record programmed | Closed data sector. |
| `LAST` | tag `0xFACF`, at least one empty record | Sector in progress. |
| `INVALID` | any other tag | Not an EEE sector. |

FRM3 and the Jaguar XF BCM treat `INVALID` as an error. The KVM profile only warns, because 16 of its 128 sectors are user D-Flash in the same 32 KB file. An unknown command inside a real EEE sector is still an error there.

## EEPROM size and word base

The record address is a word index in a 4 KB buffer (2048 words, global `0x13_F000`–`0x13_FFFF`). A smaller EEPROM is the top of that buffer. `eee_word_base` in `dflash_eee/profiles.py` is the first word of the slice:

```python
def eee_word_base(pages: int) -> int:
    # pages is ERPART. 16 pages = 4 KB, 8 pages = 2 KB.
    return (16 - pages) * 128
```

| Profile | Status | Input | Output | Word base | Partition |
| --- | --- | --- | --- | --- | --- |
| `bmw_frm3` | Supported | 32 KB | 4 KB | 0 | DFPART 0, ERPART 16 |
| `jlr_xf_bcm` | Supported | 32 KB | 2 KB | 1024 | DFPART 0, ERPART 8 |
| `bmw_frm_f_xet512` | Experimental | 32 KB assumed | 4 KB assumed | 0 | Copied from FRM3, not confirmed |
| `jlr_kvm` | Experimental | 32 KB raw read | 2 KB | 1024 | DFPART 16, ERPART 8 |
| `bmw_cas4` | Refused | — | — | — | No EEE journal. `conversion_enabled` is false |

AN3490's partition rule, implemented by `partition_is_valid`: with 128 D-Flash sectors, `(128 - DFPART) / ERPART` is at least 8. When ERPART is 1, at least 12 sectors remain for EEE. ERPART 0 means no EEE, and then DFPART is 128.

Program the output into the EEE area after setting that partition. Keep the original dump. Setting the partition erases D-Flash.

## Call the library

```python
from dflash_eee import convert_bytes, get_profile

result = convert_bytes(open("dflash.bin", "rb").read(), get_profile("bmw_frm3"))
if result.ok:
    open("eeprom.bin", "wb").write(result.image)
```

`convert_file(source, destination, profile)` does the same and writes the file when an image exists.

`result.image` is `None` when the dump is shorter than the scan window, the profile is refused, or every record fell outside the EEPROM window. `result.ok` is true when an image exists and there are no error issues. `result.corrupt` is set for unknown commands, unknown sector tags, or addresses outside the window. `result.stats` counts sectors by state, names the sector chosen as the journal end, and records how many words landed. `result.fields` is the FRM3 VIN and part numbers, and only for `bmw_frm3`.

`get_profile("jlr_xf_bcm")` is the same call and returns a 2 KB image. In the standalone function that is `ee_bytes=2048` and `word_base=1024`.

`convert_bytes` catches a short read and returns a result with `image is None`. The historical file API, `DFlashConverter.convert`, lets `Input file too short` propagate. The two-argument command `python3 dflash_to_eee.py dflash.bin eeprom.bin` is that historical FRM3 path.

## Replay order

`DFlashConverter` in `dflash_eee/converter.py` does three things: classify sectors, find the end of the ring, apply records oldest-first.

### 1. Classify every sector

`_read_stream` reads `scan_sectors` sectors from `scan_offset`. Every built-in profile uses offset 0 and 128 sectors.

```python
header = struct.unpack(">HH", blockheader)
if header[0] == self.BLOCK_EMPTY:
    if header[1] == self.BLOCK_EMPTY_CLEARED:
        self.block_types.append(self.EMPTY)
    else:
        # Block has already been prepared (FFFFFFFE in the FRM dumps).
        self.block_types.append(self.NEW)
elif header[0] == self.BLOCK_VALID:
    self.block_types.append(self.VALID)
    for _blockitem in range(self.cmds_per_block):
        datapair = struct.unpack(">HH", cmdread)
        cmd = datapair[0] & self.CMD_MASK
        addr = datapair[0] & ~self.CMD_MASK
        data = datapair[1]
        if cmd == self.CMD_VALID:
            self.block_data[blockid].append((addr, data))
        elif cmd == self.CMD_EMPTY:
            # Does not break. Later words in the sector are still
            # records. The samples rely on that.
            self.block_types[-1] = self.LAST
        else:
            # Unknown CMD. corrupt = True, scan continues.
            ...
else:
    self.block_types.append(self.INVALID)
```

`datapair[0] & ~CMD_MASK` keeps the low 11 bits, the same value as `raw & 0x07FF`.

### 2. Find the end of the journal

The erased and prepared sectors are the gap in front of the write pointer. `_find_endblock` returns the last sector that still holds data.

- One run of `NEW` sectors: the end is the sector immediately before that run. A second separate `NEW` run is rejected (`_find_new_blocks` returns `None`).
- No `NEW` run, and exactly one `LAST` sector: that sector is the end. Zero or several `LAST` sectors are rejected.
- Neither marker: the end is the sector immediately before the longest run of `EMPTY` or `NEW`, searched on the sector list concatenated with itself so a run that wraps past sector 0 is seen.
- When a `NEW` run and a `LAST` sector both exist, the `NEW` run wins. If `LAST` does not sit against that run with only `EMPTY` sectors between them, the library logs an inconsistency and still uses the `NEW` run.

`_find_new_blocks` rotates a leading run of `NEW` and `EMPTY` onto the end of the list before it searches, so a prepared gap that wraps from the last sector back to sector 0 is one run. If that rotation walks every sector, the dump is entirely erased or prepared and the function raises `All blocks seem to be new blocks`.

### 3. Apply records from oldest to newest

The image starts as `ee_words` copies of `0xFFFF`. Walking begins at the sector after the end, continues through the last sector, then wraps to sector 0. A later record overwrites an earlier one.

```python
def _build_image(self) -> None:
    startblock = (self.endblock + 1) % self.NB_BLOCKS
    base = self.profile.eee_word_base
    data = [0xFFFF] * self.EESIZE
    order = list(range(startblock, self.NB_BLOCKS)) + list(range(0, startblock))
    for block in order:
        for addr, value in self.block_data[block]:
            slot = addr - base
            if slot < 0 or slot >= self.EESIZE:
                self.out_of_range += 1
                self.corrupt = True
                continue
            data[slot] = value
            self.landed += 1
    self.data = data
```

The file is those words packed big-endian:

```python
b"".join(struct.pack(">H", word) for word in self.data)
```

Word 0 is the first two bytes of the EEPROM image. A 2 KB profile subtracts 1024 from each address, which keeps words 1024 through 2047 (bytes `0x800`–`0xFFF` of a 4 KB replay, global `0x13_F800`–`0x13_FFFF`) and drops the lower half. `INVALID`, `EMPTY`, and `NEW` sectors have no records, so they add nothing. They still occupy a slot in the ring, which is what makes the wrap land on the right sector.

If records were seen and none of them landed inside the window, `to_result` returns `image=None` so a file of `0xFF` is not written. `smallest_erpart` then names the smallest top-aligned ERPART that would contain the addresses that were seen.

## Byte addresses inside the image

FRM3 field offsets are byte addresses in the rebuilt EEPROM, big-endian: an even address is the high byte of its word, an odd address is the low byte. They were measured on the FRM3 samples. Other profiles do not use them.

| Field | Offset | Length |
| --- | --- | --- |
| VIN | `0xFD3` | 17 ASCII bytes |
| Production date | day `0xFBE`, month `0xFBD`, year `0xFBB` `0xFBC` | printed with `%02x` as `dd.mm.yyyy` |
| Programming date | day `0xF89`, month `0xF88`, year `0xF86` `0xF87` | same |
| HW-NR | `0xF97` | 6 bytes, big-endian integer |
| SW-NR | `0xF8A` | 6 bytes |
| ZB-NR | `0xF65` | 6 bytes |
| S | `0xFBF` | 6 bytes |

```python
def byte_from_words(words, addr):
    if addr % 2 == 1:
        return words[addr // 2] & 0xFF
    return words[addr // 2] >> 8
```

## Rules the sample set depends on

- An empty record marks the sector `LAST` and the loop does not `break`. Records after that empty slot are still stored.
- A single `NEW` run is trusted ahead of a `LAST` sector when both are present.
- The sector before the gap is the newest data. Replay starts on the next sector, which is the oldest, and walks forward so later writes overwrite.
- The output is big-endian 16-bit words, initialized to `0xFFFF`.
- Addresses outside `[word_base, word_base + ee_bytes/2)` are counted and skipped. They mark the image corrupt. If nothing landed, there is no output file.
- A second `NEW` run, or more than one `LAST` sector, drops that marker and the other marker (or the longest erased run) is used instead.

## Standalone function

Same three steps, with the profile knobs as arguments. The default is FRM3: 4 KB, word base 0, 128 sectors from the start of the file. Jaguar XF BCM and the KVM window are `replay_dflash(data, ee_bytes=2048, word_base=1024)`.

This copy returns the image bytes. It does not translate warning strings or decode the VIN. Unknown commands are skipped the same way the library still applies the valid records around them. Copy the copyright header from `dflash_eee/converter.py` with it.

```python
# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license.

import struct

SECTOR = 256
HEADER = 4
RECORDS = 63
BLOCK_VALID = 0xFACF
BLOCK_EMPTY = 0xFFFF
CMD_MASK = 0xF800
CMD_VALID = 0xB800
CMD_EMPTY = 0xF800


def replay_dflash(blob, ee_bytes=4096, word_base=0, sectors=128, origin=0):
    """Return the EEPROM image. word_base is 0 for 4 KB and 1024 for 2 KB."""
    types = []
    records = [[] for _ in range(sectors)]
    for block in range(sectors):
        off = origin + block * SECTOR
        if off + HEADER > len(blob):
            raise ValueError("Input file too short")
        tag, second = struct.unpack_from(">HH", blob, off)
        if tag == BLOCK_EMPTY:
            types.append("EMPTY" if second == BLOCK_EMPTY else "NEW")
            continue
        if tag != BLOCK_VALID:
            types.append("INVALID")
            continue
        types.append("VALID")
        pos = off + HEADER
        for _ in range(RECORDS):
            if pos + 4 > len(blob):
                raise ValueError("Input file too short")
            raw, data = struct.unpack_from(">HH", blob, pos)
            pos += 4
            cmd = raw & CMD_MASK
            addr = raw & 0x07FF
            if cmd == CMD_VALID:
                records[block].append((addr, data))
            elif cmd == CMD_EMPTY:
                types[-1] = "LAST"

    end = _journal_end(types)
    words = [0xFFFF] * (ee_bytes // 2)
    start = (end + 1) % sectors
    for block in list(range(start, sectors)) + list(range(start)):
        for addr, value in records[block]:
            slot = addr - word_base
            if 0 <= slot < len(words):
                words[slot] = value
    return b"".join(struct.pack(">H", word) for word in words)


def _journal_end(types):
    n = len(types)
    rotated = list(types)
    index = 0
    while rotated[index] in ("NEW", "EMPTY"):
        rotated.append(rotated[index])
        index += 1
        if index > n:
            raise ValueError("All blocks seem to be new blocks")
    new_at = None
    ended = False
    scan_index = index
    for kind in rotated[index:]:
        if kind == "NEW":
            if new_at is None:
                new_at = scan_index
            elif ended:
                new_at = None
                break
        elif kind != "EMPTY" and new_at is not None:
            ended = True
        scan_index += 1
    if new_at is not None:
        return (new_at - 1) % n
    lasts = [i for i, kind in enumerate(types) if kind == "LAST"]
    if len(lasts) == 1:
        return lasts[0]
    run = best = 0
    best_end = n - 1
    for i, kind in enumerate(types + types):
        run = run + 1 if kind in ("EMPTY", "NEW") else 0
        if run > best:
            best = run
            best_end = i
    return (best_end - best) % n
```

```python
eeprom = replay_dflash(open("dflash.bin", "rb").read())
xf = replay_dflash(open("dflash.bin", "rb").read(), ee_bytes=2048, word_base=1024)
```

Check that the record addresses you care about fall inside `word_base` through `word_base + ee_bytes / 2` before programming. The library reports that as an error and withholds an all-`0xFF` image. This function still returns the words that did land.

## Where the pieces live

| File | Role |
| --- | --- |
| `dflash_eee/converter.py` | Sector scan, journal end, image build, `convert_bytes`, `convert_file` |
| `dflash_eee/profiles.py` | Sector constants, `eee_word_base`, `partition_is_valid`, module profiles |
| `dflash_eee/journal.py` | Pack an EEPROM image back into a journal this decoder can replay. Programming a module uses the EEPROM image and the partition, not this file |
| `dflash_eee/__init__.py` | Public exports |
| `ci/input/dflash`, `ci/input/eee` | 32 KB dumps and the 4 KB images they must produce |
