# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Read VIN, dates, part numbers and key slots from an EEPROM image.

Confirmed values come from offsets declared on the profile. Detected values
come from a pattern: a 17-character VIN with a known world manufacturer
identifier and a valid ISO 3779 check digit, or a Jaguar part-number shape
published in the repair notes. A detected value is never described as certain.

FRM3 mileage and Jaguar key slots are not in any source used here, so this
module says they are absent instead of guessing an address.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from dflash_eee.converter import InputError, convert_bytes, render_field
from dflash_eee.i18n import t
from dflash_eee.profiles import AsciiField, ModuleProfile, RegionField, profile_text

# ISO 3779 transliteration. I, O and Q are not in the VIN alphabet.
_TRANSLIT = {
    **{str(digit): digit for digit in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VIN_CHARS = frozenset(_TRANSLIT)

# Jaguar/Land Rover part numbers cited in the XF BCM repair notes, for example
# CX23-14D628-AE and DX23-14B476-BG. The shape is a pattern, not an address.
_PART_RE = re.compile(rb"[A-Z]{2}[0-9]{2}-[A-Z0-9]{6}-[A-Z]{2}")


def vin_check_digit(vin: str) -> str | None:
    """ISO 3779 check character, or None when ``vin`` is not 17 legal characters.

    Position 9 (index 8) has weight 0, so the current check character does not
    change the result. Callers compare it with ``vin[8]``.
    """

    if len(vin) != 17 or any(char not in _VIN_CHARS for char in vin):
        return None
    total = sum(_TRANSLIT[vin[index]] * _WEIGHTS[index] for index in range(17))
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def is_valid_vin(vin: str, wmis: tuple[str, ...]) -> bool:
    """True when the WMI is one this profile accepts and the check digit matches."""

    if len(vin) != 17 or vin[:3] not in wmis:
        return False
    return vin_check_digit(vin) == vin[8]


@dataclass(frozen=True)
class VinCopy:
    offset: int
    value: str
    confidence: str  # confirmed or detected


@dataclass(frozen=True)
class DecodedField:
    label: str
    value: str
    offset: int | None
    length: int | None
    confidence: str
    note: str = ""


@dataclass(frozen=True)
class KeySlotView:
    index: int
    offset: int
    state: str  # used or blank
    ident: bytes
    confidence: str


@dataclass
class ModuleInfo:
    profile_id: str
    source: str  # eeprom or dflash
    fields: list[DecodedField] = field(default_factory=list)
    vins: list[VinCopy] = field(default_factory=list)
    vin_agreement: str = "none"  # none, agree, disagree
    parts: list[DecodedField] = field(default_factory=list)
    slots: list[KeySlotView] = field(default_factory=list)
    keys_status: str = "no_map"
    keys_confidence: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def keys_used(self) -> int:
        return sum(1 for slot in self.slots if slot.state == "used")


def load_module_image(data: bytes, profile: ModuleProfile) -> tuple[bytes, str, tuple[str, ...]]:
    """Return ``(eeprom, source, warnings)``.

    A file whose length is the profile's EEPROM size is used as-is. Anything
    else is replayed as a D-Flash journal. The caller prints ``warnings``.
    """

    if profile.eee_size > 0 and len(data) == profile.eee_size:
        return data, "eeprom", ()
    if not profile.conversion_enabled:
        raise InputError(profile_text(profile, "summary"))
    result = convert_bytes(data, profile, check_size=True)
    if result.image is None:
        text = result.issues[0].text if result.issues else t("info.err.no_image")
        raise InputError(text)
    warnings: list[str] = []
    if result.errors or result.corrupt:
        warnings.append(t("info.warn.replay"))
    for issue in result.issues:
        if issue.level in ("error", "warning") and issue.text not in warnings:
            warnings.append(issue.text)
    return result.image, "dflash", tuple(warnings)


def decode_image(profile: ModuleProfile, image: bytes, source: str = "eeprom") -> ModuleInfo:
    """Decode ``image``. ``source`` is ``eeprom`` or ``dflash``."""

    if profile.eee_size > 0 and len(image) != profile.eee_size:
        raise InputError(t(
            "info.err.size",
            n=len(image),
            expected=profile.eee_size,
            title=profile_text(profile, "title"),
        ))
    info = ModuleInfo(profile_id=profile.id, source=source, keys_status=profile.keys_status)
    words = _words(image)
    confirmed_vin_at: dict[int, str] = {}
    for spec in profile.fields:
        label, value = render_field(spec, words)
        note = ""
        offset = spec.offset if isinstance(spec, AsciiField) else None
        length = spec.length if isinstance(spec, AsciiField) else None
        if isinstance(spec, AsciiField) and spec.label.upper() == "VIN":
            if is_valid_vin(value, profile.vin_wmis):
                confirmed_vin_at[spec.offset] = value
            else:
                note = t("info.vin.invalid_confirmed")
        info.fields.append(DecodedField(label, value, offset, length, "confirmed", note))
    for spec in profile.regions:
        value = _decode_region(image, spec)
        note = ""
        if spec.encoding == "ascii" and spec.label.upper() == "VIN":
            if is_valid_vin(value, profile.vin_wmis) and spec.confidence == "confirmed":
                confirmed_vin_at[spec.offset] = value
            elif spec.confidence == "confirmed":
                note = t("info.vin.invalid_confirmed")
        info.fields.append(DecodedField(
            spec.label, value, spec.offset, spec.length, spec.confidence, note,
        ))
    info.vins = _scan_vins(image, profile.vin_wmis if profile.scan_vin else (), confirmed_vin_at)
    info.vin_agreement = _agreement(info.vins)
    if profile.scan_part_numbers:
        info.parts = _scan_parts(image)
    if profile.key_layout is not None and profile.keys_status == "layout":
        info.slots = _read_slots(image, profile)
        info.keys_confidence = profile.key_layout.confidence
    return info


def format_module_info(info: ModuleInfo) -> str:
    """Plain-text reading for the window and the command line."""

    lines = [t("info.profile", id=info.profile_id), t("info.source." + info.source), ""]
    for warning in info.warnings:
        lines.append(warning)
    if info.warnings:
        lines.append("")
    lines.append(t("info.heading.vin"))
    if not info.vins:
        lines.append(t("info.vin.none"))
    for copy in info.vins:
        lines.append(t(
            "info.vin.copy",
            confidence=t("info.confidence." + copy.confidence),
            offset="%04X" % copy.offset,
            value=copy.value,
        ))
    lines.append(t("info.vin.agreement." + info.vin_agreement, n=len(info.vins)))
    lines.append(t("info.wmi_note"))
    lines.append("")
    lines.append(t("info.heading.mileage"))
    lines.append(t("info.mileage.none"))
    lines.append("")
    lines.append(t("info.heading.keys"))
    lines.extend(_key_lines(info))
    lines.append("")
    lines.append(t("info.heading.parts"))
    if not info.parts:
        lines.append(t("info.parts.none"))
    for part in info.parts:
        lines.append(t(
            "info.part.copy",
            confidence=t("info.confidence." + part.confidence),
            offset="%04X" % (part.offset or 0),
            value=part.value,
        ))
    lines.append("")
    lines.append(t("info.heading.coding"))
    lines.append(t("info.coding.none"))
    if info.fields:
        lines.append("")
        lines.append(t("info.heading.fields"))
        for item in info.fields:
            lines.append(t(
                "info.field.line",
                confidence=t("info.confidence." + item.confidence),
                label=item.label,
                value=item.value,
            ))
            if item.note:
                lines.append(item.note)
    lines.append("")
    return "\n".join(lines)


def module_info_json(info: ModuleInfo) -> dict:
    """Stable object for ``info --json`` and ``keys --json``."""

    return {
        "profile": info.profile_id,
        "source": info.source,
        "warnings": list(info.warnings),
        "vin": {
            "agreement": info.vin_agreement,
            "copies": [
                {"offset": copy.offset, "value": copy.value, "confidence": copy.confidence}
                for copy in info.vins
            ],
        },
        "mileage": {"status": "not_in_map"},
        "keys": {
            "status": info.keys_status,
            "confidence": info.keys_confidence,
            "used": info.keys_used,
            "count": len(info.slots),
            "slots": [
                {
                    "index": slot.index,
                    "offset": slot.offset,
                    "state": slot.state,
                    "id": slot.ident.hex(),
                    "confidence": slot.confidence,
                }
                for slot in info.slots
            ],
        },
        "parts": [
            {"offset": part.offset, "value": part.value, "confidence": part.confidence}
            for part in info.parts
        ],
        "coding": {"status": "not_in_map"},
        "fields": [
            {
                "label": item.label,
                "value": item.value,
                "offset": item.offset,
                "confidence": item.confidence,
                "note": item.note,
            }
            for item in info.fields
        ],
    }


def module_info_json_text(info: ModuleInfo) -> str:
    return json.dumps(module_info_json(info), indent=2, ensure_ascii=True) + "\n"


def format_keys(info: ModuleInfo) -> str:
    lines = [t("info.profile", id=info.profile_id), ""]
    lines.extend(_key_lines(info))
    lines.append("")
    return "\n".join(lines)


def _key_lines(info: ModuleInfo) -> list[str]:
    if info.keys_status == "not_applicable":
        return [t("info.keys.not_applicable")]
    if info.keys_status != "layout" or not info.slots:
        return [t("info.keys.no_map")]
    lines = [t(
        "info.keys.count",
        used=info.keys_used,
        count=len(info.slots),
        confidence=t("info.confidence." + (info.keys_confidence or "detected")),
    )]
    for slot in info.slots:
        state = t("info.keys.used") if slot.state == "used" else t("info.keys.blank")
        lines.append(t(
            "info.keys.slot",
            index=slot.index + 1,
            state=state,
            offset="%04X" % slot.offset,
            ident=slot.ident.hex(),
        ))
    return lines


def _words(image: bytes) -> list[int]:
    words = []
    padded = image if len(image) % 2 == 0 else image + b"\x00"
    for offset in range(0, len(padded), 2):
        words.append((padded[offset] << 8) | padded[offset + 1])
    return words


def _decode_region(image: bytes, spec: RegionField) -> str:
    chunk = image[spec.offset:spec.offset + spec.length]
    if spec.encoding == "ascii":
        return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
    if spec.encoding == "hex":
        return chunk.hex()
    if spec.encoding == "uint-be":
        return str(int.from_bytes(chunk, "big"))
    if spec.encoding == "uint-le":
        return str(int.from_bytes(chunk, "little"))
    return chunk.hex()


def _scan_vins(image: bytes, wmis: tuple[str, ...], confirmed_at: dict[int, str]) -> list[VinCopy]:
    if not wmis and not confirmed_at:
        return []
    found: list[VinCopy] = []
    limit = len(image) - 16
    offset = 0
    while offset < limit:
        if offset in confirmed_at:
            found.append(VinCopy(offset, confirmed_at[offset], "confirmed"))
            offset += 17
            continue
        if wmis:
            raw = image[offset:offset + 17]
            try:
                text = raw.decode("ascii")
            except UnicodeError:
                offset += 1
                continue
            if is_valid_vin(text, wmis):
                found.append(VinCopy(offset, text, "detected"))
                offset += 17
                continue
        offset += 1
    for offset, value in sorted(confirmed_at.items()):
        if not any(copy.offset == offset for copy in found):
            found.append(VinCopy(offset, value, "confirmed"))
    found.sort(key=lambda copy: copy.offset)
    return found


def _agreement(copies: list[VinCopy]) -> str:
    if not copies:
        return "none"
    values = {copy.value for copy in copies}
    if len(values) == 1:
        return "agree"
    return "disagree"


def _scan_parts(image: bytes) -> list[DecodedField]:
    found = []
    for match in _PART_RE.finditer(image):
        start = match.start()
        before = image[start - 1:start] if start else b""
        after = image[match.end():match.end() + 1]
        if before.isalnum() or after.isalnum():
            continue
        text = match.group().decode("ascii")
        found.append(DecodedField(t("info.part.label"), text, start, len(text), "detected"))
    return found


def _read_slots(image: bytes, profile: ModuleProfile) -> list[KeySlotView]:
    layout = profile.key_layout
    assert layout is not None
    slots = []
    for index in range(layout.count):
        origin = layout.base + index * layout.stride
        start = origin + layout.id_offset
        ident = bytes(image[start:start + layout.id_length])
        state = "blank" if ident == layout.blank else "used"
        slots.append(KeySlotView(index, start, state, ident, layout.confidence))
    return slots
