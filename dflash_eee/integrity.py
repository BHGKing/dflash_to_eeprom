# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Hash the running program and compare it with the published checksum.

Nothing in this module contacts the network unless ``verify_release`` is
called. Startup uses the local hash and the first-run flag only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = "BHGKing/dflash_to_eeprom"
SOURCE_REPOSITORY_URL = "https://github.com/" + REPOSITORY
OFFICIAL_RELEASES_URL = SOURCE_REPOSITORY_URL + "/releases"
EXE_NAME = "dflash_to_eee.exe"
SUMS_NAME = "SHA256SUMS.txt"
APP_DIR_NAME = "dflash_to_eee"
CONFIG_NAME = "config.json"
NOTICE_FLAG = "smartscreen_notice_seen"
POWERSHELL_HASH = r"Get-FileHash .\dflash_to_eee.exe -Algorithm SHA256"
CERTUTIL_HASH = "certutil -hashfile dflash_to_eee.exe SHA256"
# BSD-3 allows redistribution, including for a fee. Say that the official
# copy is free. Do not call a charge a crime or tell the reader to sue,
# demand a refund, or report the seller.
FREE_SOFTWARE_NOTICE = (
    "dflash_to_eee is free, open-source software (BSD-3-Clause). "
    "The license allows other people to redistribute the program, including for a fee. "
    "The official version is free. If someone charged you for it, you paid for something "
    "you could get free from the official page: " + OFFICIAL_RELEASES_URL
)
FREE_BAR = "Free & open source (BSD-3) - the official download is free"
USER_AGENT = "dflash-to-eee-verify"
DEFAULT_TIMEOUT = 5.0
MAX_SUMS_BYTES = 8192

TAG_RE = re.compile(
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$"
)
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RUN_RE = re.compile(r"^[0-9]+$")
SERVER_RE = re.compile(r"^https://[A-Za-z0-9.-]+$")
_SUM_LINE = re.compile(r"^(?P<hash>[0-9a-fA-F]{64})\s+\*?(?P<name>\S+)\s*$")


@dataclass(frozen=True)
class LocalCopy:
    """The program the user actually started."""

    kind: str
    path: Path | None
    sha256: str | None


@dataclass(frozen=True)
class VerifyResult:
    """Result of comparing a local hash with SHA256SUMS.txt.

    ``status`` is ``match``, ``mismatch``, or ``could_not_check``.
    """

    status: str
    local_hash: str | None
    remote_hash: str | None
    detail: str


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def identify_running_copy(frozen: bool | None = None, executable: str | None = None) -> LocalCopy:
    """Hash ``sys.executable`` when frozen. A source run is reported as source.

    The interpreter itself is not hashed. A published checksum belongs to
    ``dflash_to_eee.exe``, and hashing Python would only look like a mismatch.
    """

    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return LocalCopy("source", None, None)
    path = Path(executable if executable is not None else sys.executable)
    try:
        digest = sha256_file(path)
    except OSError:
        return LocalCopy("executable", path, None)
    return LocalCopy("executable", path, digest)


def parse_sha256sums(text: str, filename: str = EXE_NAME) -> str | None:
    """Return the lowercase hash for ``filename`` from a SHA256SUMS file.

    Lines look like ``<64 hex>  dflash_to_eee.exe`` (two spaces, GNU style).
    A binary-mode star and a single space are accepted. Comments and other
    files are ignored. Two different hashes for the same file are refused.
    """

    if "\x00" in text:
        return None
    found = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SUM_LINE.match(line)
        if match is None:
            continue
        name = match.group("name").replace("\\", "/").rsplit("/", 1)[-1]
        if name != filename:
            continue
        digest = match.group("hash").lower()
        if found is not None and found != digest:
            return None
        found = digest
    return found


def hashes_match(actual: str, expected: str) -> bool:
    """Compare two SHA-256 hex strings. Case does not matter."""

    if not HASH_RE.fullmatch(actual) or not HASH_RE.fullmatch(expected):
        return False
    return actual.lower() == expected.lower()


def sums_url(tag: str) -> str:
    """Download URL of SHA256SUMS.txt for a release tag."""

    if not TAG_RE.fullmatch(tag):
        raise ValueError("not a version tag")
    return "https://github.com/%s/releases/download/%s/%s" % (REPOSITORY, tag, SUMS_NAME)


def release_page_url(tag: str = "") -> str:
    """Release page for a tag, or the releases index when the tag is unknown."""

    base = SOURCE_REPOSITORY_URL + "/releases"
    if tag and TAG_RE.fullmatch(tag):
        return base + "/tag/" + tag
    return base


def config_dir(platform: str | None = None) -> Path:
    """Per-user directory. On Windows this is ``%APPDATA%/dflash_to_eee``."""

    system = sys.platform if platform is None else platform
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    if system == "win32":
        return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_DIR_NAME


def config_path(platform: str | None = None) -> Path:
    return config_dir(platform) / CONFIG_NAME


def notice_seen(path: Path) -> bool:
    """True only when the config file records that the notice was dismissed."""

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(loaded, dict):
        return False
    return loaded.get(NOTICE_FLAG) is True


def mark_notice_seen(path: Path) -> None:
    """Record that the first-run notice was dismissed. Other keys are kept."""

    data: dict = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        loaded = None
    if isinstance(loaded, dict):
        data = loaded
    data[NOTICE_FLAG] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def notice_body(copy: LocalCopy, tag: str) -> str:
    """Text for the first-run notice and the Verify this copy dialog."""

    lines = [
        FREE_SOFTWARE_NOTICE,
        "",
        'Windows may show "Windows protected your PC" the first time you open '
        "dflash_to_eee.exe. The download is an unsigned open-source build, so "
        "SmartScreen does not know the publisher. Click More info, then Run anyway, "
        "only after you have checked the file.",
        "",
    ]
    if copy.kind != "executable":
        lines.append(
            "This window is running from a source checkout, not from the published "
            "dflash_to_eee.exe. There is no downloaded executable to hash. The "
            "SmartScreen warning applies to the unsigned exe from the Releases page. "
            "You can read the source at the link below, or build the exe yourself."
        )
        return "\n".join(lines)
    lines.append("SHA-256 of this executable:")
    lines.append(copy.sha256 or "(the file could not be read)")
    if copy.path is not None:
        lines.append(str(copy.path))
    lines.append("")
    lines.append("Compare it with SHA256SUMS.txt on the release. In PowerShell:")
    lines.append(POWERSHELL_HASH)
    lines.append("")
    lines.append(
        "You can also build the program from the published source instead of running the downloaded exe."
    )
    if tag and TAG_RE.fullmatch(tag):
        lines.append("This copy was built as release %s." % tag)
    else:
        lines.append(
            "This copy has no release tag embedded. It was not produced by the release workflow, "
            "so there is no published checksum to select automatically."
        )
    return "\n".join(lines)


def verification_plan(copy: LocalCopy, tag: str, check_online: bool) -> str:
    """Decide whether a verify action may download SHA256SUMS.txt.

    ``local_only`` is the first-run notice: hash the local file, do not
    connect. The other values are reported as could-not-check, except
    ``download``, which is the menu action when a tag and a hash exist.
    """

    if not check_online:
        return "local_only"
    if copy.kind != "executable":
        return "source"
    if not copy.sha256:
        return "unreadable"
    if not tag or not TAG_RE.fullmatch(tag):
        return "no_tag"
    return "download"


def status_for_plan(plan: str, tag: str) -> str:
    """Sentence for a plan that does not download, or the in-progress line."""

    if plan == "local_only":
        return "Use Help → Verify this copy to compare this hash with the published checksum."
    if plan == "source":
        return "Running from source. There is no published executable to compare."
    if plan == "unreadable":
        return "Could not check. The running executable could not be read."
    if plan == "no_tag":
        return (
            "Could not check. This copy has no release tag embedded, "
            "so SHA256SUMS.txt was not downloaded."
        )
    if plan == "download":
        return "Downloading SHA256SUMS.txt for %s..." % tag
    raise ValueError("unknown verification plan %r" % plan)


def _bounded_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number of seconds")
    if timeout <= 0 or timeout > 30:
        raise ValueError("timeout must be between 0 and 30 seconds")
    return float(timeout)


def _fetch_sums(tag: str, timeout: float, urlopen) -> str:
    request = urllib.request.Request(
        sums_url(tag),
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
    )
    with urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "code", 200)
        if int(status) != 200:
            raise OSError("HTTP %s" % status)
        chunks = []
        remaining = MAX_SUMS_BYTES + 1
        while remaining > 0:
            block = response.read(remaining)
            if not isinstance(block, (bytes, bytearray)):
                raise OSError("checksum response was not bytes")
            if not block:
                break
            chunks.append(bytes(block))
            remaining -= len(block)
        data = b"".join(chunks)
    if len(data) > MAX_SUMS_BYTES:
        raise OSError("checksum file is too large")
    return data.decode("utf-8", errors="replace")


def verify_release(
    tag: str,
    local_hash: str | None,
    timeout: float = DEFAULT_TIMEOUT,
    urlopen=None,
) -> VerifyResult:
    """Download SHA256SUMS.txt for ``tag`` and compare it with ``local_hash``.

    Network failures, a missing file, and a file without this exe's hash all
    come back as ``could_not_check``. The timeout is required and capped.
    """

    seconds = _bounded_timeout(timeout)
    if not local_hash or not HASH_RE.fullmatch(local_hash):
        return VerifyResult(
            "could_not_check",
            local_hash,
            None,
            "Could not check. This copy has no SHA-256 to compare.",
        )
    if not TAG_RE.fullmatch(tag or ""):
        return VerifyResult(
            "could_not_check",
            local_hash,
            None,
            "Could not check. This copy has no release tag, so SHA256SUMS.txt was not downloaded.",
        )
    opener = urllib.request.urlopen if urlopen is None else urlopen
    try:
        body = _fetch_sums(tag, seconds, opener)
    except Exception:
        return VerifyResult(
            "could_not_check",
            local_hash,
            None,
            "Could not check. SHA256SUMS.txt for %s could not be downloaded. "
            "Compare the SHA-256 above with the release yourself." % tag,
        )
    remote = parse_sha256sums(body)
    if remote is None:
        return VerifyResult(
            "could_not_check",
            local_hash,
            None,
            "Could not check. SHA256SUMS.txt for %s did not contain a hash for %s."
            % (tag, EXE_NAME),
        )
    if hashes_match(local_hash, remote):
        return VerifyResult(
            "match",
            local_hash.lower(),
            remote,
            "Match. This file matches SHA256SUMS.txt for %s." % tag,
        )
    return VerifyResult(
        "mismatch",
        local_hash.lower(),
        remote,
        "Mismatch. This file is %s. SHA256SUMS.txt for %s publishes %s."
        % (local_hash.lower(), tag, remote),
    )


def embedded_build_info_source(tag: str, commit: str) -> str:
    """Python source that records the tag and commit inside the frozen exe."""

    tag = tag.strip()
    commit = commit.strip().lower()
    if not TAG_RE.fullmatch(tag):
        raise ValueError("DFLASH_RELEASE_TAG is not a version tag")
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("DFLASH_RELEASE_COMMIT is not a 40-character commit")
    return (
        '"""Identity of a published build. Written by the release workflow."""\n'
        "RELEASE_TAG = %r\n"
        "COMMIT = %r\n" % (tag, commit)
    )


def write_build_info(path: Path, tag: str, commit: str) -> None:
    """Overwrite ``build_info.py`` and drop a stale bytecode cache."""

    source = embedded_build_info_source(tag, commit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8", newline="\n")
    cache = path.parent / "__pycache__"
    if cache.is_dir():
        for stale in cache.glob(path.stem + "*.pyc"):
            stale.unlink()
    namespace: dict = {}
    exec(compile(source, str(path), "exec"), namespace)
    if namespace.get("RELEASE_TAG") != tag.strip() or namespace.get("COMMIT") != commit.strip().lower():
        raise RuntimeError("embedded build info did not round-trip")


def verify_section(
    sha256: str,
    tag: str,
    commit: str,
    repository: str,
    run_id: str,
    server: str = "https://github.com",
) -> str:
    """The "Verify your download" section prepended to release notes."""

    digest = sha256.strip().lower()
    commit = commit.strip().lower()
    server = server.rstrip("/")
    if not HASH_RE.fullmatch(digest):
        raise ValueError("release notes need a SHA-256")
    if not TAG_RE.fullmatch(tag):
        raise ValueError("release notes need a version tag")
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("release notes need a commit")
    if not REPO_RE.fullmatch(repository):
        raise ValueError("release notes need an owner/name repository")
    if not RUN_RE.fullmatch(run_id):
        raise ValueError("release notes need an Actions run id")
    if not SERVER_RE.fullmatch(server):
        raise ValueError("release notes need an https server")
    tag_url = "%s/%s/tree/%s" % (server, repository, tag)
    commit_url = "%s/%s/commit/%s" % (server, repository, commit)
    run_url = "%s/%s/actions/runs/%s" % (server, repository, run_id)
    sums = "%s/%s/releases/download/%s/%s" % (server, repository, tag, SUMS_NAME)
    lines = [
        "## Verify your download",
        "",
        "`%s` is an unsigned open-source build. Windows SmartScreen may show "
        '"Windows protected your PC" the first time you open it, because the file '
        "has no publisher signature. That warning is expected. Click **More info**, "
        "then **Run anyway**, only after you have verified the download." % EXE_NAME,
        "",
        "SHA-256: `%s`" % digest,
        "",
        "```",
        digest,
        "```",
        "",
        "In PowerShell, from the folder that contains the exe:",
        "",
        "```powershell",
        POWERSHELL_HASH,
        "```",
        "",
        "Compare that hash with [SHA256SUMS.txt](%s). The file is lowercase hex, "
        "two spaces, then `%s`. The hash in this note and the hash in that file "
        "must be the same." % (sums, EXE_NAME),
        "",
        "This executable was built from tag [%s](%s) at commit [%s](%s). "
        "The Actions run that built it is [%s](%s)."
        % (tag, tag_url, commit, commit_url, run_id, run_url),
        "",
        "You can build from that tag instead of running the downloaded exe. "
        'See "Verifying downloads / Windows SmartScreen" in the README.',
    ]
    return "\n".join(lines)


def free_software_section() -> str:
    """Callout placed at the top of the release notes and mirrored in the README."""

    return "## This software is free\n\n" + FREE_SOFTWARE_NOTICE


def compose_release_notes(
    generated: str,
    sha256: str,
    tag: str,
    commit: str,
    repository: str,
    run_id: str,
    server: str = "https://github.com",
) -> str:
    """Put the free-software callout and the verify section ahead of GitHub's notes."""

    free = free_software_section()
    section = verify_section(sha256, tag, commit, repository, run_id, server)
    body = generated.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        body = "Release %s." % tag
    return free + "\n\n" + section + "\n\n" + body + "\n"
