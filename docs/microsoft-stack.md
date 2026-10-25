# The Microsoft stack, in one paragraph and one table

Pitchframe's language-model layer is built on **Microsoft Agent Framework** (MAF,
`agent-framework` 1.21.0): the Narrator and the Verifier are real `Agent` objects driven
through MAF's `BaseChatClient` interface, and the reject-and-retry loop runs on
MAF's agent plumbing rather than a hand-rolled HTTP client. In production they talk to
**Microsoft Foundry** — a Foundry project (resource kind `AIServices` created through
`az cognitiveservices account`), a model **deployment** on that project, and the
`FoundryChatClient` that speaks the Foundry Agent Service protocol — authenticated with
**Microsoft Entra ID** credentials (`DefaultAzureCredential` / `az login`), so no API
key exists to leak, commit, or paste anywhere. The analytics and the verification remain
deterministic Python, served to the browser by a **FastAPI**/**uvicorn** app; the same
`BaseChatClient` seam also accepts local tiers — **Foundry Local** (on-device models) and
**Ollama** — and, when none is configured, a scripted client, with the tier that is live
reported honestly in the UI.

| Piece | pitchframe's usage | Where it lives |
|---|---|---|
| Microsoft Agent Framework (`agent-framework` 1.21.0) | `Agent`, `BaseChatClient`, `ChatResponse` plumbing behind the Narrator/Verifier agents | [pitchframe/agents/narrator.py](../pitchframe/agents/narrator.py), [pitchframe/agents/verifier.py](../pitchframe/agents/verifier.py) |
| Microsoft Foundry (`FoundryChatClient`, resource kind `AIServices`, model deployments) | the production chat client for both agents | [pitchframe/llm/builders.py](../pitchframe/llm/builders.py) |
| Microsoft Entra ID authentication (`DefaultAzureCredential`, `az login`) | the only credential anywhere in the project; no API key | [pitchframe/llm/builders.py](../pitchframe/llm/builders.py), [docs/foundry-runbook.md](foundry-runbook.md) |
| FastAPI + uvicorn | the overlay server that renders the replay | [pitchframe/overlay/server.py](../pitchframe/overlay/server.py) |
| Foundry Local (planned seam) / Ollama (`OllamaChatClient`) | the same agent objects on local tiers, no subscription needed | [pitchframe/llm/builders.py](../pitchframe/llm/builders.py) |
| Scripted client (`ScriptedChatClient`) | the offline tier: the full loop with a model-free replies, the one CI tests | [pitchframe/llm/offline.py](../pitchframe/llm/offline.py) |

One sentence to reuse verbatim in a submission form:

> Pitchframe builds its agent layer on **Microsoft Agent Framework**, runs its agents
> against **Microsoft Foundry** model deployments authenticated through **Microsoft
> Entra ID** (`DefaultAzureCredential` — no API keys), and serves the live overlay with
> **FastAPI**/**uvicorn**, with Foundry Local and Ollama as same-interface local
> fallbacks and a scripted client so the whole system runs — and is testable — without
> any cloud dependency at all.
