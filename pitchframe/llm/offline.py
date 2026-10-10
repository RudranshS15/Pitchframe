"""A deterministic, offline stand-in for a language model.

This exists for three reasons, and the first one is schedule-critical:

1. **Quota is the critical path.** New Azure AI Foundry subscriptions routinely
   start at zero TPM, and a quota increase takes days to land. A client that
   answers from a script needs no network, no credentials and no quota, so the
   whole agent pipeline can be written and tested while that request waits in a
   queue somewhere.
2. **A judge cannot log into your Azure subscription.** The rules require the
   project to be runnable free of charge and without restriction. This tier is
   what makes the repository testable by anyone who clones it.
3. **Verification needs to be reproducible.** A real model's output is cached to
   make the demo video deterministic; scripting it makes the *tests* deterministic.

It doubles as the fault-injection harness. A script step may be:

* a ``str`` — returned verbatim as the assistant reply;
* a callable taking the prompt messages and returning a ``str``, for replies that
  must depend on what was asked;
* an exception instance, which is *raised* instead of replying.

That third form is the important one. Provider failures, malformed output and
mid-run faults can all be produced on demand without unplugging anything or
touching a real service, so every branch of the retry and fallback logic is
reachable in a test and each test states which branch it exercises.

The sequence is explicit and the client records every prompt it was given, so a
test can assert on what the agent actually asked as well as what it answered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Union

from agent_framework import BaseChatClient, ChatResponse, FunctionInvocationLayer, Message

__all__ = ["ScriptStep", "ScriptExhausted", "ScriptedChatClient"]


#: One instruction to the scripted client: reply with this text, compute a reply
#: from the prompt, or raise this exception.
ScriptStep = Union[str, Callable[[Sequence[Message]], str], BaseException]


class ScriptExhausted(RuntimeError):
    """Raised when the script has no steps left and the agent asks again.

    This is a *bug in the test*, not a modelled failure mode: a script should
    either cover the number of turns the agent takes or opt into repeat-last. It
    is raised loudly rather than silently replying with something plausible,
    because a silent fallback would let a test pass while exercising the wrong
    path — the exact failure this harness exists to prevent.
    """


def _message_text(message: Message) -> str:
    """Best-effort plain text for a message, across MAF versions.

    ``Message.text`` is the documented convenience accessor, but prompts are
    assembled from mixed content in some code paths, so this falls back to the
    raw contents rather than raising in the middle of a test.
    """
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    contents = getattr(message, "contents", None) or ()
    parts: list[str] = []
    for part in contents:
        part_text = getattr(part, "text", None)
        parts.append(part_text if isinstance(part_text, str) else str(part))
    return "".join(parts)


class ScriptedChatClient(FunctionInvocationLayer, BaseChatClient):
    """A chat client that answers from a fixed script instead of a model.

    Inherits :class:`FunctionInvocationLayer` so that tool-calling agents behave
    the same way against this client as against a real one. Without it, MAF warns
    that the client "does not support function invoking" and the agent runs with
    reduced capability — which would make offline tests unrepresentative of the
    real pipeline.

    Args:
        script: the steps to replay, in order. See :data:`ScriptStep`.
        script_name: a label used in :meth:`describe`, so logs and the overlay can
            tell which scripted client produced a reply.
        repeat_last: when the script runs out, replay the final step instead of
            raising. Useful for long agent loops where only the first reply is
            interesting, and for ``always``.
    """

    def __init__(
        self,
        script: Sequence[ScriptStep] = (),
        *,
        script_name: str = "offline",
        repeat_last: bool = False,
    ) -> None:
        super().__init__()
        self._script: list[ScriptStep] = list(script)
        self._script_name = script_name
        self._repeat_last = repeat_last
        self._last: ScriptStep | None = None
        #: Every prompt this client was given, in order.
        self.calls: list[tuple[Message, ...]] = []

    # --- construction helpers ------------------------------------------------

    @classmethod
    def always(cls, text: str, **kwargs: Any) -> ScriptedChatClient:
        """A client that replies with ``text`` to every request, forever.

        The default for tests that care about wiring rather than content.
        """
        kwargs.setdefault("script_name", "always")
        return cls([text], repeat_last=True, **kwargs)

    @classmethod
    def failing(cls, error: BaseException, **kwargs: Any) -> ScriptedChatClient:
        """A client that raises ``error`` on every request.

        Models a provider that is down, unauthenticated or rate-limited, so the
        fallback path can be exercised without breaking anything real.
        """
        kwargs.setdefault("script_name", "failing")
        return cls([error], repeat_last=True, **kwargs)

    # --- introspection -------------------------------------------------------

    @property
    def script_name(self) -> str:
        """Label for this client, for logs and overlays."""
        return self._script_name

    @property
    def call_count(self) -> int:
        """How many prompts have been received, including failed turns."""
        return len(self.calls)

    @property
    def prompts(self) -> list[str]:
        """The text of every prompt received, flattened and in order."""
        return ["\n".join(_message_text(m) for m in call) for call in self.calls]

    @property
    def remaining(self) -> int:
        """Script steps not yet consumed."""
        return len(self._script)

    def describe(self) -> str:
        """A one-line summary, used by logs and the overlay's honesty banner."""
        return (
            f"{self._script_name} scripted client "
            f"({self.call_count} call(s), {self.remaining} step(s) left)"
        )

    # --- the model interface -------------------------------------------------

    def _next_step(self) -> ScriptStep:
        """Pop the next script step, or explain why there isn't one."""
        if self._script:
            self._last = self._script.pop(0)
            return self._last
        if self._repeat_last and self._last is not None:
            return self._last
        raise ScriptExhausted(
            f"script {self._script_name!r} exhausted after {self.call_count} call(s); "
            "add more steps or pass repeat_last=True"
        )

    async def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> ChatResponse:
        """Answer one turn from the script.

        Raises:
            NotImplementedError: if ``stream`` is true. Token-level streaming is
                not modelled here on purpose — the overlay streams *verified
                claims* over SSE, not model tokens, so agents run non-streaming
                and their accepted output is streamed downstream. Claiming to
                stream while replaying a fixed string would be a lie in the logs.
            ScriptExhausted: if the script has no step left.
        """
        self.calls.append(tuple(messages))

        if stream:
            raise NotImplementedError(
                "ScriptedChatClient does not support token streaming; the overlay "
                "streams verified claims over SSE, not model tokens."
            )

        step = self._next_step()
        if isinstance(step, BaseException):
            raise step
        text = step(tuple(messages)) if callable(step) else step
        return ChatResponse(messages=Message("assistant", [text]))
