"""A server that has outlived the code it was started with.

The overlay's whole claim is that every number on screen came out of the event stream
rather than out of anybody's memory. That claim stops being true the moment the
process disagrees with the files it was started from: after an edit, the process keeps
answering from the modules it imported at startup, while the repository — and the test
suite, and anyone reading the source — describe something else. Nothing in the request
path notices. The response is simply old, and an old response is indistinguishable
from a current one.

This is not a hypothetical. A build-up panel was reported drawing the ball back to the
spot a shot had been struck from. The generator had been fixed, 331 tests passed, and
the server was still serving the stream it had imported hours earlier — so the fix was
invisible to the person it was made for. The panel was right; the process was old.

Static assets are deliberately not part of this. They are read from disk on every
request, so an edit to ``app.js`` is live the moment it is saved. Python is not: a
module is read once, at import, for as long as the process lives.

What a running process cannot do is see what it loaded. It can remember what the files
looked like when it loaded them and compare that with what they look like now, which
is what :func:`code_state` does. A mismatch does not prove the loaded code is wrong —
only that the two have diverged — and the honest response to that is to stop serving
rather than to serve a guess.

Only ``*.py`` files are hashed, and by content rather than timestamp, so a file touched
without being edited counts as unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "RESTART_COMMAND",
    "CodeState",
    "LOADED_DIGEST",
    "code_state",
    "source_digest",
]

#: What a stale process should tell its operator to do.
RESTART_COMMAND = "python -m pitchframe --overlay"


def _package_root() -> Path:
    """The ``pitchframe`` package: everything this process may have imported."""
    return Path(__file__).resolve().parents[1]


def _sources(root: Path) -> dict[str, bytes]:
    """Every Python file under ``root``, keyed by a portable relative path."""
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        # Normalised separators, so a digest taken on Windows matches one taken
        # anywhere else — otherwise the same checkout reads as changed code.
        found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _digest_of(sources: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(sources):
        digest.update(name.encode("utf-8"))
        digest.update(sources[name])
    return digest.hexdigest()


def source_digest(root: Path | None = None) -> str:
    """A digest of the package's source as it is on disk *now*.

    Args:
        root: which directory to read. Defaults to the installed package; a test
            passes a temporary directory so it can prove the digest moves when the
            code does, without editing the real thing.
    """
    return _digest_of(_sources(_package_root() if root is None else root))


@dataclass(frozen=True, slots=True)
class CodeState:
    """What this process started from, beside what the files say now."""

    #: Digest of the source at import, which is what is loaded.
    loaded: str
    #: Digest of the source on disk at this moment.
    current: str
    #: Relative paths that differ, added or removed. Empty when nothing changed.
    changed: tuple[str, ...]

    @property
    def is_stale(self) -> bool:
        """True when the files have moved on since this process imported them."""
        return self.loaded != self.current

    def message(self) -> str | None:
        """What to tell an operator, or ``None`` when there is nothing to say.

        The names are included because "restart the server" is advice the reader can
        only weigh if they can see what changed.
        """
        if not self.is_stale:
            return None
        names = ", ".join(self.changed) or "the package source"
        return (
            f"This server is running code older than the files on disk ({names}). "
            f"Restart it: {RESTART_COMMAND}"
        )


#: What the source looked like when this module was imported, which is what this
#: process loaded. Every module the server can reach was imported in the same startup
#: that imported this one.
_LOADED = _sources(_package_root())
LOADED_DIGEST = _digest_of(_LOADED)


def code_state() -> CodeState:
    """Compare the loaded source with the source on disk."""
    now = _sources(_package_root())
    changed = tuple(
        sorted(
            name
            for name in set(_LOADED) | set(now)
            if _LOADED.get(name) != now.get(name)
        )
    )
    return CodeState(loaded=LOADED_DIGEST, current=_digest_of(now), changed=changed)
