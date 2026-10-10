"""The agent layer: a Narrator, a Verifier, and the loop that connects them.

Split deliberately across the dependency boundary:

* :mod:`pitchframe.agents.pipeline` is **standard library only**. It holds the
  snapshot, the trace and the propose/verify/retry loop, and it talks to its two
  collaborators through protocols. The retry logic is the part most likely to be
  wrong and the part least likely to be tested if exercising it required a model,
  so it takes plain objects and runs anywhere.
* :mod:`pitchframe.agents.narrator` and :mod:`pitchframe.agents.verifier` are the
  model-backed implementations. They import ``agent-framework`` and are only usable
  once ``requirements.txt`` is installed.

Neither is imported here. Importing this package must stay free, so that
``from pitchframe.agents.pipeline import run_pipeline`` works on a bare interpreter
and the failure from a missing optional dependency points at the module that
actually needs it.
"""

from __future__ import annotations

__all__: list[str] = []
