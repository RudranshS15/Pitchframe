"""Which language model is actually answering, and why.

The system can be driven by any of four tiers, tried in order of preference. The
ordering is a product decision, not an accident:

============ ====================================== ==========================
Tier         What it is                             Why it sits here
============ ====================================== ==========================
``foundry``  Azure AI Foundry, the intended path    The hero technology
``local``    Foundry Local, models on-device        Still Foundry, no Azure needed
``ollama``   A local Ollama server                  Last resort with a real model
``offline``  Scripted replies, no model at all      Keeps the demo runnable
============ ====================================== ==========================

The last tier is not a degraded failure mode to be hidden. It is what lets a
judge clone the repository and see the pipeline work without an Azure
subscription, which the rules require.

**The honesty rule.** Every fallback is reported, never silent. :class:`Selection`
carries the tier that was chosen *and* the reason it was chosen, so the overlay can
say "running on local model — Foundry unreachable" instead of quietly showing
scripted text as though a model produced it. A viewer must never be able to
mistake a scripted reply for an inference.

This module is standard library only. It decides *which* tier is live; building the
client for a tier is the caller's job, because that is where the third-party
imports live and the core must not depend on them.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "TIERS",
    "Tier",
    "Selection",
    "available_tiers",
    "select_tier",
    "describe_tiers",
    "DEFAULT_PREFERENCE",
]

#: Preference order, best first. ``offline`` is always last because it needs
#: nothing and would otherwise win every time.
DEFAULT_PREFERENCE: tuple[str, ...] = ("foundry", "local", "ollama", "offline")


@dataclass(frozen=True, slots=True)
class Tier:
    """One way of getting text out of a model."""

    key: str
    label: str
    requires: str
    uses_a_model: bool


#: Every tier the system knows about, in preference order.
TIERS: tuple[Tier, ...] = (
    Tier(
        key="foundry",
        label="Azure AI Foundry",
        requires="AZURE_AI_PROJECT_ENDPOINT set and `az login` completed",
        uses_a_model=True,
    ),
    Tier(
        key="local",
        label="Foundry Local (on-device)",
        requires="the foundry-local runtime installed and a model downloaded",
        uses_a_model=True,
    ),
    Tier(
        key="ollama",
        label="Ollama (local server)",
        requires="an Ollama server reachable at OLLAMA_HOST",
        uses_a_model=True,
    ),
    Tier(
        key="offline",
        label="Scripted (no model)",
        requires="nothing",
        uses_a_model=False,
    ),
)

_BY_KEY: dict[str, Tier] = {tier.key: tier for tier in TIERS}

#: Environment variables that indicate a Foundry project is configured.
_FOUNDRY_ENV_VARS: tuple[str, ...] = (
    "AZURE_AI_PROJECT_ENDPOINT",
    "FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_AI_FOUNDRY_ENDPOINT",
)

#: Distribution name to import name, for the packages we probe for.
#: ``foundry_local_sdk`` is probed by its distribution's import of the same name;
#: a miss here simply means the tier is skipped, never an error.
_PROBE_MODULES: Mapping[str, str] = {
    "agent-framework": "agent_framework",
    "azure-identity": "azure.identity",
    "ollama": "ollama",
}


@dataclass(frozen=True, slots=True)
class Selection:
    """The tier that was chosen, and the honest reason it was chosen."""

    tier: Tier
    reason: str
    #: True when the chosen tier is not the most-preferred available one.
    is_fallback: bool

    @property
    def key(self) -> str:
        return self.tier.key

    @property
    def uses_a_model(self) -> bool:
        """Whether a real model produced the text, or a script did."""
        return self.tier.uses_a_model

    def banner(self) -> str:
        """A short line for the overlay, in plain language.

        Deliberately blunt: this text is what stops a scripted reply being
        mistaken for an inference.
        """
        if self.tier.uses_a_model:
            base = f"running on {self.tier.label.lower()}"
        else:
            base = "scripted replies — no model in use"
        if self.is_fallback:
            # Stated separately from the tier name so the two facts compose: the
            # overlay can show that no model is in use AND that a better tier was
            # expected. Collapsing them would hide one of the two.
            base += " — preferred tier unavailable"
        return base


def _default_find_module(name: str) -> bool:
    """Whether an importable module is present, without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # A module can raise from find_spec when a parent package is missing, and
        # malformed specs raise ValueError. Neither should crash tier selection.
        return False


def available_tiers(
    *,
    env: Mapping[str, str] | None = None,
    find_module: Callable[[str], bool] | None = None,
) -> tuple[str, ...]:
    """Tiers whose preconditions appear to be met, in preference order.

    This checks *preconditions*, not liveness: it confirms a Foundry endpoint is
    configured and the client libraries are installed, but it does not call Azure
    to prove the credential works. That confirmation happens once, when the client
    is built, and a failure there is what drives the fallback. Keeping the probe
    offline means tier selection can never hang on a network call.

    Args:
        env: environment to read; defaults to :data:`os.environ`.
        find_module: module probe; defaults to :func:`_default_find_module`. Injectable so
            tests can describe a machine without pretending to be one.
    """
    env = os.environ if env is None else env
    find_module = _default_find_module if find_module is None else find_module

    found: list[str] = []
    has_framework = find_module(_PROBE_MODULES["agent-framework"])

    if (
        has_framework
        and find_module(_PROBE_MODULES["azure-identity"])
        and any(env.get(name) for name in _FOUNDRY_ENV_VARS)
    ):
        found.append("foundry")

    # Foundry Local is detected by the framework rather than by configuration, so
    # it is only offered when the client class is actually importable.
    if has_framework and find_module("foundry_local_sdk"):
        found.append("local")

    if has_framework and find_module(_PROBE_MODULES["ollama"]) and env.get("OLLAMA_HOST"):
        found.append("ollama")

    # Always available: it needs nothing at all.
    found.append("offline")
    return tuple(found)


def select_tier(
    preference: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    find_module: Callable[[str], bool] | None = None,
) -> Selection:
    """Pick the best available tier, with a reason a human can read.

    Args:
        preference: override the order; defaults to :data:`DEFAULT_PREFERENCE`.
            Unknown keys are ignored rather than fatal, so a typo in a config file
            degrades to the default instead of refusing to start.
        env: environment to read; defaults to :data:`os.environ`.
        find_module: module probe, injectable for tests.

    Raises:
        RuntimeError: if ``preference`` names no known tier. That is a
            misconfiguration, and guessing a default would hide it.
    """
    order = tuple(preference) if preference is not None else DEFAULT_PREFERENCE
    known = [key for key in order if key in _BY_KEY]
    if not known:
        raise RuntimeError(
            f"no known provider tiers in preference {list(order)!r}; "
            f"known tiers are: {', '.join(_BY_KEY)}"
        )

    available = available_tiers(env=env, find_module=find_module)
    available_set = set(available)

    for index, key in enumerate(known):
        if key in available_set:
            tier = _BY_KEY[key]
            skipped = [k for k in known[:index] if k not in available_set]
            if skipped:
                missed = ", ".join(_BY_KEY[k].label for k in skipped)
                reason = f"{tier.label} selected; unavailable: {missed}"
            else:
                reason = f"{tier.label} selected as the most preferred available tier"
            return Selection(tier=tier, reason=reason, is_fallback=bool(skipped))

    # Unreachable while "offline" is in TIERS and always available, but stated
    # rather than asserted so a future edit to available_tiers cannot silently
    # return nothing.
    raise RuntimeError(
        f"no available provider tier among {list(known)!r} "
        f"(detected: {', '.join(available) or 'none'})"
    )


def describe_tiers(
    *,
    env: Mapping[str, str] | None = None,
    find_module: Callable[[str], bool] | None = None,
) -> str:
    """A human-readable table of every tier and whether it is usable here.

    Used by the CLI's ``--providers`` flag, so the first question a new user asks
    ("what is this actually going to run on?") has a direct answer.
    """
    available = set(available_tiers(env=env, find_module=find_module))
    width = max(len(tier.label) for tier in TIERS)
    lines = []
    for tier in TIERS:
        mark = "ready  " if tier.key in available else "missing"
        lines.append(f"  [{mark}] {tier.label:<{width}}  needs: {tier.requires}")
    return "\n".join(lines)
