# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Rebuild a Freescale/NXP S12XE emulated-EEPROM image from a D-Flash dump.

The conversion core lives here so the command-line tool, the desktop GUI and
the web upload all call the same code. Module geometry (D-Flash size, EEE
size, which sectors to replay) comes from :mod:`dflash_eee.profiles`.
"""

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
    "ConversionResult",
    "DFlashConverter",
    "InputError",
    "ProfileError",
    "convert_bytes",
    "convert_file",
    "discover_profiles",
    "get_profile",
    "partition_is_valid",
]

__version__ = "2.0.0"
