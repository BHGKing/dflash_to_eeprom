# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Hash comparison and the first-run flag. These tests do not use the network."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dflash_eee.build_info import COMMIT, RELEASE_TAG
from dflash_eee.integrity import (
    CERTUTIL_HASH,
    FREE_BAR,
    FREE_SOFTWARE_NOTICE,
    MAX_SUMS_BYTES,
    OFFICIAL_RELEASES_URL,
    POWERSHELL_HASH,
    compose_release_notes,
    config_dir,
    config_path,
    embedded_build_info_source,
    hashes_match,
    identify_running_copy,
    mark_notice_seen,
    notice_body,
    notice_seen,
    parse_sha256sums,
    release_page_url,
    sha256_file,
    status_for_plan,
    sums_url,
    verification_plan,
    verify_release,
    write_build_info,
    LocalCopy,
)

ROOT = Path(__file__).resolve().parents[1]
KNOWN_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
TAG = "v1.2.3"
COMMIT_SHA = "ab" * 20


class _Body:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self, n=-1):
        if n < 0:
            n = len(self.payload)
        chunk = self.payload[:n]
        self.payload = self.payload[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_sha256_file_matches_the_known_vector(tmp_path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == KNOWN_SHA256
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


def test_source_checkout_is_not_hashed_as_an_executable():
    copy = identify_running_copy(frozen=False, executable=sys.executable)
    assert copy.kind == "source"
    assert copy.sha256 is None
    assert copy.path is None


def test_frozen_copy_hashes_the_given_file_not_the_interpreter(tmp_path):
    path = tmp_path / "dflash_to_eee.exe"
    path.write_bytes(b"abc")
    copy = identify_running_copy(frozen=True, executable=str(path))
    assert copy.kind == "executable"
    assert copy.sha256 == KNOWN_SHA256
    missing = identify_running_copy(frozen=True, executable=str(tmp_path / "absent.exe"))
    assert missing.kind == "executable"
    assert missing.sha256 is None


def test_parse_sha256sums_reads_the_release_file_format():
    text = "%s  dflash_to_eee.exe\n" % KNOWN_SHA256.upper()
    assert parse_sha256sums(text) == KNOWN_SHA256
    assert parse_sha256sums(text.replace("\n", "\r\n")) == KNOWN_SHA256


def test_parse_sha256sums_accepts_one_space_a_star_and_ignores_other_files():
    other = "11" * 32
    text = "\n".join([
        "# comment",
        "%s  readme.txt" % other,
        "%s *dflash_to_eee.exe" % KNOWN_SHA256,
        "",
    ])
    assert parse_sha256sums(text) == KNOWN_SHA256
    windows = "%s  C:\\Users\\tech\\dflash_to_eee.exe\n" % KNOWN_SHA256
    assert parse_sha256sums(windows) == KNOWN_SHA256


def test_parse_sha256sums_refuses_a_conflict_or_a_missing_name():
    assert parse_sha256sums("%s  other.exe\n" % KNOWN_SHA256) is None
    assert parse_sha256sums("not a hash\n") is None
    conflict = "%s  dflash_to_eee.exe\n%s  dflash_to_eee.exe\n" % (KNOWN_SHA256, "cd" * 32)
    assert parse_sha256sums(conflict) is None
    assert parse_sha256sums("\x00" + KNOWN_SHA256) is None


def test_hashes_match_is_case_insensitive_and_rejects_junk():
    assert hashes_match(KNOWN_SHA256, KNOWN_SHA256.upper())
    assert not hashes_match(KNOWN_SHA256, "cd" * 32)
    assert not hashes_match("abc", KNOWN_SHA256)
    assert not hashes_match("", "")


def test_notice_flag_is_false_until_dismissed_and_keeps_other_keys(tmp_path):
    path = tmp_path / "dflash_to_eee" / "config.json"
    assert notice_seen(path) is False
    path.parent.mkdir()
    path.write_text('{"smartscreen_notice_seen": false, "keep": 1}\n', encoding="utf-8")
    assert notice_seen(path) is False
    mark_notice_seen(path)
    assert notice_seen(path) is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["smartscreen_notice_seen"] is True
    assert saved["keep"] == 1
    mark_notice_seen(path)
    assert notice_seen(path) is True


def test_notice_flag_replaces_a_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    assert notice_seen(path) is False
    path.write_text("[1, 2]", encoding="utf-8")
    assert notice_seen(path) is False
    mark_notice_seen(path)
    assert notice_seen(path) is True


def test_config_dir_prefers_appdata_then_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert config_dir() == tmp_path / "Roaming" / "dflash_to_eee"
    assert config_path() == tmp_path / "Roaming" / "dflash_to_eee" / "config.json"
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir(platform="linux") == tmp_path / "dflash_to_eee"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_dir(platform="win32") == Path.home() / "AppData" / "Roaming" / "dflash_to_eee"


def test_free_software_notice_is_factual():
    assert "free, open-source software" in FREE_SOFTWARE_NOTICE
    assert "BSD-3-Clause" in FREE_SOFTWARE_NOTICE
    assert "including for a fee" in FREE_SOFTWARE_NOTICE
    assert "The official version is free." in FREE_SOFTWARE_NOTICE
    assert "you paid for something you could get free" in FREE_SOFTWARE_NOTICE
    assert OFFICIAL_RELEASES_URL in FREE_SOFTWARE_NOTICE
    assert OFFICIAL_RELEASES_URL == "https://github.com/BHGKing/dflash_to_eeprom/releases"
    lowered = FREE_SOFTWARE_NOTICE.lower()
    for banned in ("scammed", "refund", "report the seller", "lawsuit", "police"):
        assert banned not in lowered
    assert "official download is free" in FREE_BAR


def test_notice_body_covers_smartscreen_and_the_running_copy():
    source = notice_body(LocalCopy("source", None, None), "")
    assert "Windows protected your PC" in source
    assert "More info" in source
    assert "Run anyway" in source
    assert "source checkout" in source
    assert FREE_SOFTWARE_NOTICE in source
    assert KNOWN_SHA256 not in source

    executable = notice_body(LocalCopy("executable", Path("dflash_to_eee.exe"), KNOWN_SHA256), TAG)
    assert KNOWN_SHA256 in executable
    assert POWERSHELL_HASH in executable
    assert TAG in executable
    unreadable = notice_body(LocalCopy("executable", Path("dflash_to_eee.exe"), None), "")
    assert "could not be read" in unreadable
    assert "no release tag" in unreadable


def test_verification_plan_downloads_only_for_a_tagged_executable():
    exe = LocalCopy("executable", Path("dflash_to_eee.exe"), KNOWN_SHA256)
    source = LocalCopy("source", None, None)
    assert verification_plan(exe, TAG, check_online=False) == "local_only"
    assert verification_plan(source, TAG, check_online=True) == "source"
    assert verification_plan(LocalCopy("executable", Path("x"), None), TAG, True) == "unreadable"
    assert verification_plan(exe, "", True) == "no_tag"
    assert verification_plan(exe, TAG, True) == "download"
    assert "Downloading SHA256SUMS.txt for v1.2.3" in status_for_plan("download", TAG)
    assert "no published executable" in status_for_plan("source", "").lower()


def test_sums_url_and_release_page_use_the_official_repository():
    assert sums_url(TAG) == (
        "https://github.com/BHGKing/dflash_to_eeprom/releases/download/v1.2.3/SHA256SUMS.txt"
    )
    assert sums_url("v1.0.0-rc.1").endswith("/v1.0.0-rc.1/SHA256SUMS.txt")
    assert release_page_url(TAG) == "https://github.com/BHGKing/dflash_to_eeprom/releases/tag/v1.2.3"
    assert release_page_url("") == OFFICIAL_RELEASES_URL
    assert release_page_url("../etc") == OFFICIAL_RELEASES_URL
    with pytest.raises(ValueError):
        sums_url("v1")


def test_verify_release_match_mismatch_and_could_not_check():
    calls = []

    def urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        body = ("%s  dflash_to_eee.exe\n" % KNOWN_SHA256).encode("ascii")
        return _Body(body)

    match = verify_release(TAG, KNOWN_SHA256.upper(), timeout=5, urlopen=urlopen)
    assert match.status == "match"
    assert match.remote_hash == KNOWN_SHA256
    assert calls == [(sums_url(TAG), 5.0)]

    def mismatch_open(request, timeout):
        return _Body(("%s  dflash_to_eee.exe\n" % ("cd" * 32)).encode("ascii"))

    mismatch = verify_release(TAG, KNOWN_SHA256, urlopen=mismatch_open)
    assert mismatch.status == "mismatch"
    assert "Mismatch." in mismatch.detail

    def broken(request, timeout):
        raise TimeoutError("timed out")

    failed = verify_release(TAG, KNOWN_SHA256, urlopen=broken)
    assert failed.status == "could_not_check"
    assert "could not be downloaded" in failed.detail

    def http_error(request, timeout):
        return _Body(b"nope", status=404)

    missing = verify_release(TAG, KNOWN_SHA256, urlopen=http_error)
    assert missing.status == "could_not_check"

    def garbage(request, timeout):
        return _Body(b"# no hash for the exe\n")

    bad_file = verify_release(TAG, KNOWN_SHA256, urlopen=garbage)
    assert bad_file.status == "could_not_check"
    assert "did not contain a hash" in bad_file.detail

    def huge(request, timeout):
        return _Body(b"a" * (MAX_SUMS_BYTES + 1))

    assert verify_release(TAG, KNOWN_SHA256, urlopen=huge).status == "could_not_check"


def test_verify_release_does_not_download_without_a_tag_or_a_hash():
    def urlopen(request, timeout):
        raise AssertionError("network used")

    no_tag = verify_release("", KNOWN_SHA256, urlopen=urlopen)
    assert no_tag.status == "could_not_check"
    assert "no release tag" in no_tag.detail
    no_hash = verify_release(TAG, None, urlopen=urlopen)
    assert no_hash.status == "could_not_check"
    bad_tag = verify_release("not a tag", KNOWN_SHA256, urlopen=urlopen)
    assert bad_tag.status == "could_not_check"


def test_commands_are_the_documented_ones():
    assert POWERSHELL_HASH == "Get-FileHash .\\dflash_to_eee.exe -Algorithm SHA256"
    assert CERTUTIL_HASH == "certutil -hashfile dflash_to_eee.exe SHA256"


def test_release_notes_lead_with_the_free_callout_then_the_hash():
    digest = "cd" * 32
    notes = compose_release_notes(
        "## What's Changed\n* fix a journal\n",
        sha256=digest.upper(),
        tag="v1.0.0-rc.1",
        commit=COMMIT_SHA.upper(),
        repository="BHGKing/dflash_to_eeprom",
        run_id="4242",
    )
    assert notes.startswith("## This software is free\n")
    assert notes.index("## This software is free") < notes.index("## Verify your download")
    assert notes.index("## Verify your download") < notes.index("## What's Changed")
    assert digest in notes
    assert POWERSHELL_HASH in notes
    assert "Windows protected your PC" in notes
    assert "https://github.com/BHGKing/dflash_to_eeprom/tree/v1.0.0-rc.1" in notes
    assert "https://github.com/BHGKing/dflash_to_eeprom/commit/%s" % COMMIT_SHA in notes
    assert "https://github.com/BHGKing/dflash_to_eeprom/actions/runs/4242" in notes
    assert "https://github.com/BHGKing/dflash_to_eeprom/releases/download/v1.0.0-rc.1/SHA256SUMS.txt" in notes
    assert "build from that tag" in notes
    assert OFFICIAL_RELEASES_URL in notes
    empty = compose_release_notes("", sha256=digest, tag=TAG, commit=COMMIT_SHA,
                                  repository="BHGKing/dflash_to_eeprom", run_id="1")
    assert empty.strip().endswith("Release v1.2.3.")


def test_embedded_build_info_round_trip(tmp_path):
    source = embedded_build_info_source(" v1.2.3 ", COMMIT_SHA.upper())
    namespace = {}
    exec(source, namespace)
    assert namespace["RELEASE_TAG"] == "v1.2.3"
    assert namespace["COMMIT"] == COMMIT_SHA
    destination = tmp_path / "build_info.py"
    write_build_info(destination, "v1.2.3", COMMIT_SHA)
    assert 'RELEASE_TAG = "v1.2.3"' in destination.read_text(encoding="utf-8") or (
        "RELEASE_TAG = 'v1.2.3'" in destination.read_text(encoding="utf-8")
    )
    with pytest.raises(ValueError):
        embedded_build_info_source("v1", COMMIT_SHA)
    with pytest.raises(ValueError):
        embedded_build_info_source(TAG, "abc")
    with pytest.raises(ValueError):
        embedded_build_info_source("../v1.0.0", COMMIT_SHA)


def test_source_tree_has_no_embedded_release():
    assert RELEASE_TAG == ""
    assert COMMIT == ""


def test_embed_and_notes_commands(tmp_path):
    destination = tmp_path / "build_info.py"
    env = os.environ.copy()
    env["DFLASH_RELEASE_TAG"] = TAG
    env["DFLASH_RELEASE_COMMIT"] = COMMIT_SHA
    embedded = subprocess.run(
        [sys.executable, str(ROOT / "ci" / "release_publish.py"), "embed", "--path", str(destination)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert embedded.returncode == 0, embedded.stderr
    namespace = {}
    exec(destination.read_text(encoding="utf-8"), namespace)
    assert namespace["RELEASE_TAG"] == TAG

    env["DFLASH_RELEASE_TAG"] = "nope"
    rejected = subprocess.run(
        [sys.executable, str(ROOT / "ci" / "release_publish.py"), "embed", "--path", str(tmp_path / "no.py")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert not (tmp_path / "no.py").exists()

    generated = tmp_path / "generated.md"
    generated.write_text("## What's Changed\n* fix\n", encoding="utf-8")
    output = tmp_path / "RELEASE_NOTES.md"
    notes = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ci" / "release_publish.py"),
            "notes",
            "--generated", str(generated),
            "--output", str(output),
            "--tag", TAG,
            "--hash", KNOWN_SHA256,
            "--commit", COMMIT_SHA,
            "--run-id", "99",
            "--repository", "BHGKing/dflash_to_eeprom",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert notes.returncode == 0, notes.stderr
    text = output.read_text(encoding="utf-8")
    assert text.startswith("## This software is free")
    assert KNOWN_SHA256 in text
    assert "## What's Changed" in text


def test_readme_and_release_workflow_publish_the_same_guidance():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# D-Flash to EEPROM\n\n## This software is free\n")
    assert CERTUTIL_HASH in readme
    assert POWERSHELL_HASH in readme
    assert "### Build from source" in readme
    assert "Verifying downloads / Windows SmartScreen" in readme
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "release_publish.py embed" in workflow
    assert "release_publish.py notes" in workflow
    assert "--notes-file" in workflow
    assert "releases/generate-notes" in workflow
    assert "--generate-notes" not in workflow
    assert "DFLASH_RELEASE_TAG" in workflow
    assert "DFLASH_RELEASE_COMMIT" in workflow
    assert "This software is free" in workflow
    assert "Get-FileHash .\\dflash_to_eee.exe -Algorithm SHA256" in workflow
