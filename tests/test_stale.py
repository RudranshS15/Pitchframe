"""Tests for the stale-server guard.

The guard exists because of one specific failure: a fix landed in the source, the suite
passed, and the overlay kept serving the stream its process had imported hours earlier
— so the person the fix was for saw none of it. A build-up panel drew the ball back to
where a shot had been struck, and was reported as broken when it was merely old.

What is tested here is therefore the guard's own premises: that the digest actually
moves when the code does, that it stays still when nothing relevant changed, and that
the message it produces is advice somebody could act on. The server's refusal is
asserted in ``tests/test_overlay.py``, next to the rest of the HTTP contract.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pitchframe.overlay.stale import (
    RESTART_COMMAND,
    CodeState,
    code_state,
    source_digest,
)


class TheDigestMovesWithTheCode(unittest.TestCase):
    """A digest that never changes is worse than no digest at all."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _write(self, name: str, body: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_editing_a_module_changes_the_digest(self) -> None:
        self._write("mod.py", "x = 1\n")
        before = source_digest(self.root)
        self._write("mod.py", "x = 2\n")
        self.assertNotEqual(before, source_digest(self.root))

    def test_adding_or_removing_a_module_changes_the_digest(self) -> None:
        self._write("mod.py", "x = 1\n")
        before = source_digest(self.root)
        self._write("other.py", "y = 1\n")
        self.assertNotEqual(before, source_digest(self.root))
        (self.root / "other.py").unlink()
        self.assertEqual(before, source_digest(self.root))

    def test_renaming_a_module_counts_as_a_change(self) -> None:
        """The name is hashed too, because the import that loaded ``mod`` does not
        follow a rename to ``renamed`` — the loaded code and the file disagree."""
        self._write("mod.py", "x = 1\n")
        before = source_digest(self.root)
        (self.root / "mod.py").rename(self.root / "renamed.py")
        self.assertNotEqual(before, source_digest(self.root))

    def test_a_no_op_edit_leaves_the_digest_alone(self) -> None:
        """Content, not timestamp: saving a file without changing it is not a change."""
        self._write("mod.py", "x = 1\n")
        before = source_digest(self.root)
        (self.root / "mod.py").touch()
        self.assertEqual(before, source_digest(self.root))

    def test_only_python_sources_are_hashed(self) -> None:
        """Static assets are re-read on every request and cannot go stale; bytecode is
        not source. Hashing either would make a stale process out of a saved stylesheet,
        and an operator who learns to dismiss the warning has learned to ignore it."""
        self._write("mod.py", "x = 1\n")
        before = source_digest(self.root)
        self._write("__pycache__/mod.py", "whatever\n")
        self._write("overlay/static/app.js", "console.log(1);\n")
        self._write("overlay/static/styles.css", "body { color: red; }\n")
        self._write("notes.md", "# not source\n")
        self.assertEqual(before, source_digest(self.root))


class TheMessageSaysWhatChanged(unittest.TestCase):
    """Advice an operator can weigh, rather than a shrug."""

    def test_a_fresh_state_says_nothing(self) -> None:
        state = CodeState(loaded="abc", current="abc", changed=())
        self.assertFalse(state.is_stale)
        self.assertIsNone(state.message())

    def test_a_stale_state_names_the_files_and_the_fix(self) -> None:
        state = CodeState(
            loaded="abc", current="def", changed=("pitchframe/generator.py",)
        )
        self.assertTrue(state.is_stale)
        message = state.message() or ""
        self.assertIn("pitchframe/generator.py", message)
        self.assertIn(RESTART_COMMAND, message)

    def test_a_stale_state_with_no_names_still_says_something(self) -> None:
        """A blank message would be worse than none: the request fails and nobody is
        told why."""
        state = CodeState(loaded="abc", current="def", changed=())
        self.assertTrue(state.is_stale)
        self.assertIn(RESTART_COMMAND, state.message() or "")


class ThePackageIsConsistentWithItself(unittest.TestCase):
    """The guard's premise, checked where it is cheap to check it."""

    def test_the_package_is_not_stale_at_the_start_of_a_run(self) -> None:
        state = code_state()
        self.assertFalse(state.is_stale, state.message())
        self.assertEqual(state.changed, ())


if __name__ == "__main__":
    unittest.main()
