# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Rebuild a Freescale/NXP S12XE emulated-EEPROM image from a D-Flash dump.

The conversion core lives here so the command-line tool, the desktop GUI and
the web upload all call the same code. Module geometry (D-Flash size, EEE
size, which sectors to replay) comes from :mod:`dflash_eee.profiles`.
Comparing two dumps (original against read-back, or two modules) lives in
:mod:`dflash_eee.compare`.
"""

from dflash_eee.compare import CompareResult, DiffRange, compare_bytes, write_report
from dflash_eee.converter import (
    ConversionResult,
    DFlashConverter,
    InputError,
    convert_bytes,
    convert_file,
)
from dflash_eee.profiles import (
    ProfileError,
    discover_profiles,
    get_profile,
    partition_is_valid,
)

__all__ = [
    "CompareResult",
    "ConversionResult",
    "DFlashConverter",
    "DiffRange",
    "InputError",
    "ProfileError",
    "compare_bytes",
    "convert_bytes",
    "convert_file",
    "discover_profiles",
    "get_profile",
    "partition_is_valid",
    "write_report",
]

__version__ = "2.0.0"
