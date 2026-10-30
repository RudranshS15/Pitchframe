"""Test package for Pitchframe.

The suite runs against an empty download cache, not the one a developer has filled while
trying the real-match path. Without this, a single fetched match silently changes the
catalogue's size and every test that walks it — and the failure would look like a count
that drifted rather than like state outside the test. Tests that need cached data build
their own directory and point ``PITCHFRAME_DATA_DIR`` at it.
"""

from __future__ import annotations

import os
import tempfile

os.environ["PITCHFRAME_DATA_DIR"] = tempfile.mkdtemp(prefix="pitchframe-tests-")
