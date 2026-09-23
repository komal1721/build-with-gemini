"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over the A2A protocol, returning replies as
structured parts the chat UI knows how to show:

  * {"kind": "text", "text": ...}  -> a normal chat bubble
  * {"kind": "a2ui", "data": ...}  -> one A2UI message (beginRendering /
    surfaceUpdate); static/index.html renders these as a card.
"""

import os
import uuid

import google.auth
import google.auth.transport.requests
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.protobuf.json_format import ParseDict

RESOURCE = os.environ.get(
    "AGENT_ENGINE_RESOURCE_NAME",
    "projects/qwiklabs-gcp-01-cec5bf0618af/locations/us-east1/reasoningEngines/8553367034283425792",
)
# The agent's app directory (matches agent_directory in agents-cli-manifest.yaml).
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
# Location is embedded in the resource name: projects/<p>/locations/<loc>/reasoningEngines/<id>.
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

# A2A endpoint for an Agent Runtime deployment, via the Agent Engine HTTP
# passthrough. The card lives at the well-known path under this base.
A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"

# The agent tags its A2UI data parts with this mime type.
_A2UI_MIME = "application/json+a2ui"

# One set of ADC credentials, refreshed per request (access tokens expire ~1h).
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)


def _auth_headers() -> dict[str, str]:
    _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


def _make_text_part(text: str) -> Part:
    try:
        from a2a.types import TextPart

        return Part(root=TextPart(text=text))
    except ImportError:
        return Part(text=text)


def _get_user_role():
    if hasattr(Role, "ROLE_USER"):
        return Role.ROLE_USER
    if hasattr(Role, "user"):
        return Role.user
    return getattr(Role, "ROLE_USER", "user")


app = FastAPI()


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    # Always return JSON so the browser never receives a plain-text 500 page
    # (which shows up in the chat as "Unexpected token 'I', "Internal S"... is
    # not valid JSON"). Any server-side failure now surfaces as a readable
    # message in the chat bubble instead.
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Reuse ONE A2A context per user so the agent remembers the conversation.
_contexts: dict[str, str] = {}
# Cache the agent card after the first fetch.
_card: AgentCard | None = None


async def _get_card(client: httpx.AsyncClient) -> AgentCard:
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        data = resp.json()
        try:
            card = AgentCard(**data)
        except Exception:
            card = AgentCard()
            ParseDict(data, card, ignore_unknown_fields=True)
        if hasattr(card, "url"):
            try:
                card.url = A2A_BASE
            except AttributeError:
                pass
        _card = card
    return _card


def _extract_parts(parts: list) -> list[dict]:
    """Turn A2A response parts into structured parts for the chat UI.

    Text parts pass through as {"kind": "text"}. A2UI data parts (tagged
    application/json+a2ui) become {"kind": "a2ui", "data": <message>} so the UI
    renders the card; each data part is one A2UI message (beginRendering or
    surfaceUpdate).
    """
    out: list[dict] = []
    for p in parts:
        root = getattr(p, "root", p)
        text = getattr(root, "text", None)
        if text:
            out.append({"kind": "text", "text": text})
            continue

        data = getattr(root, "data", None)
        if data is not None:
            meta = getattr(root, "metadata", None) or {}
            mime = (
                meta.get("mimeType")
                if isinstance(meta, dict)
                else getattr(root, "media_type", None)
            )
            if (
                mime == _A2UI_MIME
                or getattr(root, "media_type", None) == _A2UI_MIME
            ):
                out.append({"kind": "a2ui", "data": data})
            continue

        url = getattr(root, "url", None)
        if url:
            out.append({"kind": "text", "text": url})
            continue

        file_obj = getattr(root, "file", None)
        if file_obj and getattr(file_obj, "uri", None):
            out.append({"kind": "text", "text": file_obj.uri})
    return out


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    user_id = body.get("user_id") or "web-user"
    parts: list[dict] = []

    async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
        card = await _get_card(client)
        try:
            from a2a.types import TransportProtocol

            config = ClientConfig(
                supported_transports=[
                    TransportProtocol.jsonrpc,
                    TransportProtocol.http_json,
                ],
                httpx_client=client,
            )
        except ImportError:
            config = ClientConfig(httpx_client=client)

        factory = ClientFactory(config)
        a2a_client = factory.create(card)

        msg = Message(
            message_id=str(uuid.uuid4()),
            role=_get_user_role(),
            parts=[_make_text_part(message)],
            context_id=_contexts.get(user_id),
        )

        try:
            from a2a.types import SendMessageRequest

            send_req = SendMessageRequest(message=msg)
        except ImportError:
            send_req = msg

        last_task = None
        got_artifact_update = False
        async for event in a2a_client.send_message(send_req):
            if isinstance(event, tuple):
                task, update = event
                if task is not None:
                    last_task = task
                    if getattr(task, "context_id", None):
                        _contexts[user_id] = task.context_id
                if isinstance(update, TaskArtifactUpdateEvent):
                    got_artifact_update = True
                    parts.extend(_extract_parts(update.artifact.parts))
            elif hasattr(event, "HasField"):
                if event.HasField("task"):
                    last_task = event.task
                    if event.task.context_id:
                        _contexts[user_id] = event.task.context_id
                if event.HasField("artifact_update"):
                    got_artifact_update = True
                    parts.extend(
                        _extract_parts(event.artifact_update.artifact.parts)
                    )
                if event.HasField("message"):
                    parts.extend(_extract_parts(event.message.parts))
            else:
                ctx_id = getattr(event, "context_id", None)
                if ctx_id:
                    _contexts[user_id] = ctx_id
                art = getattr(event, "artifact", None)
                if art is not None:
                    got_artifact_update = True
                    parts.extend(_extract_parts(art.parts))

        # Non-streaming fallback: pull parts from the final task's artifacts.
        if not got_artifact_update and last_task is not None:
            task_artifacts = getattr(last_task, "artifacts", None) or []
            for artifact in task_artifacts:
                parts.extend(_extract_parts(artifact.parts))

    if not parts:
        # The turn produced no text or UI (e.g. the agent only ran tools, or a
        # tool stalled). Be honest rather than silent.
        parts = [{"kind": "text", "text": "(The agent didn't return a reply.)"}]
    return JSONResponse({"parts": parts})


# Serve the chat UI (keep this mount last so /chat wins).
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
