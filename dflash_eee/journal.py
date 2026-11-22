# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Build an S12XE D-Flash journal that this tool can replay.

AN3490 fixes the geometry: 256-byte sectors, a two-word header, 63 records
of a 16-bit address and a 16-bit data word. The tag values written here are
the ones the decoder already accepts from FRM dumps, not numbers printed in
AN3490: data sector ``0xFACF``, programmed record ``0xB800``, empty record
``0xF800``. The second header word is ``0xFFFE``, the value on almost every
data sector in those dumps. The decoder does not interpret that word.

The address is the word index in the 4 KB buffer. A 2 KB profile starts at
word 1024, the same window the replay uses.

This journal round-trips through the decoder. It is not the repair procedure.
Programmers write the EEPROM image into the EEE partition after setting
DFPART and ERPART. Writing this file over raw D-Flash has not been proven on
a car, and the silicon's own sector state machine may expect more than these
tags.
"""

from __future__ import annotations

import struct

from dflash_eee.i18n import t
from dflash_eee.profiles import RECORDS_PER_SECTOR, ModuleProfile, ProfileError

_HEADER_MARK = 0xFACF
_HEADER_SECOND = 0xFFFE
_CMD_VALID = 0xB800
_ADDR_LIMIT = 0x7FF


class JournalError(ProfileError):
    """The EEPROM image cannot be packed into a journal."""


def encode_journal(image: bytes, profile: ModuleProfile) -> bytes:
    """Pack ``image`` into a D-Flash journal of ``profile.dflash_size`` bytes."""

    if profile.eee_size <= 0 or len(image) != profile.eee_size or len(image) % 2:
        raise JournalError(t(
            "journal.err.size",
            n=len(image),
            expected=profile.eee_size,
            title=profile.title,
        ))
    if not profile.conversion_enabled:
        raise JournalError(profile.summary)
    base = profile.eee_word_base
    words = [struct.unpack_from(">H", image, offset)[0] for offset in range(0, len(image), 2)]
    records = []
    for index, word in enumerate(words):
        addr = base + index
        if addr > _ADDR_LIMIT:
            raise JournalError(t("journal.err.address", addr=addr))
        records.append((_CMD_VALID | addr, word))
    per = RECORDS_PER_SECTOR
    chunks = [records[start:start + per] for start in range(0, len(records), per)]
    if chunks and len(chunks[-1]) == per:
        # A full sector has no empty record, so it would not be marked LAST.
        # One following data sector with only empty records supplies that mark.
        chunks.append([])
    sectors = profile.dflash_size // profile.layout.sector_size
    if len(chunks) > sectors:
        raise JournalError(t("journal.err.full", id=profile.id))
    out = bytearray(b"\xFF" * profile.dflash_size)
    for index, chunk in enumerate(chunks):
        origin = index * profile.layout.sector_size
        struct.pack_into(">HH", out, origin, _HEADER_MARK, _HEADER_SECOND)
        for record_index, (command, data) in enumerate(chunk):
            struct.pack_into(
                ">HH",
                out,
                origin + profile.layout.header_size + record_index * profile.layout.record_size,
                command,
                data,
            )
    return bytes(out)
