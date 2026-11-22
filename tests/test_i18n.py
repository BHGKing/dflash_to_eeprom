# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Catalog parity, placeholder agreement, fallback, and language selection."""

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from dflash_eee.converter import JOURNAL_MESSAGES
from dflash_eee.i18n import (
    catalogs,
    config_path,
    locales_dir,
    normalize,
    reload_catalogs,
    resolve_language,
    save_language,
    set_language,
    t,
)
from dflash_eee.profiles import BUILTIN

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dflash_to_eee.py"
GOOD = ROOT / "ci" / "input" / "dflash" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"
WARN = ROOT / "ci" / "input" / "dflash" / "14a0823be62fb1c822015c7c41aee861abd68967.bin"
LOGS = ROOT / "ci" / "input" / "logs"

_NAMED = re.compile(r"%\([^)]+\)[#0\- +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]")
_KEY = re.compile(r'["\']((?:gui|report|cli|profile|field|status|converter)\.[A-Za-z0-9_.]+)["\']')


def _pieces(text: str) -> tuple[list[str], int]:
    found = []
    literals = 0
    index = 0
    while index < len(text):
        if text[index] != "%":
            index += 1
            continue
        if text.startswith("%%", index):
            literals += 1
            index += 2
            continue
        match = _NAMED.match(text, index)
        if match:
            found.append(match.group(0))
            index = match.end()
            continue
        literals += 1
        index += 1
    return found, literals


def _load(code: str) -> dict[str, str]:
    return json.loads((locales_dir() / ("%s.json" % code)).read_text(encoding="utf-8"))


def _log_lines(blob: bytes) -> list[bytes]:
    kept = []
    for line in blob.splitlines():
        if line.startswith(b"Traceback"):
            continue
        if line.startswith(b"  File "):
            continue
        if line.startswith(b"    "):
            continue
        kept.append(line)
    return kept


def test_spanish_keys_exist_in_english_and_placeholders_match():
    en = _load("en")
    es = _load("es")
    assert set(es) <= set(en)
    assert set(en) <= set(es)
    for key, english in en.items():
        assert isinstance(english, str) and english.strip()
        assert isinstance(es[key], str) and es[key].strip()
        english_parts, english_literals = _pieces(english)
        spanish_parts, spanish_literals = _pieces(es[key])
        assert english_literals == spanish_literals, key
        assert Counter(english_parts) == Counter(spanish_parts), key


def test_english_catalog_matches_profile_source_and_journal_logs():
    en = _load("en")
    for profile in BUILTIN:
        for attr in ("title", "menu_label", "vehicles", "summary", "write_notes", "assumptions"):
            value = getattr(profile, attr)
            key = "profile.%s.%s" % (profile.id, attr)
            if value == "":
                assert key not in en
            else:
                assert en[key] == value
        for index, source in enumerate(profile.sources):
            assert en["profile.%s.source.%d" % (profile.id, index)] == source
    for key, english in JOURNAL_MESSAGES.items():
        assert en[key] == english


def test_quoted_catalog_keys_are_translated():
    en = _load("en")
    found = set()
    for path in list((ROOT / "dflash_eee").rglob("*.py")) + [ROOT / "gui.py"]:
        found.update(_KEY.findall(path.read_text(encoding="utf-8")))
    # Stems that gain .one / .many, and the status / profile / drop keys built at runtime.
    found.update({
        "status.supported",
        "status.experimental",
        "status.unsupported",
        "gui.drop.hint",
        "gui.drop.no_dnd",
        "report.sectors_not_eee.one",
        "report.sectors_not_eee.many",
        "report.unknown_cmd.one",
        "report.unknown_cmd.many",
        "report.out_of_range.one",
        "report.out_of_range.many",
        "report.records_past.one",
        "report.records_past.many",
        "report.window.outside.one",
        "report.window.outside.many",
        "report.reason.new_blocks",
        "report.reason.last_block",
        "report.reason.longest_empty",
    })
    missing = sorted(
        key for key in found
        if key not in en and not (key + ".one" in en and key + ".many" in en)
    )
    assert missing == []


def test_phrases_the_older_tests_look_for_stay_in_english():
    en = _load("en")
    assert en["report.outcome.ok"] == "Conversion succeeded"
    assert en["report.size.short"].startswith("Wrong size:")
    assert en["report.size.extra"].startswith("Wrong size:")
    assert en["report.unrecognized"].startswith("Unrecognized")
    assert en["status.supported"] == "Supported"
    assert en["status.experimental"] == "Experimental"
    assert en["status.unsupported"] == "Not supported"
    assert en["profile.err.unknown"].startswith("Unknown profile")
    assert "experimental" in en["profile.err.external_note"]
    assert "CAS4" in en["profile.bmw_cas4.summary"]
    assert en["profile.bmw_cas4.menu_label"] == "BMW - CAS4 / CAS4+ (not this tool)"
    assert en["profile.jlr_xf_bcm.menu_label"] == "Jaguar - XF BCM / BCMB (supported)"
    assert en["profile.jlr_kvm.menu_label"] == "Jaguar / Land Rover - KVM / RFA (experimental)"
    assert "outside this profile's EEE window" in en["report.window.outside.one"]
    assert "outside this profile's EEE window" in en["report.window.outside.many"]
    assert "no EEPROM image" in en["report.window.empty"]
    assert en["gui.menu.verify"] == "Verify this copy"
    assert en["gui.menu.help"] == "Help"
    assert en["gui.menu.about"] == "About"
    assert "free" in en["gui.window_title"].lower()
    assert "official download is free" in en["gui.free_bar"]
    from dflash_eee.integrity import FREE_BAR, FREE_SOFTWARE_NOTICE
    assert en["gui.free_bar"] == FREE_BAR
    assert en["gui.free_notice"] == FREE_SOFTWARE_NOTICE
    assert "Windows protected your PC" in en["gui.verify.smartscreen"]
    assert "More info" in en["gui.verify.smartscreen"]
    assert "Run anyway" in en["gui.verify.smartscreen"]
    assert "source checkout" in en["gui.verify.source_checkout"]
    assert "could not be read" in en["gui.verify.hash_unreadable"]
    assert "no release tag" in en["gui.verify.no_tag_embedded"]
    assert "Downloading SHA256SUMS.txt for %(tag)s" in en["gui.verify.status.download"]
    assert "no published executable" in en["gui.verify.status.source"]
    assert en["gui.verify.detail.mismatch"].startswith("Mismatch.")
    assert "could not be downloaded" in en["gui.verify.detail.download_failed"]
    assert "did not contain a hash" in en["gui.verify.detail.missing_name"]
    assert "no release tag" in en["gui.verify.detail.no_tag"]


def test_spanish_catalog_fits_the_legacy_latin1_console():
    for key, value in _load("es").items():
        try:
            value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise AssertionError("%s is not latin-1: %s" % (key, exc)) from exc


def test_missing_translation_falls_back_to_english():
    set_language("es")
    table = catalogs()["es"]
    saved = table.pop("gui.button.convert")
    try:
        assert t("gui.button.convert") == "Convert to EEPROM"
        assert t("no.such.key", default="Hello") == "Hello"
        assert t("no.such.key") == "no.such.key"
    finally:
        table["gui.button.convert"] = saved


def test_broken_placeholder_falls_back_to_the_english_sentence():
    set_language("es")
    table = catalogs()["es"]
    saved = table["report.size.short"]
    table["report.size.short"] = "sin marcadores"
    try:
        assert t("report.size.short", nbytes=512, title="BMW / Mini FRM3", needed=32768) == (
            "Wrong size: 512 bytes. BMW / Mini FRM3 needs 32768 bytes."
        )
    finally:
        table["report.size.short"] = saved


def test_spanish_size_sentence_keeps_the_numbers():
    set_language("es")
    text = t("report.size.short", nbytes=512, title="BMW / Mini FRM3", needed=32768)
    assert text.startswith("Tamaño incorrecto:")
    assert "512" in text and "32768" in text
    assert "BMW / Mini FRM3" in text


def test_normalize_and_resolution_order(tmp_path, monkeypatch):
    assert normalize("es_MX.UTF-8") == "es"
    assert normalize("es-ES") == "es"
    assert normalize("en_US.UTF-8") == "en"
    assert normalize("C") == "en"
    assert normalize("POSIX") == "en"
    assert normalize("fr_FR.UTF-8") is None
    assert normalize("") is None
    assert normalize(None) is None

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"language": "es", "other": 1}), encoding="utf-8")
    monkeypatch.setenv("DFLASH_TO_EEPROM_CONFIG", str(cfg))
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "en_US.UTF-8")
    # A saved choice wins for the window. LANG wins for the command line.
    assert resolve_language(honor_env=False) == "es"
    assert resolve_language(honor_env=True) == "en"
    assert resolve_language("es", honor_env=True) == "es"
    with pytest.raises(Exception):
        resolve_language("fr", honor_env=True)

    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.delenv("DFLASH_TO_EEPROM_CONFIG", raising=False)
    # No saved file: the C locale is English.
    monkeypatch.setenv("DFLASH_TO_EEPROM_CONFIG", str(tmp_path / "missing.json"))
    assert resolve_language(honor_env=False) == "en"


def test_save_language_keeps_other_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"notice": "seen"}), encoding="utf-8")
    monkeypatch.setenv("DFLASH_TO_EEPROM_CONFIG", str(cfg))
    save_language("es")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["language"] == "es"
    assert data["notice"] == "seen"
    assert config_path() == cfg
    set_language("en")
    reload_catalogs()


def test_frozen_bundle_directory_is_preferred(monkeypatch, tmp_path):
    bundled = tmp_path / "dflash_eee" / "locales"
    bundled.mkdir(parents=True)
    (bundled / "en.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert locales_dir() == bundled


def test_spec_bundles_the_locale_directory():
    spec = (ROOT / "dflash_to_eee.spec").read_text(encoding="utf-8")
    assert "dflash_eee" in spec and "locales" in spec
    assert (ROOT / "dflash_eee" / "locales" / "en.json").is_file()
    assert (ROOT / "dflash_eee" / "locales" / "es.json").is_file()


def _run(args, env):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        env=env,
    )


def test_cli_lang_flag_and_locale(tmp_path):
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["LC_MESSAGES"] = "C"
    listed = _run(["--lang", "es", "--list-profiles"], env)
    text = listed.stdout.decode("latin-1")
    assert listed.returncode == 0
    for profile_id in ("bmw_frm3", "bmw_frm_f_xet512", "jlr_xf_bcm", "jlr_kvm", "bmw_cas4"):
        assert profile_id in text
    assert "Soportado" in text
    assert "No soportado" in text
    assert "MC9S12XEQ384" in text

    forced = _run(["--lang", "en", "--list-profiles"], {**env, "LC_ALL": "es_MX.UTF-8", "LANG": "es_MX.UTF-8"})
    english = forced.stdout.decode("latin-1")
    assert "Supported" in english
    assert "Not supported" in english
    assert "Soportado" not in english

    spanish_env = os.environ.copy()
    spanish_env["LC_ALL"] = "es_MX.UTF-8"
    spanish_env["LC_MESSAGES"] = "es_MX.UTF-8"
    spanish_env["LANG"] = "es_MX.UTF-8"
    via_lang = _run(["--list-profiles"], spanish_env)
    assert "Soportado" in via_lang.stdout.decode("latin-1")

    bad = _run(["--lang", "fr", "--list-profiles"], env)
    assert bad.returncode == 1
    assert b"Unknown language" in bad.stderr

    destination = tmp_path / "out.bin"
    converted = _run(["--lang", "es", "--profile", "bmw_frm3", str(GOOD), str(destination)], env)
    report = converted.stderr.decode("latin-1")
    assert converted.returncode == 0
    assert "VIN: 83IM9D60QABCUS9S4" in report
    assert "Conversión correcta" in report
    assert destination.read_bytes() == (ROOT / "ci" / "input" / "eee" / GOOD.name).read_bytes()


def test_legacy_two_argument_log_stays_english_when_lang_is_spanish(tmp_path):
    env = os.environ.copy()
    env["LC_ALL"] = "es_MX.UTF-8"
    env["LC_MESSAGES"] = "es_MX.UTF-8"
    env["LANG"] = "es_MX.UTF-8"
    for source in (GOOD, WARN):
        destination = tmp_path / source.name
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(source), str(destination)],
            cwd=str(ROOT),
            capture_output=True,
            env=env,
        )
        golden = (LOGS / source.name).read_bytes()
        assert _log_lines(result.stdout + result.stderr) == _log_lines(golden), source.name
