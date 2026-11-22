# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Message catalogs for the desktop window and the command line.

English (``en.json``) is the source. A missing key in another language falls
back to English, then to the ``default`` argument, then to the key itself.
Catalogs are ordinary JSON files next to this module so a PyInstaller
one-file build can unpack them from ``sys._MEIPASS``.

The journal lines compared with ``ci/input/logs`` are not translated. Those
sentences are the 2017 decoder's log, not chrome.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


# Endonyms. The menu shows these names as written, in every language.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Español",
}

_catalogs: dict[str, dict[str, str]] | None = None
_language: str | None = None


class LanguageError(Exception):
    """``--lang`` asked for a language this build does not ship."""

    def __init__(self, code: object):
        self.code = code
        super().__init__(str(code))


def locales_dir() -> Path:
    """Directory of ``en.json`` / ``es.json``, including inside a frozen exe."""

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "dflash_eee" / "locales")
        candidates.append(Path(sys.executable).resolve().parent / "dflash_eee" / "locales")
    candidates.append(Path(__file__).resolve().parent / "locales")
    for path in candidates:
        if (path / "en.json").is_file():
            return path
    return candidates[-1]


def reload_catalogs() -> dict[str, dict[str, str]]:
    """Read the JSON catalogs again. Tests use this after editing a file."""

    global _catalogs
    loaded: dict[str, dict[str, str]] = {}
    folder = locales_dir()
    for code in LANGUAGES:
        path = folder / ("%s.json" % code)
        if not path.is_file():
            loaded[code] = {}
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("%s must be a JSON object" % path.name)
        loaded[code] = {str(key): value for key, value in data.items() if isinstance(value, str)}
    _catalogs = loaded
    return loaded


def catalogs() -> dict[str, dict[str, str]]:
    global _catalogs
    if _catalogs is None:
        reload_catalogs()
    assert _catalogs is not None
    return _catalogs


def normalize(code: object) -> str | None:
    """Map ``es_MX.UTF-8``, ``es-MX``, ``C`` or ``Español``-unrelated tags to ``en``/``es``.

    Unknown languages return ``None`` so the caller can keep English.
    ``C`` and ``POSIX`` are the C locale, which this tool treats as English.
    """

    if not isinstance(code, str):
        return None
    raw = code.strip()
    if not raw:
        return None
    token = raw.split(".", 1)[0].split("@", 1)[0].replace("-", "_").lower()
    if token in ("c", "posix"):
        return "en"
    if token in LANGUAGES:
        return token
    primary = token.split("_", 1)[0]
    if primary in LANGUAGES:
        return primary
    return None


def available_languages() -> list[tuple[str, str]]:
    return [(code, LANGUAGES[code]) for code in LANGUAGES]


def config_path() -> Path:
    """Per-user config file. ``DFLASH_TO_EEPROM_CONFIG`` overrides it for tests.

    The language key lives in the same file as the first-run notice.
    """

    override = os.environ.get("DFLASH_TO_EEPROM_CONFIG")
    if override:
        return Path(override)
    from dflash_eee.integrity import config_path as shared_config_path

    return shared_config_path()


def language_from_config() -> str | None:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("language")
    return normalize(value)


def save_language(code: str) -> None:
    """Remember the menu choice. Other keys in the file are left in place."""

    chosen = normalize(code)
    if chosen is None:
        raise LanguageError(code)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            data = loaded
    data["language"] = chosen
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def user_locale_tag() -> str | None:
    """OS locale tag, without changing the process numeric locale.

    Environment variables win, because that is what a terminal user set.
    A Windows desktop often has none of them, so the user default locale is
    read from the system. On other platforms the C library's message locale
    is queried and restored.
    """

    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            return value
    if sys.platform == "win32":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
                return buffer.value or None
        except (AttributeError, OSError):
            pass
    try:
        import locale
        saved = locale.setlocale(locale.LC_MESSAGES, None)
        try:
            locale.setlocale(locale.LC_MESSAGES, "")
            return locale.setlocale(locale.LC_MESSAGES, None)
        finally:
            locale.setlocale(locale.LC_MESSAGES, saved)
    except (locale.Error, OSError, AttributeError):
        return None


def resolve_language(explicit: str | None = None, *, honor_env: bool = False) -> str:
    """Pick a language code.

    The window calls this with ``honor_env`` false: a saved menu choice beats
    the OS locale, and the OS locale is used only on the first run.

    The command line calls it with ``honor_env`` true so ``--lang`` wins,
    then ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG``, then the saved choice, then
    the OS locale.
    """

    if isinstance(explicit, str) and explicit.strip():
        chosen = normalize(explicit)
        if chosen is None:
            raise LanguageError(explicit)
        return chosen
    if honor_env:
        for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
            chosen = normalize(os.environ.get(key))
            if chosen:
                return chosen
    saved = language_from_config()
    if saved:
        return saved
    detected = normalize(user_locale_tag())
    if detected:
        return detected
    return "en"


def get_language() -> str:
    global _language
    if _language is None:
        try:
            _language = resolve_language(honor_env=False)
        except LanguageError:
            _language = "en"
    return _language


def set_language(code: str) -> str:
    global _language
    chosen = normalize(code)
    if chosen is None:
        raise LanguageError(code)
    _language = chosen
    return chosen


def reset_language() -> None:
    """Forget the in-process choice so the next lookup reads config and locale."""

    global _language
    _language = None


def ensure_language() -> str:
    return get_language()


def init_language(explicit: str | None = None, *, honor_env: bool = False) -> str:
    global _language
    _language = resolve_language(explicit, honor_env=honor_env)
    return _language


_PLACEHOLDERS = re.compile(r"%\(([^)]+)\)")


def _placeholder_names(template: str) -> set[str]:
    return set(_PLACEHOLDERS.findall(template))


def t(key: str, default: str | None = None, **kwargs: object) -> str:
    """Translate ``key``. Missing keys fall back to English, then ``default``.

    A translation that drops a placeholder also falls back, so a half-finished
    catalog cannot swallow a byte count or a file name.
    """

    table = catalogs()
    english = table.get("en", {}).get(key)
    template = table.get(get_language(), {}).get(key)
    if template is None:
        template = english
    if not isinstance(template, str):
        template = default if default is not None else key
    if kwargs and isinstance(english, str) and not _placeholder_names(english) <= _placeholder_names(template):
        template = english
    if not kwargs:
        return template
    try:
        return template % kwargs
    except (KeyError, TypeError, ValueError):
        fallback = english if isinstance(english, str) else (default if default is not None else key)
        try:
            return fallback % kwargs
        except (KeyError, TypeError, ValueError):
            return fallback
