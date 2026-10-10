"""The optional language-model layer.

Everything in :mod:`pitchframe` outside this package is standard library only.
That is a deliberate boundary: the generator, the stats engine and the CLI run on
a bare Python install, so anyone can clone the repository and see the system work
before installing a single dependency.

This package is where the dependencies live, and it is never imported by the core.
:mod:`pitchframe.llm.provider` is standard library only and decides *which* tier
is live; :mod:`pitchframe.llm.offline` needs ``agent-framework`` and provides the
scripted client that unit tests and the no-Azure demo path both run on.

Nothing here is imported at package import time, so ``import pitchframe`` stays
free of third-party code no matter what is installed.
"""

from __future__ import annotations

__all__: list[str] = []
