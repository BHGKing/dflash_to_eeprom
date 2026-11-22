# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Keep the existing suite in English, away from the developer's real config.

A Spanish OS locale or a saved language choice must not change the golden logs
or the English sentences the older tests look for. Language coverage lives in
``test_i18n.py`` and sets ``LANG`` / ``--lang`` itself.
"""

import os
import tempfile
from pathlib import Path

import pytest

_config = Path(tempfile.mkdtemp(prefix="dflash-eee-cfg-")) / "config.json"
os.environ["DFLASH_TO_EEPROM_CONFIG"] = str(_config)
os.environ["LC_ALL"] = "C"
os.environ["LC_MESSAGES"] = "C"
os.environ["LANG"] = "C"
os.environ["LANGUAGE"] = "en"


def pytest_configure():
    # Importing the library translates profile checks. Do it after the env is set.
    from dflash_eee.i18n import set_language
    set_language("en")


@pytest.fixture(autouse=True)
def _english_ui():
    from dflash_eee.i18n import set_language
    set_language("en")
    yield
    set_language("en")
