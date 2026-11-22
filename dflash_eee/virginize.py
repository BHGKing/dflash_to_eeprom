# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Build a new EEPROM image with identity bytes cleared.

Jaguar XF BCM: every detected VIN (valid WMI and ISO 3779 check digit) is
replaced with 0xFF. Part numbers, key bytes and every other byte stay. No
checksum algorithm for that EEPROM is published, so none is rewritten unless
the profile itself declares one that covers a cleared byte.

BMW FRM3: not implemented. The footwell module is not an immobilizer. Public
notes give the VIN at 0xFD3 and say this tool does not clear mileage or
checksums. A programmer script offers "Do Virgin" without publishing the
bytes. Guessing them is refused.

The caller writes the bytes to a new path. This module does not open files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dflash_eee.decode import ModuleInfo, decode_image
from dflash_eee.i18n import t
from dflash_eee.profiles import ChecksumSpec, ModuleProfile, profile_text


@dataclass(frozen=True)
class ByteChange:
    offset: int
    old: bytes
    new: bytes
    reason: str


@dataclass(frozen=True)
class VirginizePlan:
    supported: bool
    image: bytes | None
    changes: tuple[ByteChange, ...]
    notes: tuple[str, ...]
    reason: str

    @property
    def ready(self) -> bool:
        return self.supported and self.image is not None and len(self.changes) > 0


def virginize_image(profile: ModuleProfile, image: bytes) -> VirginizePlan:
    """Plan the byte changes. ``image`` is an EEPROM image, not a D-Flash dump."""

    if profile.virginize_mode == "unsupported":
        return VirginizePlan(False, None, (), (), _unsupported_reason(profile))
    if profile.eee_size <= 0 or len(image) != profile.eee_size:
        return VirginizePlan(False, None, (), (), t(
            "virginize.err.size",
            n=len(image),
            expected=profile.eee_size,
            title=profile_text(profile, "title"),
        ))
    info = decode_image(profile, image, source="eeprom")
    buf = bytearray(image)
    changes: list[ByteChange] = []

    def wipe(start: int, length: int, reason: str, replacement: bytes | None = None) -> None:
        if length <= 0 or start < 0 or start + length > len(buf):
            return
        old = bytes(buf[start:start + length])
        new = replacement if replacement is not None else bytes([0xFF]) * length
        if len(new) != length or old == new:
            return
        buf[start:start + length] = new
        changes.append(ByteChange(start, old, new, reason))

    if profile.virginize_mode in ("clear_vin", "clear_vin_and_keys"):
        for copy in info.vins:
            wipe(copy.offset, 17, t("virginize.reason.vin", offset="%04X" % copy.offset, confidence=t("info.confidence." + copy.confidence)))
        for spec in profile.regions:
            if spec.clear_on_virgin:
                wipe(spec.offset, spec.length, t("virginize.reason.region", label=spec.label))
    if profile.virginize_mode == "clear_vin_and_keys" and profile.key_layout is not None:
        layout = profile.key_layout
        for slot in info.slots:
            if slot.state != "used":
                continue
            wipe(
                slot.offset,
                layout.id_length,
                t("virginize.reason.key", index=slot.index + 1),
                layout.blank,
            )
    _fix_checksums(profile, buf, changes)
    if not changes:
        return VirginizePlan(True, None, (), _notes(profile, info, ()), t("virginize.no_changes"))
    return VirginizePlan(True, bytes(buf), tuple(changes), _notes(profile, info, tuple(changes)), "")


def format_virginize_plan(profile: ModuleProfile, plan: VirginizePlan) -> str:
    """Text for the confirmation dialog, the terminal and the report header."""

    lines = [profile_text(profile, "title"), ""]
    if not plan.supported or plan.image is None:
        lines.append(plan.reason)
        for note in plan.notes:
            lines.append(note)
        lines.append("")
        return "\n".join(lines)
    lines.append(t("virginize.intro", n=len(plan.changes)))
    lines.append("")
    for change in plan.changes:
        lines.append(t(
            "virginize.change",
            offset="%04X" % change.offset,
            n=len(change.new),
            reason=change.reason,
        ))
        lines.append(t("virginize.old", hex=_hex(change.old)))
        lines.append(t("virginize.new", hex=_hex(change.new)))
    lines.append("")
    for note in plan.notes:
        lines.append(note)
    lines.append("")
    return "\n".join(lines)


def output_path_problem(source: Path, dest: Path) -> str | None:
    """``same`` or ``exists`` when ``dest`` must not be written, else None."""

    try:
        same = source.resolve() == dest.resolve()
    except OSError:
        same = source.absolute() == dest.absolute()
    if same:
        return "same"
    if dest.exists():
        return "exists"
    return None


def _unsupported_reason(profile: ModuleProfile) -> str:
    if profile.id == "bmw_frm3":
        return t("virginize.unsupported.frm3")
    if profile.id == "jlr_kvm":
        return t("virginize.unsupported.kvm")
    return t("virginize.unsupported.generic", title=profile_text(profile, "title"))


def _notes(profile: ModuleProfile, info: ModuleInfo, changes: tuple[ByteChange, ...]) -> tuple[str, ...]:
    notes = []
    checksum_changes = [change for change in changes if change.reason == t("virginize.reason.checksum")]
    if checksum_changes:
        notes.append(t("virginize.note.checksum_updated", n=len(checksum_changes)))
    else:
        notes.append(t("virginize.note.checksum_none"))
    if profile.keys_status == "not_applicable":
        notes.append(t("info.keys.not_applicable"))
    elif profile.virginize_mode != "clear_vin_and_keys":
        notes.append(t("virginize.note.keys_kept"))
    if info.parts:
        notes.append(t("virginize.note.parts_kept"))
    if profile.id == "jlr_xf_bcm":
        notes.append(t("virginize.note.jlr"))
    notes.append(t("virginize.note.write_eeprom"))
    notes.append(t("journal.note"))
    return tuple(notes)


def _fix_checksums(profile: ModuleProfile, buf: bytearray, changes: list[ByteChange]) -> None:
    if not changes:
        return
    touched = set()
    for change in changes:
        touched.update(range(change.offset, change.offset + len(change.new)))
    specs: list[tuple[int, ChecksumSpec]] = []
    for region in profile.regions:
        if region.checksum is not None and not region.checksum.relative:
            specs.append((0, region.checksum))
    if profile.virginize_mode == "clear_vin_and_keys" and profile.key_layout is not None:
        layout = profile.key_layout
        if layout.checksum is not None and layout.checksum.relative:
            for index in range(layout.count):
                specs.append((layout.base + index * layout.stride, layout.checksum))
    for origin, spec in specs:
        start = origin + spec.start
        span = range(start, start + spec.length)
        if not (set(span) & touched):
            continue
        digest = _digest(bytes(buf[start:start + spec.length]), spec.algorithm)
        stored = origin + spec.offset
        old = bytes(buf[stored:stored + len(digest)])
        if old == digest:
            continue
        buf[stored:stored + len(digest)] = digest
        changes.append(ByteChange(stored, old, digest, t("virginize.reason.checksum")))
        touched.update(range(stored, stored + len(digest)))


def _digest(data: bytes, algorithm: str) -> bytes:
    if algorithm == "sum8":
        return bytes([sum(data) & 0xFF])
    if algorithm == "xor8":
        value = 0
        for byte in data:
            value ^= byte
        return bytes([value])
    if algorithm == "sum16be":
        value = sum(data) & 0xFFFF
        return bytes([(value >> 8) & 0xFF, value & 0xFF])
    raise ValueError(algorithm)


def _hex(data: bytes) -> str:
    return " ".join("%02X" % byte for byte in data)
