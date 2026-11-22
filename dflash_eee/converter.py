# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""S12XE D-Flash journal replay.

The control flow is the 2017 FRM3 decoder, parameterized by a module profile.
Quirks that the sample dumps depend on are left in place: an empty record
marks the sector as the latest one but does not stop the scan of that sector,
and a run of ready sectors is trusted over a single half-full sector when
both are present.

What AN3490 contributes is the geometry, not a second algorithm. Each sector
is 256 bytes, the header is two words, and each data record is an address
word plus a data word, 63 records to a sector. Ready sectors are kept erased.
The address word's top bits are the record state (empty ``0xF800``, programmed
``0xB800`` in the dumps this tool was checked against) and the low 11 bits
are the EEE word address, which covers the 4 KB maximum.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

from dflash_eee.profiles import (
    AsciiField,
    DateField,
    Field as FieldSpec,
    ModuleProfile,
    PartField,
    ProfileError,
    get_profile,
)


class InputError(Exception):
    """The dump cannot be replayed. ``args[0]`` stays a short sentence."""


@dataclass
class Issue:
    level: str  # error, warning, info
    text: str


@dataclass
class ConversionResult:
    image: bytes | None
    profile: ModuleProfile
    issues: list[Issue] = field(default_factory=list)
    fields: list[tuple[str, str]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    corrupt: bool = False
    input_size: int = 0
    endblock: int | None = None
    endblock_reason: str = ""

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return self.image is not None and not self.errors


def _byte_from_words(words: list[int], addr: int) -> int:
    if addr % 2 == 1:
        return words[addr // 2] & 0xFF
    return words[addr // 2] >> 8


def render_field(spec: FieldSpec, words: list[int]) -> tuple[str, str]:
    """Return ``(label, value)`` for one decoded field."""

    def get_byte(addr: int) -> int:
        return _byte_from_words(words, addr)

    if isinstance(spec, AsciiField):
        chars = "".join(chr(get_byte(spec.offset + index)) for index in range(spec.length))
        return spec.label, chars
    if isinstance(spec, DateField):
        day = "%02x" % get_byte(spec.day)
        month = "%02x" % get_byte(spec.month)
        year = "".join("%02x" % get_byte(offset) for offset in spec.year_offsets)
        return spec.label, "%s.%s.%s" % (day, month, year)
    if isinstance(spec, PartField):
        value = 0
        for index in range(spec.length):
            value = (value << 8) + get_byte(spec.offset + index)
        # log_format includes the label text; the parenthetical is the value's caption.
        rendered = spec.log_format % value
        caption = rendered.split(": ", 1)[-1]
        return spec.label, caption
    raise ProfileError("Cannot render field %r" % spec)


def render_info(profile: ModuleProfile, words: list[int], corrupt: bool) -> str:
    """Multi-line summary logged after a successful replay.

    For the FRM3 field set this is byte-for-byte the historical message,
    including the leading blank lines. Other profiles only report corruption
    and the profile name, so an unverified module does not inherit the FRM3 map.
    """

    result = "\n\n"
    if corrupt:
        result += "Corrupt D-Flash file detected! Results probably incorrect!\n"
    if profile.fields:
        for spec in profile.fields:
            label, value = render_field(spec, words)
            if isinstance(spec, AsciiField):
                result += "%s: %s\n" % (label, value)
            elif isinstance(spec, DateField):
                result += "%s: %s\n" % (label, value)
            elif isinstance(spec, PartField):
                result += (spec.log_format % _part_value(spec, words)) + "\n"
        return result
    result += "Profile: %s\n" % profile.title
    result += "EEE image: %d bytes\n" % (len(words) * 2)
    return result


def _part_value(spec: PartField, words: list[int]) -> int:
    value = 0
    for index in range(spec.length):
        value = (value << 8) + _byte_from_words(words, spec.offset + index)
    return value


class DFlashConverter(object):
    """Replay one D-Flash dump into an EEE image.

    Class attributes are the historical FRM3 geometry. A profile replaces
    them on the instance. Calling :meth:`convert` still logs the same
    sentences the 2017 script logged for that geometry.
    """

    NB_BLOCKS = 128
    BLOCKSIZE = 256
    HEADERSIZE = 4
    CMDSIZE = 4
    EESIZE = 2048  # words, not bytes

    BLOCK_VALID = 0xFACF
    BLOCK_EMPTY = 0xFFFF
    BLOCK_EMPTY_CLEARED = 0xFFFF
    CMD_MASK = 0xF800
    CMD_VALID = 0xB800
    CMD_EMPTY = 0xF800

    VALID = "VALID"
    LAST = "LAST"
    EMPTY = "EMPTY"
    NEW = "NEW"
    INVALID = "INVALID"

    def __init__(self, profile: ModuleProfile | None = None):
        self.profile = profile if profile is not None else get_profile("bmw_frm3")
        if not self.profile.conversion_enabled:
            raise ProfileError(self.profile.summary)
        layout = self.profile.layout
        self.NB_BLOCKS = self.profile.scan_sectors
        self.BLOCKSIZE = layout.sector_size
        self.HEADERSIZE = layout.header_size
        self.CMDSIZE = layout.record_size
        self.EESIZE = self.profile.ee_words
        self.BLOCK_VALID = layout.block_valid
        self.BLOCK_EMPTY = layout.block_empty
        self.BLOCK_EMPTY_CLEARED = layout.block_empty
        self.CMD_MASK = layout.cmd_mask
        self.CMD_VALID = layout.cmd_valid
        self.CMD_EMPTY = layout.cmd_empty
        self.cmds_per_block = (self.BLOCKSIZE - self.HEADERSIZE) // self.CMDSIZE
        self._reset()

    def _reset(self) -> None:
        self.block_types: list[str] = []
        self.block_data: list[list[tuple[int, int]]] = [[] for _ in range(self.NB_BLOCKS)]
        self.endblock = None
        self.endblock_reason = ""
        self.corrupt = False
        self.data: list[int] = []
        self.messages: list[tuple[str, str]] = []
        self.out_of_range = 0

    def _emit(self, level: str, message: str) -> None:
        self.messages.append((level, message))
        getattr(logging, level)(message)

    def _read_file(self, filename: str) -> None:
        with open(filename, "rb") as infile:
            self._read_stream(infile)

    def _read_bytes(self, blob: bytes) -> None:
        # BytesIO would also work; a tiny seekable wrapper keeps one parser.
        from io import BytesIO
        self._read_stream(BytesIO(blob))

    def _read_stream(self, infile) -> None:
        """Read ``scan_sectors`` sectors starting at ``scan_offset``.

        The exception text "Input file too short" is part of the historical
        contract: short dumps in ci/input/logs are checked for it.
        """

        self._reset()
        self.corrupt = False
        origin = self.profile.scan_offset
        for blockid in range(self.NB_BLOCKS):
            infile.seek(origin + blockid * self.BLOCKSIZE)
            blockheader = infile.read(self.HEADERSIZE)
            if len(blockheader) != self.HEADERSIZE:
                raise Exception("Input file too short")
            header = struct.unpack(">HH", blockheader)
            if header[0] == self.BLOCK_EMPTY:
                if header[1] == self.BLOCK_EMPTY_CLEARED:
                    self.block_types.append(self.EMPTY)
                else:
                    # Block has already been prepared (FFFFFFFE in the FRM dumps).
                    self.block_types.append(self.NEW)
            elif header[0] == self.BLOCK_VALID:
                logging.debug("Data block id: %d, type: 0x%04X" % (blockid, header[1]))
                self.block_types.append(self.VALID)
                for _blockitem in range(self.cmds_per_block):
                    cmdread = infile.read(self.CMDSIZE)
                    if len(cmdread) != self.CMDSIZE:
                        raise Exception("Input file too short")
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
                        self._emit("error", "Unknown CMD type detected: 0x%02X" % cmd)
                        self.corrupt = True
            else:
                if self.profile.unknown_sector == "warn":
                    self._emit("warning", "Unknown block type: 0x%04X" % header[0])
                else:
                    self._emit("error", "Unknown block type: 0x%04X" % header[0])
                    self.corrupt = True
                self.block_types.append(self.INVALID)

    def _find_new_blocks(self):
        block_types = list(self.block_types)
        curr_block = 0
        while block_types[curr_block] == self.NEW or block_types[curr_block] == self.EMPTY:
            block_types.append(block_types[curr_block])
            curr_block += 1
            if curr_block > self.NB_BLOCKS:
                raise Exception("All blocks seem to be new blocks")

        newblock = None
        ended = False
        for curr_block in range(curr_block, len(block_types)):
            if block_types[curr_block] == self.NEW:
                if newblock is None:
                    newblock = curr_block
                elif ended:
                    self._emit("warning", "Found more than one set of new blocks!")
                    return None
            elif block_types[curr_block] != self.EMPTY and newblock is not None:
                ended = True
        if newblock is None:
            self._emit("warning", "No new blocks found!")
            return None
        return (newblock - 1) % self.NB_BLOCKS

    def _find_last_block(self):
        count = self.block_types.count(self.LAST)
        if count == 1:
            return self.block_types.index(self.LAST)
        if count == 0:
            self._emit("warning", "No last block found!")
            return None
        self._emit("warning", "More than one 'last' block found!")
        return None

    def _find_longest_empty(self):
        now = 0
        longest = 0
        longest_block = None
        for blockid, block in enumerate(self.block_types + self.block_types):
            if block == self.EMPTY or block == self.NEW:
                now += 1
            else:
                now = 0
            if now > longest:
                longest = now
                longest_block = blockid
        if longest_block is None:
            return self.NB_BLOCKS - 1
        return (longest_block - longest) % self.NB_BLOCKS

    def _find_endblock(self) -> None:
        endblock_new = self._find_new_blocks()
        endblock_last = self._find_last_block()

        if endblock_new is None:
            if endblock_last is None:
                self._emit("warning", "No last or new blocks, using longest chain of empty blocks!")
                self.endblock = self._find_longest_empty()
                self.endblock_reason = "longest_empty"
            else:
                self.endblock = endblock_last
                self.endblock_reason = "last_block"
        else:
            if endblock_last != endblock_new and endblock_last is not None:
                block_types = self.block_types + self.block_types
                endblock_new_wrapped = endblock_new
                if endblock_new_wrapped < endblock_last:
                    endblock_new_wrapped += len(self.block_types)
                empty_run = block_types[endblock_last + 1:endblock_new_wrapped + 1]
                if empty_run.count(self.EMPTY) != endblock_new_wrapped - endblock_last:
                    self._emit("warning", "Inconsistency detected, last used block not followed by new blocks!")
                    self._emit("warning", "Using new blocks as reference!")
            self.endblock = endblock_new
            self.endblock_reason = "new_blocks"

    def _build_image(self) -> None:
        startblock = (self.endblock + 1) % self.NB_BLOCKS
        data = [0xFFFF] * self.EESIZE
        order = list(range(startblock, self.NB_BLOCKS)) + list(range(0, startblock))
        for block in order:
            for item in self.block_data[block]:
                addr = item[0]
                if addr >= self.EESIZE:
                    self.out_of_range += 1
                    self.corrupt = True
                    self._emit(
                        "error",
                        "Record address 0x%04X is outside the %d-byte EEE" % (addr, self.EESIZE * 2),
                    )
                    continue
                data[addr] = item[1]
        self.data = data

    def _image_bytes(self) -> bytes:
        return b"".join(struct.pack(">H", word) for word in self.data)

    def _save_file(self, filename: str) -> None:
        self._build_image()
        with open(filename, "wb") as outfile:
            outfile.write(self._image_bytes())

    def _get_byte(self, addr: int) -> int:
        return _byte_from_words(self.data, addr)

    def _get_info(self) -> str:
        return render_info(self.profile, self.data, self.corrupt)

    def convert(self, dflash_filename: str, ee_filename: str) -> None:
        """File in, file out. Logs the historical summary on the root logger."""

        self._read_file(dflash_filename)
        self._find_endblock()
        self._save_file(ee_filename)
        logging.info(self._get_info())

    def to_result(self, input_size: int, check_size: bool) -> ConversionResult:
        image = self._image_bytes()
        issues = [Issue(level, text) for level, text in self.messages]
        if check_size and input_size != self.profile.dflash_size:
            if input_size < self.profile.scan_offset + self.profile.scan_sectors * self.BLOCKSIZE:
                # Already failed earlier. Kept for callers that still ask.
                pass
            else:
                issues.insert(
                    0,
                    Issue(
                        "warning",
                        "Wrong size: %d bytes. %s expects %d. Extra bytes are ignored."
                        % (input_size, self.profile.title, self.profile.dflash_size),
                    ),
                )
        validish = self.block_types.count(self.VALID) + self.block_types.count(self.LAST)
        if validish == 0:
            issues.append(Issue(
                "error",
                "Unrecognized D-Flash layout: no sector header 0x%04X." % self.BLOCK_VALID,
            ))
        if self.out_of_range:
            issues.append(Issue(
                "error",
                "%d record(s) pointed past the %d-byte EEE and were not written."
                % (self.out_of_range, self.profile.eee_size),
            ))
        fields = []
        if self.profile.fields and self.data:
            fields = [render_field(spec, self.data) for spec in self.profile.fields]
        reason = {
            "new_blocks": "Ready sectors mark the end of the journal.",
            "last_block": "The half-full sector marks the end of the journal.",
            "longest_empty": "No ready or half-full sector; used the longest erased run.",
        }.get(self.endblock_reason, self.endblock_reason)
        stats = {
            "sectors": self.NB_BLOCKS,
            "valid": self.block_types.count(self.VALID),
            "last": self.block_types.count(self.LAST),
            "empty": self.block_types.count(self.EMPTY),
            "new": self.block_types.count(self.NEW),
            "invalid": self.block_types.count(self.INVALID),
            "records": sum(len(items) for items in self.block_data),
            "endblock": self.endblock,
            "endblock_reason": reason,
            "eee_bytes": len(image),
            "out_of_range": self.out_of_range,
        }
        return ConversionResult(
            image=image,
            profile=self.profile,
            issues=issues,
            fields=fields,
            stats=stats,
            corrupt=bool(self.corrupt) or validish == 0 or self.out_of_range > 0,
            input_size=input_size,
            endblock=self.endblock,
            endblock_reason=self.endblock_reason,
        )


def convert_bytes(data: bytes, profile: ModuleProfile | None = None, check_size: bool = True) -> ConversionResult:
    """Replay a dump already in memory. Never raises for a short or odd file.

    A short dump, an unsupported profile and a journal that does not parse
    come back as a result with ``image is None`` or with error issues, so a
    GUI can show the sentence instead of a traceback.
    """

    profile = profile if profile is not None else get_profile("bmw_frm3")
    if not profile.conversion_enabled:
        return ConversionResult(
            image=None,
            profile=profile,
            issues=[Issue("error", profile.summary)],
            input_size=len(data),
            corrupt=True,
        )
    needed = profile.scan_offset + profile.scan_sectors * profile.layout.sector_size
    if len(data) < needed:
        return ConversionResult(
            image=None,
            profile=profile,
            issues=[Issue(
                "error",
                "File is %d bytes. %s needs %d bytes of D-Flash (this dump is short)."
                % (len(data), profile.title, profile.dflash_size),
            )],
            input_size=len(data),
            corrupt=True,
        )
    converter = DFlashConverter(profile)
    try:
        converter._read_bytes(data)
        converter._find_endblock()
        converter._build_image()
    except Exception as exc:
        return ConversionResult(
            image=None,
            profile=profile,
            issues=[Issue("error", str(exc))],
            input_size=len(data),
            corrupt=True,
        )
    return converter.to_result(len(data), check_size=check_size)


def convert_file(source: str, destination: str, profile: ModuleProfile | None = None) -> ConversionResult:
    """Replay ``source`` and write the image to ``destination`` when one exists."""

    with open(source, "rb") as handle:
        data = handle.read()
    result = convert_bytes(data, profile=profile, check_size=True)
    if result.image is not None:
        with open(destination, "wb") as handle:
            handle.write(result.image)
    return result
