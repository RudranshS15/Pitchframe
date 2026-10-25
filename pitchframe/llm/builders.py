"""Building the live agent pair for the tier the selector chose.

:mod:`pitchframe.llm.provider` decides *which* tier is live; this module builds the
	real client for it, and it is the only place that imports the third-party pieces. Both
	builders import inside their function bodies, so an interpreter with nothing installed
	still runs the whole overlay on the scripted tier — losing an agent runtime must not
	break a demo that never talked to a model in the first place.

The honesty rule the selection layer states applies twice as hard here: a tier whose
client cannot be built is *unavailable*, not degraded. Every failure path raises
:class:`ClientUnavailable` with a message that names the missing thing, and the caller
(network layer or CLI) turns that message into the provider banner rather than trying
to guess at a model name.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ClientUnavailable", "LivePair", "build_live_pair"]


class ClientUnavailable(RuntimeError):
	"""The selected tier cannot be built; ``str()`` names the missing thing."""


@dataclass(frozen=True, slots=True)
class LivePair:
	"""A :class:`~pitchframe.agents.narrator.NarratorAgent` and
	:class:`~pitchframe.agents.verifier.VerifierAgent` wired to one real model client,
	plus the deployment name the pair will mention in the banner."""

	narrator: Any
	verifier: Any
	model: str


def _foundry_pair(env: Mapping[str, str]) -> tuple[Any, str]:
	"""Build the Foundry Agents pair.

	Args:
		env: the environment to read endpoint and deployment from.

	Returns:
		An ``agent_framework.Agent`` on a ``FoundryChatClient`` and the model name.

	Raises:
		ClientUnavailable: which of the two settings is missing, or why the SDK
			refused to build the client (auth, endpoint shape, ...).
	"""
	endpoint = (
		env.get("AZURE_AI_PROJECT_ENDPOINT")
		or env.get("FOUNDRY_PROJECT_ENDPOINT")
	)
	if not endpoint:
		raise ClientUnavailable(
			"AZURE_AI_PROJECT_ENDPOINT (or FOUNDRY_PROJECT_ENDPOINT) is not set"
		)
	model = (
		env.get("AZURE_AI_MODEL_DEPLOYMENT")
		or env.get("FOUNDRY_MODEL")
		or env.get("AZURE_AI_MODEL")
	)
	if not model:
		raise ClientUnavailable(
			"AZURE_AI_MODEL_DEPLOYMENT is not set; set it to the *deployment* name "
			"of the model, which is not always the model's own name"
		)
	from agent_framework.foundry import FoundryChatClient
	from azure.identity import DefaultAzureCredential

	try:
		client = FoundryChatClient(
			project_endpoint=endpoint,
			model=model,
			credential=DefaultAzureCredential(),
		)
	except Exception as exc:  # auth, endpoint shape, SDK drift — say so, do not guess
		raise ClientUnavailable(
			f"Foundry client for {model!r} could not be built: {type(exc).__name__}: {exc}"
		) from exc
	return client, model


def _ollama_pair(env: Mapping[str, str]) -> tuple[Any, str]:
	"""Build the Ollama pair (``agent-framework-ollama`` extra).

	Same contract as :func:`_foundry_pair`; ``OLLAMA_HOST`` is the base URL of the
	local Ollama server (``http://127.0.0.1:11434`` by default in Ollama itself) and
	``OLLAMA_MODEL_ID`` is the tag of a model already pulled there.
	"""
	host = env.get("OLLAMA_HOST")
	if not host:
		raise ClientUnavailable("OLLAMA_HOST is not set")
	model = env.get("OLLAMA_MODEL_ID")
	if not model:
		raise ClientUnavailable(
			"OLLAMA_MODEL_ID is not set; set it to a model tag already pulled into Ollama"
		)
	from agent_framework.ollama import OllamaChatClient

	try:
		client = OllamaChatClient(host=host, model=model)
	except Exception as exc:
		raise ClientUnavailable(
			f"Ollama client for {model!r} could not be built: {type(exc).__name__}: {exc}"
		) from exc
	return client, model


#: Builders for the tiers that have a live client path. ``"local"`` (Foundry Local,
#: on-device) is described by :func:`pitchframe.llm.provider.describe_tiers` as a next
#: step, not shipped: there is no client path for it in this build, and selecting it
#: falls back with that note rather than a guess.
_PAIR_BUILDERS: dict[str, Callable[[Mapping[str, str]], tuple[Any, str]]] = {
	"foundry": _foundry_pair,
	"ollama": _ollama_pair,
}


def build_live_pair(tier_key: str, *, env: Mapping[str, str] | None = None) -> LivePair:
	"""Build the live Narrator/Verifier pair for ``tier_key``, or raise.

	Args:
		tier_key: the key the tier selector chose (``"foundry"``, ``"ollama"``, ...).
			Anything without an entry in ``_PAIR_BUILDERS`` — including ``"offline"``,
			the default — returns without raising, because the scripted tier is not a
			failure to report.
		env: environment to read settings from; ``os.environ`` when omitted. Injectable
			so tests can ask for a tier with known-missing settings.

	Returns:
		A :class:`LivePair`, or ``None`` when ``tier_key`` has no live path (the
		scripted tier keeps the pipeline it already has).

	Raises:
		ClientUnavailable: when the tier *should* have a live path but cannot be
			built — a missing setting, a missing SDK, or a client the SDK refused.
			``str()`` of the exception is written for a banner, not a stack trace.
	"""
	if tier_key not in _PAIR_BUILDERS:
		return None
	# The settings check comes first *deliberately*: it names the env var the operator
	# forgot, which is more actionable than anything the SDK could say later, and on a
	# machine that never installed agent-framework the settings message is the honest
	# difference between "you left something out" and "you installed nothing".
	try:
		client, model = _PAIR_BUILDERS[tier_key](os.environ if env is None else env)
	except ClientUnavailable:
		raise
	except ImportError as exc:
		missing = getattr(exc, "name", None) or str(exc)
		raise ClientUnavailable(
			f"the agent runtime is not installed ({missing}); "
			"pip install -r requirements.txt"
		) from exc
	except Exception as exc:  # noqa: BLE001 — the caller only needs the message
		raise ClientUnavailable(
			f"{tier_key} client could not be built: {type(exc).__name__}: {exc}"
		) from exc
	try:
		import agent_framework as af
		from ..agents.narrator import NarratorAgent
		from ..agents.verifier import VerifierAgent
	except ImportError as exc:
		missing = getattr(exc, "name", None) or str(exc)
		raise ClientUnavailable(
			f"the agent runtime is not installed ({missing}); "
			"pip install -r requirements.txt"
		) from exc
	return LivePair(
		narrator=NarratorAgent(af.Agent(client=client, name="pitchframe-narrator")),
		verifier=VerifierAgent(af.Agent(client=client, name="pitchframe-verifier")),
		model=model,
	)
