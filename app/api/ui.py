"""
Settings UI — server-rendered HTML with HTMX for inline editing.

Routes:
  GET    /ui/                                           — tenant list
  POST   /ui/tenants                                    — create tenant
  DELETE /ui/tenants/{id}                               — delete tenant
  GET    /ui/tenants/{id}                               — tenant settings page
  POST   /ui/tenants/{id}/name                          — rename tenant (HTMX partial)
  GET    /ui/tenants/{id}/name/edit                     — name edit form (HTMX partial)
  GET    /ui/tenants/{id}/event-types/{name}            — read-only row (HTMX partial)
  GET    /ui/tenants/{id}/event-types/{name}/edit       — edit form row (HTMX partial)
  POST   /ui/tenants/{id}/event-types/{name}            — save knowledge, return row
  POST   /ui/tenants/{id}/notifications                 — save SMS recipients
  POST   /ui/tenants/{id}/kafka                        — save Kafka settings
  GET    /ui/tenants/{id}/chat                          — web chat page
  POST   /ui/tenants/{id}/chat/message                  — send a chat message (HTMX partial)
"""
import datetime
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database.session import get_db
from app.llm.general_conversation import run_general_conversation, stream_general_conversation
from app.models.anomaly import Anomalies
from app.models.conversation import Conversations, Messages
from app.models.event import EventTypes
from app.models.insight import Insights
from app.models.tenant import Tenants
from app.models.tenant_kafka_settings import TenantKafkaSettings
from app.models.trend import Trends
from app.security.encryption import EncryptionConfigurationError, encrypt_secret
from app.workers.metric_worker import run_for_tenant

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="app/templates")


def _render(request: Request, template: str, context: dict[str, Any]) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)


def _get_tenant(db: Session, tenant_id: uuid.UUID) -> Tenants:
    tenant = db.query(Tenants).filter(Tenants.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def _get_event_type(db: Session, tenant_id: uuid.UUID, event_name: str) -> EventTypes:
    et = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id, EventTypes.event_name == event_name)
        .first()
    )
    if et is None:
        raise HTTPException(status_code=404, detail="Event type not found")
    return et


def _load_event_types(db: Session, tenant_id: uuid.UUID) -> list[EventTypes]:
    return (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id)
        .order_by(EventTypes.event_name)
        .all()
    )


def _load_dashboard_data(
    db: Session,
    tenant_id: uuid.UUID,
) -> tuple[list[Anomalies], list[Trends], list[Insights]]:
    anomalies = (
        db.query(Anomalies)
        .filter(Anomalies.tenant_id == tenant_id, Anomalies.resolved_at == None)  # noqa: E711
        .order_by(Anomalies.detected_at.desc())
        .limit(50)
        .all()
    )
    trends = (
        db.query(Trends)
        .filter(Trends.tenant_id == tenant_id, Trends.resolved_at == None)  # noqa: E711
        .order_by(Trends.detected_at.desc())
        .limit(50)
        .all()
    )
    insights = (
        db.query(Insights)
        .options(selectinload(Insights.anomaly), selectinload(Insights.trend))
        .filter(Insights.tenant_id == tenant_id)
        .order_by(Insights.created_at.desc())
        .limit(20)
        .all()
    )
    return anomalies, trends, insights


# ── Tenant list ───────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    tenants = db.query(Tenants).order_by(Tenants.name).all()
    return _render(request, "index.html", {"tenants": tenants})


# ── Tenant CRUD ───────────────────────────────────────────────────────────────

@router.post("/tenants", response_class=HTMLResponse)
def create_tenant(
    request: Request,
    name: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = Tenants(
        id=uuid.uuid4(),
        name=name.strip() or "My Tenant",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    response = _render(request, "partials/tenant_row.html", {"tenant": tenant})
    response.headers["HX-Trigger"] = "tenantCreated"
    return response


@router.delete("/tenants/{tenant_id}", response_class=HTMLResponse)
def delete_tenant(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    tenant = _get_tenant(db, tenant_id)
    db.delete(tenant)
    db.commit()
    return Response(status_code=200)


# ── Tenant settings page ──────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}", response_class=HTMLResponse)
def tenant_settings(
    request: Request,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    event_types = _load_event_types(db, tenant_id)
    kafka_settings = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .first()
    )
    return _render(request, "tenant.html", {
        "tenant": tenant,
        "event_types": event_types,
        "kafka_settings": kafka_settings,
        "kafka_consumer_group": f"{settings.kafka_consumer_group_prefix or 'ai-events'}-{tenant_id}",
        "kafka_global_configured": bool(settings.kafka_bootstrap_servers),
        "slack_configured": bool(settings.slack_bot_token and settings.slack_signing_secret),
        "twilio_configured": bool(
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_from_number
        ),
    })


# ── Tenant name inline edit (HTMX) ────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/name", response_class=HTMLResponse)
def tenant_name(
    request: Request,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    return _render(request, "partials/tenant_name.html", {"tenant": tenant})


@router.get("/tenants/{tenant_id}/name/edit", response_class=HTMLResponse)
def tenant_name_edit(
    request: Request,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    return _render(request, "partials/tenant_name_edit.html", {"tenant": tenant})


@router.post("/tenants/{tenant_id}/name", response_class=HTMLResponse)
def update_tenant_name(
    request: Request,
    tenant_id: uuid.UUID,
    name: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    tenant.name = name.strip() or tenant.name
    db.commit()
    db.refresh(tenant)
    return _render(request, "partials/tenant_name.html", {"tenant": tenant})


# ── Event type partials (HTMX) ────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/event-types/{event_name}", response_class=HTMLResponse)
def event_type_row(
    request: Request,
    tenant_id: uuid.UUID,
    event_name: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    et = _get_event_type(db, tenant_id, event_name)
    return _render(request, "partials/event_type_row.html", {
        "et": et, "tenant_id": tenant_id,
    })


@router.get("/tenants/{tenant_id}/event-types/{event_name}/edit", response_class=HTMLResponse)
def event_type_edit_row(
    request: Request,
    tenant_id: uuid.UUID,
    event_name: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    et = _get_event_type(db, tenant_id, event_name)
    return _render(request, "partials/event_type_edit.html", {
        "et": et, "tenant_id": tenant_id,
    })


@router.post("/tenants/{tenant_id}/event-types/{event_name}", response_class=HTMLResponse)
def update_event_type(
    request: Request,
    tenant_id: uuid.UUID,
    event_name: str,
    description: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    business_context: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    et = _get_event_type(db, tenant_id, event_name)

    et.description = description.strip() or None

    metadata: dict[str, Any] = {}
    if category.strip():
        metadata["category"] = category.strip()
    if business_context.strip():
        metadata["business_context"] = business_context.strip()
    # Preserve related_events set via the conversational agent
    if et.type_metadata and et.type_metadata.get("related_events"):
        metadata["related_events"] = et.type_metadata["related_events"]
    et.type_metadata = metadata or None

    db.commit()
    db.refresh(et)

    return _render(request, "partials/event_type_row.html", {
        "et": et, "tenant_id": tenant_id, "saved": True,
    })


# ── Pipeline scan (HTMX) ─────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/scan", response_class=HTMLResponse)
def scan_tenant(
    request: Request,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _get_tenant(db, tenant_id)
    result = run_for_tenant(db, tenant_id)
    anomalies, trends, insights = _load_dashboard_data(db, tenant_id)
    return _render(request, "partials/scan_result.html", {
        "result": result,
        "tenant_id": tenant_id,
        "event_types": _load_event_types(db, tenant_id),
        "anomalies": anomalies,
        "trends": trends,
        "insights": insights,
    })


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/dashboard", response_class=HTMLResponse)
def tenant_dashboard(
    request: Request,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    anomalies, trends, insights = _load_dashboard_data(db, tenant_id)

    return _render(request, "dashboard.html", {
        "tenant": tenant,
        "anomalies": anomalies,
        "trends": trends,
        "insights": insights,
    })


# ── Notification settings (HTMX) ─────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/notifications", response_class=HTMLResponse)
def update_notifications(
    request: Request,
    tenant_id: uuid.UUID,
    slack_channel: Annotated[str, Form()] = "",
    sms_recipients: Annotated[list[str], Form()] = [],
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    tenant.slack_channel = slack_channel.strip().lstrip("#") or None

    # Normalise numbers: strip whitespace/dashes/parens, prepend whatsapp: prefix.
    # Input values arrive as bare E.164 numbers (e.g. "+15551234567").
    cleaned: list[str] = []
    for raw in sms_recipients:
        # Strip formatting characters the user might type
        digits = "".join(c for c in raw if c in "+0123456789")
        if digits:
            cleaned.append(f"whatsapp:{digits}")
    tenant.sms_recipients = cleaned or None
    db.commit()

    return _render(request, "partials/notifications_saved.html", {
        "tenant": tenant,
    })


# ── Kafka settings (HTMX) ────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/kafka", response_class=HTMLResponse)
def update_kafka_settings(
    request: Request,
    tenant_id: uuid.UUID,
    bootstrap_servers: Annotated[str, Form()] = "",
    topic_include_pattern: Annotated[str, Form()] = "",
    topic_exclude_pattern: Annotated[str, Form()] = "^__",
    error_topic_pattern: Annotated[str, Form()] = r"\.errors?$",
    event_name_fields: Annotated[str, Form()] = "event_name, type, action, name",
    security_protocol: Annotated[str, Form()] = "",
    sasl_mechanism: Annotated[str, Form()] = "",
    sasl_username: Annotated[str, Form()] = "",
    sasl_password: Annotated[str, Form()] = "",
    clear_sasl_password: Annotated[str | None, Form()] = None,
    enabled: Annotated[str, Form()] = "on",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _get_tenant(db, tenant_id)
    now = datetime.datetime.now(datetime.UTC)
    fields = [f.strip() for f in event_name_fields.split(",") if f.strip()]

    kafka = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .first()
    )
    if kafka is None:
        kafka = TenantKafkaSettings(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
        db.add(kafka)

    kafka.bootstrap_servers = bootstrap_servers.strip() or None
    kafka.topic_include_pattern = topic_include_pattern.strip() or None
    kafka.topic_exclude_pattern = topic_exclude_pattern.strip() or "^__"
    kafka.error_topic_pattern = error_topic_pattern.strip() or r"\.errors?$"
    kafka.event_name_fields = fields or ["event_name", "type", "action", "name"]
    kafka.security_protocol = security_protocol.strip() or None
    kafka.sasl_mechanism = sasl_mechanism.strip() or None
    kafka.sasl_username = sasl_username.strip() or None
    if clear_sasl_password == "on":
        kafka.sasl_password_encrypted = None
        kafka.sasl_password_updated_at = None
    elif sasl_password:
        try:
            kafka.sasl_password_encrypted = encrypt_secret(sasl_password)
        except EncryptionConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kafka.sasl_password_updated_at = now
    kafka.enabled = enabled == "on"
    kafka.updated_at = now
    db.commit()

    return _render(request, "partials/kafka_settings_saved.html", {})


# ── Web chat ──────────────────────────────────────────────────────────────────

def _get_or_create_web_conversation(db: Session, tenant_id: uuid.UUID) -> Conversations:
    existing = (
        db.query(Conversations)
        .filter(Conversations.tenant_id == tenant_id, Conversations.channel == "web")
        .order_by(Conversations.created_at.desc())
        .first()
    )
    if existing:
        return existing
    conv = Conversations(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        insight_id=None,
        channel="web",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(conv)
    db.flush()
    return conv


@router.get("/tenants/{tenant_id}/chat", response_class=HTMLResponse)
def chat_page(
    request: Request,
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    conv = _get_or_create_web_conversation(db, tenant_id)
    messages = (
        db.query(Messages)
        .filter(Messages.conversation_id == conv.id)
        .order_by(Messages.created_at)
        .all()
    )
    return _render(request, "chat.html", {
        "tenant": tenant,
        "conversation_id": conv.id,
        "messages": messages,
        "ai_configured": settings.llm_configured,
    })


@router.post("/tenants/{tenant_id}/chat/message", response_class=HTMLResponse)
def chat_message(
    request: Request,
    tenant_id: uuid.UUID,
    message: Annotated[str, Form()] = "",
    conversation_id: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tenant = _get_tenant(db, tenant_id)
    text = message.strip()
    if not text:
        return HTMLResponse("")

    # Resolve conversation
    conv: Conversations | None = None
    if conversation_id:
        try:
            cid = uuid.UUID(conversation_id)
            conv = db.query(Conversations).filter(
                Conversations.id == cid,
                Conversations.tenant_id == tenant_id,
            ).first()
        except ValueError:
            pass
    if conv is None:
        conv = _get_or_create_web_conversation(db, tenant_id)

    # Persist user message
    now = datetime.datetime.now(datetime.UTC)
    db.add(Messages(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        sender="user",
        message=text,
        created_at=now,
    ))
    db.flush()

    if not settings.llm_configured:
        assistant_text = (
            "No LLM API key is configured. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your .env file to enable the chat assistant."
        )
    else:
        history = [
            {"role": m.sender, "content": m.message}
            for m in (
                db.query(Messages)
                .filter(
                    Messages.conversation_id == conv.id,
                    Messages.sender.in_(["user", "assistant"]),
                )
                .order_by(Messages.created_at)
                .all()
            )
            if m.sender and m.message
        ]
        # Remove the message we just added so we don't duplicate it in history
        if history and history[-1]["role"] == "user" and history[-1]["content"] == text:
            history = history[:-1]

        try:
            assistant_text = run_general_conversation(
                user_message=text,
                history=history,
                tenant_id=tenant_id,
                db=db,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Chat agent error")
            assistant_text = "Something went wrong while generating a response. Please try again."

    db.add(Messages(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        sender="assistant",
        message=assistant_text,
        created_at=datetime.datetime.now(datetime.UTC),
    ))
    db.commit()

    return _render(request, "partials/chat_exchange.html", {
        "tenant": tenant,
        "user_message": text,
        "assistant_message": assistant_text,
        "conversation_id": conv.id,
    })


@router.get("/tenants/{tenant_id}/chat/stream")
def chat_stream(
    tenant_id: uuid.UUID,
    message: str = Query(default=""),
    conversation_id: str = Query(default=""),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events endpoint for the web chat. Streams status + final response."""
    text = message.strip()
    if not text:
        def _empty() -> Any:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Empty message.'})}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    _get_tenant(db, tenant_id)

    conv: Conversations | None = None
    if conversation_id:
        try:
            cid = uuid.UUID(conversation_id)
            conv = db.query(Conversations).filter(
                Conversations.id == cid,
                Conversations.tenant_id == tenant_id,
            ).first()
        except ValueError:
            pass
    if conv is None:
        conv = _get_or_create_web_conversation(db, tenant_id)

    now = datetime.datetime.now(datetime.UTC)
    db.add(Messages(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        sender="user",
        message=text,
        created_at=now,
    ))
    db.flush()

    history = [
        {"role": m.sender, "content": m.message}
        for m in (
            db.query(Messages)
            .filter(
                Messages.conversation_id == conv.id,
                Messages.sender.in_(["user", "assistant"]),
            )
            .order_by(Messages.created_at)
            .all()
        )
        if m.sender and m.message
    ]
    # Exclude the message we just added
    if history and history[-1]["role"] == "user" and history[-1]["content"] == text:
        history = history[:-1]

    if not settings.llm_configured:
        no_key_text = (
            "No LLM API key is configured. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your .env file to enable the chat assistant."
        )

        def _no_key() -> Any:
            yield f"data: {json.dumps({'type': 'done', 'text': no_key_text})}\n\n"

        db.add(Messages(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            sender="assistant",
            message=no_key_text,
            created_at=datetime.datetime.now(datetime.UTC),
        ))
        db.commit()
        return StreamingResponse(_no_key(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    conv_id = conv.id

    def generate() -> Any:
        import logging as _logging
        _log = _logging.getLogger(__name__)
        final_text = ""
        try:
            for event in stream_general_conversation(text, history, tenant_id, db):
                if event["type"] == "done":
                    final_text = event["text"]
                yield f"data: {json.dumps(event)}\n\n"
        except Exception:
            _log.exception("Chat stream error")
            error_event = {"type": "error", "text": "Something went wrong. Please try again."}
            yield f"data: {json.dumps(error_event)}\n\n"
            final_text = ""

        if final_text:
            db.add(Messages(
                id=uuid.uuid4(),
                conversation_id=conv_id,
                sender="assistant",
                message=final_text,
                created_at=datetime.datetime.now(datetime.UTC),
            ))
            db.commit()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
