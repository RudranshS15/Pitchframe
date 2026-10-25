# The overlay's container image, for Azure Container Apps.
#
# Multi-stage: the builder installs the wheels from requirements.txt against the same
# Python the final stage runs, so the final image carries no pip cache and no build
# tools. The core of Pitchframe is standard library only; the overlay needs fastapi,
# uvicorn and the agent runtime, and the Foundry/Ollama tiers stay configured by
# environment, so a container with no Azure settings runs the honest offline tier and
# the provider banner says exactly that.
#
# Python is pinned to 3.13-slim for the reason requirements.txt pins it: 3.14 does not
# yet have wheels for the whole agent dependency chain.

FROM python:3.13-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

FROM python:3.13-slim

# uvicorn --factory resolves the app object itself; nothing here is copied by hand.
WORKDIR /app

COPY --from=builder /install /usr/local
COPY pitchframe ./pitchframe

ENV PORT=8000 \
    PYTHONUNBUFFERED=1

# The health endpoint the harness already drives (/api/health) is what the hosted
# check polls, so the container exposes the same path the local run does.
EXPOSE 8000

CMD ["sh", "-c", "uvicorn pitchframe.overlay.server:create_app --factory --host 0.0.0.0 --port $PORT"]
