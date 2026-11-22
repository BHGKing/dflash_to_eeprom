# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Module profiles for the S12XE D-Flash to EEE replay.

A profile describes one ECU's published memory geometry. It does not describe
a private memory map. Field offsets are included only for the original FRM3
decoder, whose locations are exercised by the sample dumps in ``ci/``.

Adding a module
---------------
Built-in profiles are :class:`ModuleProfile` entries in ``BUILTIN``. Drop-in
profiles are JSON files in a ``profiles`` directory next to the working
directory or, for the frozen Windows executable, next to the ``.exe``. A JSON
profile is never treated as supported: if the file asks for that status it is
loaded as experimental. Supported means the geometry was checked against the
sources cited on the built-in entry.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# S12XE hardware EEE, from Freescale AN3490 and the record encoding used by
# the original FRM3 decoder (header word 0xFACF, 63 records of address+data).
# AN3490: 256-byte erase sector, two-word sector header, 63 data records per
# sector, each record a 16-bit EEE address label and a 16-bit data word.
SECTOR_SIZE = 256
HEADER_SIZE = 4
RECORD_SIZE = 4
RECORDS_PER_SECTOR = (SECTOR_SIZE - HEADER_SIZE) // RECORD_SIZE  # 63

BLOCK_VALID = 0xFACF
BLOCK_EMPTY = 0xFFFF
CMD_MASK = 0xF800
CMD_VALID = 0xB800
CMD_EMPTY = 0xF800

DFLASH_BYTES = 32 * 1024
DFLASH_SECTORS = DFLASH_BYTES // SECTOR_SIZE  # 128

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ProfileError(Exception):
    """The selected profile cannot be used for a conversion."""


@dataclass(frozen=True)
class EeeLayout:
    """Record encoding of one EEE implementation.

    Every built-in profile uses :data:`S12XE_LAYOUT`. A different layout
    belongs here only when a public source describes its sector header and
    record words. Do not invent one.
    """

    name: str = "s12xe-an3490"
    sector_size: int = SECTOR_SIZE
    header_size: int = HEADER_SIZE
    record_size: int = RECORD_SIZE
    block_valid: int = BLOCK_VALID
    block_empty: int = BLOCK_EMPTY
    cmd_mask: int = CMD_MASK
    cmd_valid: int = CMD_VALID
    cmd_empty: int = CMD_EMPTY

    @property
    def records_per_sector(self) -> int:
        return (self.sector_size - self.header_size) // self.record_size


S12XE_LAYOUT = EeeLayout()


@dataclass(frozen=True)
class AsciiField:
    label: str
    offset: int
    length: int
    kind: str = "ascii"


@dataclass(frozen=True)
class DateField:
    """BCD-style date stored as separate bytes, printed with ``%02x``.

    The byte order is the one in the original FRM3 tool, not a guess at a
    generic calendar structure. ``year_offsets`` are concatenated in order.
    """

    label: str
    day: int
    month: int
    year_offsets: tuple[int, ...]
    kind: str = "date"


@dataclass(frozen=True)
class PartField:
    label: str
    offset: int
    length: int
    log_format: str
    kind: str = "part"


Field = AsciiField | DateField | PartField


@dataclass(frozen=True)
class ModuleProfile:
    id: str
    title: str
    menu_label: str
    manufacturer: str
    status: str  # supported, experimental, unsupported
    mcu: str
    vehicles: str
    dflash_size: int
    eee_size: int
    dfpart_sectors: int | None
    erpart_pages: int | None
    scan_sectors: int
    scan_offset: int
    partition_confirmed: bool
    unknown_sector: str  # "error" or "warn"
    summary: str
    write_notes: str
    assumptions: str
    sources: tuple[str, ...]
    fields: tuple[Field, ...] = ()
    layout: EeeLayout = field(default_factory=lambda: S12XE_LAYOUT)
    conversion_enabled: bool = True
    external: bool = False

    @property
    def supported(self) -> bool:
        return self.status == "supported"

    @property
    def ee_words(self) -> int:
        return self.eee_size // 2

    @property
    def status_label(self) -> str:
        return {
            "supported": "Supported",
            "experimental": "Experimental",
            "unsupported": "Not supported",
        }[self.status]

    def partition_text(self) -> str:
        if self.dfpart_sectors is None or self.erpart_pages is None:
            return "Partition: not stated by the sources used here."
        user = self.dfpart_sectors * SECTOR_SIZE
        eee = self.erpart_pages * SECTOR_SIZE
        prefix = "Partition" if self.partition_confirmed else "Partition (not confirmed)"
        return (
            "%s: user D-Flash %d sectors (%d bytes), EEE %d pages (%d bytes)."
            % (prefix, self.dfpart_sectors, user, self.erpart_pages, eee)
        )


def partition_is_valid(dfpart: int, erpart: int, total_sectors: int = DFLASH_SECTORS) -> bool:
    """AN3490 partition rule for a 128-sector S12XE D-Flash.

    ``(128 - DFPART) / ERPART`` must be at least 8. When ``ERPART`` is 1,
    at least 12 D-Flash sectors must remain for EEE. ``ERPART`` 0 is the
    documented "no EEE" partition and requires every sector to be user D-Flash.
    """

    if dfpart < 0 or erpart < 0 or dfpart > total_sectors:
        return False
    if erpart == 0:
        return dfpart == total_sectors
    eee_sectors = total_sectors - dfpart
    if eee_sectors / erpart < 8:
        return False
    if erpart == 1 and eee_sectors < 12:
        return False
    return True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _check_profile(profile: ModuleProfile) -> None:
    _require(profile.status in ("supported", "experimental", "unsupported"), profile.id + " status")
    _require(bool(_ID_RE.match(profile.id)), "Profile id %r is not a lowercase identifier" % profile.id)
    _require(profile.unknown_sector in ("error", "warn"), profile.id + " unknown_sector")
    _require(len(profile.sources) > 0, profile.id + " needs at least one source")
    _require(profile.layout.records_per_sector == RECORDS_PER_SECTOR or profile.layout is not S12XE_LAYOUT,
             profile.id)
    if profile.layout.sector_size <= profile.layout.header_size:
        raise ProfileError(profile.id + " layout header is larger than the sector")
    if (profile.layout.sector_size - profile.layout.header_size) % profile.layout.record_size:
        raise ProfileError(profile.id + " records do not fill a sector")

    if profile.status == "supported":
        _require(profile.conversion_enabled, profile.id + " is supported but does not convert")
        _require(not profile.assumptions.strip(), profile.id + " is supported but still lists assumptions")
        _require(not profile.external, profile.id + " cannot be both external and supported")
        _require(profile.partition_confirmed, profile.id + " partition is not confirmed")
    if profile.status == "experimental":
        _require(profile.assumptions.strip() != "", profile.id + " is experimental but has no assumption text")
    if profile.status == "unsupported":
        _require(not profile.conversion_enabled, profile.id + " is unsupported but still converts")

    if profile.dfpart_sectors is not None and profile.erpart_pages is not None:
        _require(
            partition_is_valid(profile.dfpart_sectors, profile.erpart_pages),
            "%s partition DFPART=%s ERPART=%s violates the AN3490 size rule"
            % (profile.id, profile.dfpart_sectors, profile.erpart_pages),
        )
        if profile.conversion_enabled and profile.partition_confirmed:
            _require(
                profile.eee_size == profile.erpart_pages * SECTOR_SIZE,
                profile.id + " EEE size does not match ERPART x 256",
            )

    if not profile.conversion_enabled:
        return

    _require(profile.dflash_size > 0, profile.id)
    _require(profile.eee_size > 0 and profile.eee_size % 2 == 0, profile.id + " EEE size")
    _require(profile.scan_sectors > 0, profile.id)
    window = profile.scan_offset + profile.scan_sectors * profile.layout.sector_size
    _require(window <= profile.dflash_size, profile.id + " scan window is larger than the D-Flash")
    _require(profile.ee_words > 0, profile.id)


# Field offsets below are the original FRM3 decoder (van Leeuwen, 2017).
# They are printed from the rebuilt image of every sample in ci/input and the
# golden logs record the same strings. They are not reused for other modules.
_FRM3_FIELDS: tuple[Field, ...] = (
    AsciiField("VIN", 0xFD3, 17),
    DateField("Production date", day=0xFBE, month=0xFBD, year_offsets=(0xFBB, 0xFBC)),
    DateField("Programming date", day=0xF89, month=0xF88, year_offsets=(0xF86, 0xF87)),
    PartField("HW-NR", 0xF97, 6, "HW-NR: %07x (Hardware part number)"),
    PartField("SW-NR", 0xF8A, 6, "SW-NR: %07x (Updated part number)"),
    PartField("ZB-NR", 0xF65, 6, "ZB-NR: %07x (Original part number)"),
    PartField("S", 0xFBF, 6, "S:     %07x (Original part number)"),
)


BUILTIN: tuple[ModuleProfile, ...] = (
    ModuleProfile(
        id="bmw_frm3",
        title="BMW / Mini FRM3",
        menu_label="BMW / Mini - FRM3 footwell (supported)",
        manufacturer="BMW",
        status="supported",
        mcu="MC9S12XEQ384",
        vehicles=(
            "BMW and Mini FRM3 / FRM3R footwell modules whose programmer script "
            "is the E-series MC9S12XEQ384 (VVDI: FRM-E-SERIES-XEQ384). "
            "A published walk-through uses an E71. If the programmer identifies "
            "the part as F-series MC9S12XET512, use the experimental F-series profile."
        ),
        dflash_size=DFLASH_BYTES,
        eee_size=4096,
        dfpart_sectors=0,
        erpart_pages=16,
        scan_sectors=DFLASH_SECTORS,
        scan_offset=0,
        partition_confirmed=True,
        unknown_sector="error",
        summary=(
            "The whole 32 KB D-Flash is the EEE journal for a 4 KB emulated EEPROM. "
            "When the partition is lost the programmer can only read the raw 32 KB; "
            "replaying it rebuilds the 4 KB image the module used to expose as EEPROM."
        ),
        write_notes=(
            "Write the partition as user D-Flash = 0 sectors and EEE = 16 sectors (4 KB). "
            "That erase destroys the D-Flash copy, so keep the original dump. "
            "Program this file into the EEE / EEPROM area, not back into D-Flash, and verify the read-back. "
            "The image does not clear fault memory, short-circuit counters, mileage or checksums."
        ),
        assumptions="",
        sources=(
            "Freescale AN3490, Overview of the MC9S12XE Emulated EEPROM: 32 KB D-Flash on the S12XEP100 (16K words), 256-byte sectors, two-word sector header, 63 data records per sector, DFPART/ERPART partition rule. https://www.nxp.com/docs/en/application-note/AN3490.pdf",
            "UPA-S footwell manual for MCU MC9S12XEQ384: D-Flash 32 KB, EEE up to 4 KB, example partition EEE 16 pages and D-Flash user 0. https://www.ecupinout.com/public/STD_FRM_MCU-9S12XEQ384_OSC8_4.75v_ConnStd4.pdf",
            "Xhorse VVDI, Tips and Info about MC9S12XE Chip: every MC9S12XE D-Flash is 32 KB; giving it all to EEE yields 4 KB, and the BMW E-chassis FRM is the example. A lost partition must be read as 32 KB D-Flash and converted. https://www.xhorsevvdi.com/service/tips-and-info-about-mc9s12xe-chip.html",
            "VVDI Prog FRM repair note: read MC9S12XEQ384 with EEE 0 / D-Flash 128 sectors, convert, write partition EEE 16 sectors / D-Flash 0. E-series script is XEQ384; F-series script is listed separately as XET512. http://blog.vvdishop.com/repair-bmw-frm3-xeq384-with-vvdi-prog/",
            "This repository: the original decoder and the ci/ sample pairs (32 KB D-Flash, 4 KB EEPROM), including the FRM3 field strings in ci/input/logs.",
        ),
        fields=_FRM3_FIELDS,
    ),
    ModuleProfile(
        id="bmw_frm_f_xet512",
        title="BMW FRM F-series (XET512 script)",
        menu_label="BMW - FRM F-series XET512 script (experimental)",
        manufacturer="BMW",
        status="experimental",
        mcu="MC9S12XET512 (programmer script name)",
        vehicles=(
            "Footwell modules that VVDI Prog lists under FRM-F-SERIES-XET512 "
            "rather than FRM-E-SERIES-XEQ384. The same VVDI note also labels "
            "mask 2M25J as 9S12XEQ384, so the die name is not settled."
        ),
        dflash_size=DFLASH_BYTES,
        eee_size=4096,
        dfpart_sectors=0,
        erpart_pages=16,
        scan_sectors=DFLASH_SECTORS,
        scan_offset=0,
        partition_confirmed=False,
        unknown_sector="error",
        summary=(
            "Runs the same S12XE journal replay as FRM3. No FRM3 VIN or part-number "
            "fields are decoded, because those offsets were only checked on the E-series samples."
        ),
        write_notes=(
            "Do not program the image unless the programmer's own partition screen shows "
            "4 KB EEE and 0 user D-Flash. If it shows another size, stop and keep the backup."
        ),
        assumptions=(
            "No public repair note used here states the F-series script's D-Flash size, "
            "EEE size or DFPART/ERPART. The numbers above are copied from the E-series "
            "FRM3 (32 KB D-Flash, 4 KB EEE, DFPART 0, ERPART 16) so a tech can compare "
            "them with the programmer. They are not a confirmed F-series memory map. "
            "VVDI Prog 4.6.8 added FRM-F-SERIES-XET512 as a separate script and, in the "
            "same article, associates mask 2M25J with both that name and 9S12XEQ384."
        ),
        sources=(
            "VVDI Prog FRM repair note, F-series script named separately from the E-series XEQ384 script. http://blog.vvdishop.com/repair-bmw-frm3-xeq384-with-vvdi-prog/",
            "Freescale AN3490 for the shared S12XE record system. https://www.nxp.com/docs/en/application-note/AN3490.pdf",
        ),
        fields=(),
    ),
    ModuleProfile(
        id="jlr_xf_bcm",
        title="Jaguar XF BCM / BCMB",
        menu_label="Jaguar - XF BCM / BCMB (supported)",
        manufacturer="Jaguar",
        status="supported",
        mcu="MC9S12XE (XET256 reported, same 32 KB D-Flash family)",
        vehicles=(
            "Jaguar XF (X250) front BCM and rear BCMB. Published part numbers "
            "include rear BCMB CX23-14D628-AE / AF and the later DX23 units, "
            "and front modules in the 14B476 part family. This is not the KVM/RFA."
        ),
        dflash_size=DFLASH_BYTES,
        eee_size=2048,
        dfpart_sectors=0,
        erpart_pages=8,
        scan_sectors=DFLASH_SECTORS,
        scan_offset=0,
        partition_confirmed=True,
        unknown_sector="error",
        summary=(
            "The XF BCM gives the entire 32 KB D-Flash to the EEE journal and "
            "emulates 2 KB (8 sectors), not the 4 KB used by the BMW FRM3. "
            "The journal encoding is the same S12XE hardware format. The image "
            "written back is 2 KB. Records addressed above that window are reported "
            "as errors instead of being stored in a spare 2 KB."
        ),
        write_notes=(
            "Partition to EEE = 8 sectors (2048 bytes) and user D-Flash = 0, then "
            "write this 2 KB file to EEPROM, not to D-Flash. Verify the read-back. "
            "Key programming and configuration can still require JLR SDD. "
            "The original 4 KB FRM tool's upper half is the erased spare of a 4 KB "
            "image; this profile does not emit that spare."
        ),
        assumptions="",
        sources=(
            "Jaguar Forum, BCM repair: XF emulated EEPROM is restored with this project's D-Flash replay; the XF uses 2 KB, so the blank upper half of the old 4 KB output was discarded, and the partition used was 2048 bytes EEPROM and 0 D-Flash. https://www.jaginfo.org/threads/bcm-repair.117049/page-7",
            "Jaguar Forum, XF key-not-found: original allocation described as 2048 bytes EEPROM and 0 bytes D-Flash, with the whole 32 KB D-Flash backing that EEPROM. https://www.jaginfo.org/threads/problem-with-jaguar-xf-key-not-found.117470/page-4",
            "Jaguar Forum, BCMB dump rewrite: XF rear BCMB CX23-14D628 on MC9S12XET256; facelift DX23 partition described as 2 KB EEE and 0 D-Flash. https://www.jaginfo.org/threads/bcmb-dump-rewrite.118137/",
            "MHH AUTO, 2013 Jaguar XF BCM: MC9S12XET256, repair partition emulated EEPROM 8 x 256 and D-Flash 0. https://mhhauto.com/Thread-2013-JAGUAR-XF-BCM-FAULTY-DUE-TO-BATTERY-DISCONNECTION",
            "Xhorse VVDI, Tips and Info about MC9S12XE Chip: D-Flash on the MC9S12XE series is 32 KB. https://www.xhorsevvdi.com/service/tips-and-info-about-mc9s12xe-chip.html",
            "Freescale AN3490: family record system (256-byte sector, 63 records, DFPART/ERPART). ERPART 8 and DFPART 0 meets the size rule and is 2 KB of EEE RAM. https://www.nxp.com/docs/en/application-note/AN3490.pdf",
        ),
        fields=(),
    ),
    ModuleProfile(
        id="jlr_kvm",
        title="Jaguar / Land Rover KVM (RFA)",
        menu_label="Jaguar / Land Rover - KVM / RFA (experimental)",
        manufacturer="Jaguar / Land Rover",
        status="experimental",
        mcu="MC9S12XEQ384 (some RFA masks are XET256)",
        vehicles=(
            "Keyless Vehicle Module / Remote Function Actuator on Jaguar and "
            "Land Rover, the module programmer menus call KVM or RFA. "
            "Not the XF body BCM."
        ),
        dflash_size=DFLASH_BYTES,
        eee_size=2048,
        dfpart_sectors=16,
        erpart_pages=8,
        scan_sectors=DFLASH_SECTORS,
        scan_offset=0,
        partition_confirmed=True,
        unknown_sector="warn",
        summary=(
            "Published partition is 4 KB user D-Flash plus 2 KB EEE. "
            "A healthy locked module does not yield a plain EEE image; the chip "
            "has to be unlocked first. This profile replays a raw 32 KB read "
            "into a 2 KB image."
        ),
        write_notes=(
            "Unlock before reading (Xhorse notes the security byte FC). "
            "The published write-back partition is D-Flash 4 KB and EEPROM 2 KB. "
            "Do not program this image until it has been compared with a known-good "
            "module of the same hardware. Verify after writing."
        ),
        assumptions=(
            "Xhorse and the CG100 KVM procedure agree the partition is 4 KB user "
            "D-Flash (DFPART 16) and 2 KB EEE (ERPART 8). They do not say whether "
            "those 16 user sectors sit at the start or the end of a raw 32 KB dump, "
            "and AN3490's extracted text does not settle the order either. "
            "This profile therefore scans all 128 sectors. Sectors that are not "
            "EEE records are warnings, not hard errors, because the user D-Flash "
            "is expected to be inside the same file. Unknown commands inside a "
            "real EEE sector are still errors. The 2 KB output is unverified "
            "against a KVM dump."
        ),
        sources=(
            "Xhorse VVDI, Tips and Info about MC9S12XE Chip: Land Rover KVM is 4 KB D-Flash and 2 KB EEE, security byte FC, and must be unlocked before the EEE can be read. https://www.xhorsevvdi.com/service/tips-and-info-about-mc9s12xe-chip.html",
            "CG100 Jaguar/Land Rover KVM reset on Motorola 9S12XEQ384: adjust DFLASH 4K, EEPROM 2K. https://blog.uobdii.com/jaguar-land-rover-kvm-reset-cg100-can-read-write-9s12xeq384/",
            "VVDI Prog Land Rover RFA note: MC9S12XEQ384 D-Flash and EEPROM, masks including 1M12S. http://blog.xhorsevvdi.com/vvdi-prog-read-land-rover-rfa-1m12s-ok/",
            "Freescale AN3490 partition rule: (128-16)/8 = 14, which is a legal S12XE partition, and the record system is the family EEE. https://www.nxp.com/docs/en/application-note/AN3490.pdf",
        ),
        fields=(),
    ),
    ModuleProfile(
        id="bmw_cas4",
        title="BMW CAS4 / CAS4+",
        menu_label="BMW - CAS4 / CAS4+ (not this tool)",
        manufacturer="BMW",
        status="unsupported",
        mcu="MC9S12XDP512 (CAS4 v1) or MC9S12XEP100 (CAS4+ / v2)",
        vehicles="BMW CAS4 and CAS4+ access modules. Not an FRM, and not an EEE journal of the FRM kind.",
        dflash_size=DFLASH_BYTES,
        eee_size=0,
        dfpart_sectors=128,
        erpart_pages=0,
        scan_sectors=0,
        scan_offset=0,
        partition_confirmed=True,
        unknown_sector="error",
        summary=(
            "CAS4 version 1 (MC9S12XDP512, mask 1L15Y) stores its data in a real "
            "EEPROM, not in S12XE D-Flash emulation. CAS4+ / version 2 "
            "(MC9S12XEP100, masks 5M48H and 1N35H) is partitioned as 32 KB of "
            "user D-Flash and no EEE. Replaying this tool's EEE journal against "
            "either file would invent an image the module does not use."
        ),
        write_notes="Do not write a file from this tool to a CAS4 or CAS4+.",
        assumptions="",
        sources=(
            "Xhorse VVDI, Tips and Info about MC9S12XE Chip: BMW CAS4+ uses the whole 32 KB as D-Flash and has no EEE. https://www.xhorsevvdi.com/service/tips-and-info-about-mc9s12xe-chip.html",
            "CAS EEPROM notes: CAS4 version 1 is MC9S12XDP512 with a 4 KB EEPROM; version 2 is MC9S12XEP100 with a 32 KB D-Flash record layout that is not this EEE journal. https://tuning.wales/technical/cas-editor",
        ),
        fields=(),
        conversion_enabled=False,
    ),
)


def _validate_builtins() -> None:
    seen = set()
    for profile in BUILTIN:
        _require(profile.id not in seen, "duplicate profile " + profile.id)
        seen.add(profile.id)
        _check_profile(profile)


_validate_builtins()


def get_profile(profile_id: str, profiles: list[ModuleProfile] | None = None) -> ModuleProfile:
    choices = profiles if profiles is not None else list(BUILTIN)
    for profile in choices:
        if profile.id == profile_id:
            return profile
    known = ", ".join(profile.id for profile in choices)
    raise ProfileError("Unknown profile %r. Known profiles: %s" % (profile_id, known))


def default_extra_dirs() -> list[Path]:
    """Directories a tech can drop a JSON profile into."""

    found = []
    cwd_dir = Path.cwd() / "profiles"
    if cwd_dir.is_dir():
        found.append(cwd_dir)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent / "profiles"
        if exe_dir.is_dir() and exe_dir not in found:
            found.append(exe_dir)
    return found


def _as_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(label + " must be an integer")
    return value


def _field_from_json(raw: dict) -> Field:
    kind = raw.get("kind")
    label = raw.get("label")
    if not isinstance(label, str) or not label:
        raise ProfileError("Field is missing a label")
    if kind == "ascii":
        return AsciiField(label, _as_int(raw.get("offset"), "offset"), _as_int(raw.get("length"), "length"))
    if kind == "date":
        year = raw.get("year")
        if not isinstance(year, list) or not year:
            raise ProfileError("Date field year must be a list of offsets")
        return DateField(
            label,
            day=_as_int(raw.get("day"), "day"),
            month=_as_int(raw.get("month"), "month"),
            year_offsets=tuple(_as_int(item, "year") for item in year),
        )
    if kind == "part":
        fmt = raw.get("log_format")
        if not isinstance(fmt, str) or fmt.count("%") != 1:
            raise ProfileError("Part field log_format must contain one %% conversion")
        return PartField(
            label,
            _as_int(raw.get("offset"), "offset"),
            _as_int(raw.get("length"), "length"),
            fmt,
        )
    raise ProfileError("Unknown field kind %r" % kind)


def profile_from_json(path: Path) -> ModuleProfile:
    """Load one drop-in profile. Supported status in the file is downgraded."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("%s is not valid JSON (%s)" % (path.name, exc)) from exc
    if not isinstance(raw, dict):
        raise ProfileError(path.name + " must contain a JSON object")

    required = (
        "id", "title", "manufacturer", "status", "mcu", "vehicles",
        "dflash_size", "eee_size", "summary", "write_notes", "sources",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise ProfileError("%s is missing %s" % (path.name, ", ".join(missing)))

    sources = raw["sources"]
    if not isinstance(sources, list) or not all(isinstance(item, str) and item.strip() for item in sources):
        raise ProfileError(path.name + " sources must be a non-empty list of strings")

    status = raw["status"]
    assumptions = raw.get("assumptions") or ""
    if not isinstance(assumptions, str):
        raise ProfileError(path.name + " assumptions must be a string")
    external_note = (
        "Loaded from %s. Drop-in files are experimental even if the file says supported."
        % path.name
    )
    if status == "supported":
        status = "experimental"
        assumptions = (assumptions + " " + external_note).strip()
    elif status == "experimental":
        assumptions = (assumptions + " " + external_note).strip()
    elif status != "unsupported":
        raise ProfileError(path.name + " status must be supported, experimental or unsupported")

    conversion_enabled = raw.get("conversion_enabled", status != "unsupported")
    if status == "unsupported":
        conversion_enabled = False

    fields_raw = raw.get("fields") or []
    if not isinstance(fields_raw, list):
        raise ProfileError(path.name + " fields must be a list")

    dfpart = raw.get("dfpart_sectors")
    erpart = raw.get("erpart_pages")
    profile = ModuleProfile(
        id=str(raw["id"]),
        title=str(raw["title"]),
        menu_label=str(raw.get("menu_label") or raw["title"]),
        manufacturer=str(raw["manufacturer"]),
        status=status,
        mcu=str(raw["mcu"]),
        vehicles=str(raw["vehicles"]),
        dflash_size=_as_int(raw["dflash_size"], "dflash_size"),
        eee_size=_as_int(raw["eee_size"], "eee_size"),
        dfpart_sectors=None if dfpart is None else _as_int(dfpart, "dfpart_sectors"),
        erpart_pages=None if erpart is None else _as_int(erpart, "erpart_pages"),
        scan_sectors=_as_int(raw.get("scan_sectors", DFLASH_SECTORS), "scan_sectors"),
        scan_offset=_as_int(raw.get("scan_offset", 0), "scan_offset"),
        partition_confirmed=bool(raw.get("partition_confirmed", False)),
        unknown_sector=str(raw.get("unknown_sector", "error")),
        summary=str(raw["summary"]),
        write_notes=str(raw["write_notes"]),
        assumptions=assumptions,
        sources=tuple(sources),
        fields=tuple(_field_from_json(item) for item in fields_raw),
        conversion_enabled=bool(conversion_enabled),
        external=True,
    )
    _check_profile(profile)
    return profile


def discover_profiles(extra_dirs: list[Path] | None = None) -> tuple[list[ModuleProfile], list[str]]:
    """Built-in profiles plus any JSON profiles in ``extra_dirs``.

    Returns ``(profiles, load_errors)``. A bad JSON file is reported and
    skipped. An id that collides with a profile already loaded is an error
    for that file only.
    """

    profiles = list(BUILTIN)
    errors: list[str] = []
    seen = {profile.id for profile in profiles}
    for directory in extra_dirs or []:
        directory = Path(directory)
        if not directory.is_dir():
            errors.append("%s is not a directory" % directory)
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                profile = profile_from_json(path)
            except ProfileError as exc:
                errors.append(str(exc))
                continue
            if profile.id in seen:
                errors.append("%s repeats profile id %s" % (path.name, profile.id))
                continue
            profiles.append(profile)
            seen.add(profile.id)
    return profiles, errors
