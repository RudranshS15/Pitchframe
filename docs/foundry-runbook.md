# Day-zero runbook: Microsoft Foundry

**Do this before writing another line of agent code.** Not because it takes long to
run — it takes about twenty minutes — but because one step in it has a queue you do
not control, and everything else in the project is scoped against it.

> **Naming.** The product was renamed **Microsoft Foundry** in November 2025.
> "Azure AI Foundry" is the older name and still appears throughout older docs,
> tutorials and search results. The ARM resource kind is still `AIServices`, which
> looks like legacy but is the correct, current value. Do not follow instructions
> that tell you to create a *hub* — that is Foundry (classic), a different workflow.

---

## The critical path

| Step | Time | Blocking? |
|---|---|---|
| 1–4: create resource, project, deploy a model | ~20 min | No |
| **5: request a TPM quota increase** | **unknown, days** | **Yes — start it first** |
| 6: point the project at the endpoint | 2 min | No |

New Azure subscriptions routinely ship with **0 tokens-per-minute quota** for the
models you actually want. Until that number is raised, a deployment can exist and
still refuse every request. Microsoft does not publish an approval SLA; it states
that requests are processed in receipt order with priority given to customers
already consuming existing quota. The widely repeated "3–5 business days" figure is
anecdote, not a commitment — treat it as the optimistic case.

**Practical consequence:** file the quota request in step 5 on the same day you
create the resource, before deploying anything else. If you are on a deadline
measured in days, that request is the schedule.

---

## Prerequisites

```bash
az --version          # must be 2.80.0 or newer for the project commands
az login
az account set --subscription "<subscription-id>"
```

`az cognitiveservices` is part of the **core** CLI. No extension install is needed —
if a tutorial tells you to `az extension add --name ml`, you are reading the Foundry
(classic) hub workflow, which is not this.

Python dependencies for the project itself:

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows
.venv/bin/python     -m pip install -r requirements.txt     # macOS / Linux
```

---

## Step 1 — resource group

```bash
az group create --name pitchframe-rg --location eastus
```

Pick a region where your intended model is available. This varies, and a deployment
can fail purely on region, so check step 3's `list-models` output for your region
before committing to one.

## Step 2 — the Foundry resource

```bash
az cognitiveservices account create \
  --name pitchframe-foundry \
  --resource-group pitchframe-rg \
  --kind AIServices \
  --sku S0 \
  --location eastus \
  --custom-domain pitchframe-foundry \
  --assign-identity \
  --allow-project-management true
```

Two of those flags are not optional garnish:

- `--assign-identity` — project creation requires a managed identity on the resource.
- `--allow-project-management true` — without it, step 3 fails.

`--custom-domain` must be globally unique. If it is taken, the command fails with a
name conflict even though the resource name itself was free.

## Step 3 — the project

```bash
az cognitiveservices account project create \
  --name pitchframe-foundry \
  --resource-group pitchframe-rg \
  --project-name pitchframe \
  --location eastus
```

Confirm it provisioned before moving on:

```bash
az cognitiveservices account project show \
  --name pitchframe-foundry \
  --resource-group pitchframe-rg \
  --project-name pitchframe \
  --query properties.provisioningState --output tsv
```

Expect `Succeeded`.

## Step 4 — deploy a model

**Do not copy a model tuple from a blog post.** Ask your own subscription and region
what is actually deployable, because the catalogue differs per subscription:

```bash
az cognitiveservices account list-models \
  --name pitchframe-foundry \
  --resource-group pitchframe-rg \
  --query "[].{name:name, format:format, version:version}" --output table
```

Then deploy using values from that output. The shape of the command:

```bash
az cognitiveservices account deployment create \
  --name pitchframe-foundry \
  --resource-group pitchframe-rg \
  --deployment-name pitchframe-chat \
  --model-name <name-from-list-models> \
  --model-version <version-from-list-models> \
  --model-format <format-from-list-models> \
  --sku-name GlobalStandard \
  --sku-capacity 1
```

Two things that trip people up:

- `--model-format` is **`OpenAI`** for Azure OpenAI models and **`Microsoft`** for
  first-party models such as Phi. Reading it from `list-models` avoids the guess.
- `--sku-name` is the *deployment type* (`GlobalStandard`, `Standard`, …), which is a
  different axis from the resource's `S0` tier. `--sku-capacity 1` requests the
  smallest allocation.

If the deployment is created but every request returns a quota error, that is step 5,
not a misconfiguration.

## Step 5 — request a TPM quota increase ⚠️

**This is the step with the queue. Do it now, not after the code works.**

In the Foundry portal:

1. Sign in and make sure the **New Foundry** toggle is on.
2. **Manage** → **Quota** → **Token per minute**.
3. **Request quota**, in the upper right.

You need **Owner** or **Contributor** at *subscription* scope to file the request.
The subscription, model, deployment type and region all have to match what you
deployed — a request against the wrong scope is approved and does nothing.

Two distinctions that cost people a week:

- **Request quota** raises the subscription ceiling. **Editing a deployment's
  allocation** only moves quota that already exists between deployments. If another
  deployment has unused TPM, rebalancing is instant and may unblock you without a
  request at all — check that first.
- When a subscription genuinely has 0, no rebalancing is possible. The request is the
  only path.

Microsoft notes that quota is managed at different scopes per model (region, global,
or data zone), shown in the Quota page's **Scope** column. A request at the wrong
scope is silently ineffective.

---

## Step 6 — point the project at it

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://pitchframe-foundry.services.ai.azure.com/api/projects/pitchframe"
export AZURE_AI_MODEL_DEPLOYMENT="pitchframe-chat"
```

Copy `.env.example` to `.env` and fill in the same two values. **No API key goes
anywhere.** Authentication is `AzureCliCredential`, which reads the identity already
established by `az login` — which means there is no key to commit, paste into a chat
window, or scrape from a log.

> Microsoft's own samples are inconsistent about the endpoint variable name: current
> Foundry examples use `AZURE_AI_PROJECT_ENDPOINT`, the Azure AI Projects library
> overview uses `FOUNDRY_PROJECT_ENDPOINT`. This project reads either.

Verify the tier is detected before debugging anything else:

```bash
python -m pitchframe --providers
```

---

## The honest fallbacks

The project runs without any of this. That is deliberate, and not a degraded mode to
be embarrassed about:

- `python -m pitchframe --seed 7` — the core system, standard library only.
- `python -m pitchframe --claims --seed 7` — the full agent pipeline, offline. Real
  Narrator and Verifier agents, scripted replies instead of a model.
- Foundry Local — `FoundryLocalClient` ships in the same package as
  `FoundryChatClient`. Models on-device, no Azure subscription and no quota.
- Ollama — a local server, if one is reachable.

Tier selection reports which one is live and **says so out loud**, so a scripted
reply can never be mistaken for an inference.

---

## Deadlines, in IST

The hackathon runs on Pacific Time. Your clock is IST. These are the conversions that
actually matter:

| Event | Pacific | IST |
|---|---|---|
| Registration closes | Oct 20, 12:00 pm PT | **Oct 21, ~00:30 IST** |
| Submission closes | Oct 27, 11:59 pm PT | **Oct 28, ~12:29 IST** |

Your effective deadline is **midday on Oct 28**, not the 27th. Register this week
regardless of build progress — registration is not blocked by any of the above.

---

## Submission checklist

- [ ] Repository **public**, and the first commit after the registration period opened
- [ ] Pitch and description written
- [ ] Demo video, public, **under two minutes**
- [ ] Working access link for judges
- [ ] Everything in English
- [ ] Nothing obscene, defamatory, or promoting alcohol, drugs, tobacco or politics
- [ ] `python -m pitchframe --seed 7` works on a **clean machine** with no installs

That last one is the one most likely to fail, because it is the one nobody tests on a
machine other than their own.
