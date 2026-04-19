import datetime
import logging
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.llm.conversation import run_conversation, run_trend_conversation
from app.models.conversation import Conversations, Messages
from app.models.insight import Insights
from app.models.notification import Notifications

logger = logging.getLogger(__name__)


def get_or_create_conversation(db: Session, insight: Insights, channel: str) -> Conversations:
    """Return the existing conversation for this insight and channel, or create one."""
    existing = (
        db.query(Conversations)
        .filter(Conversations.insight_id == insight.id, Conversations.channel == channel)
        .first()
    )
    if existing:
        return existing

    conversation = Conversations(
        id=uuid.uuid4(),
        tenant_id=insight.tenant_id,
        insight_id=insight.id,
        channel=channel,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(conversation)
    db.flush()
    return conversation


def _load_history(db: Session, conversation: Conversations) -> list[dict]:  # type: ignore[type-arg]
    """Load prior turns as a list of role/content dicts."""
    msgs = (
        db.query(Messages)
        .filter(Messages.conversation_id == conversation.id)
        .order_by(Messages.created_at)
        .all()
    )
    return [{"role": m.sender, "content": m.message} for m in msgs if m.sender and m.message]


def _save_message(
    db: Session, conversation: Conversations, role: str, content: str
) -> None:
    db.add(Messages(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender=role,
        message=content,
        created_at=datetime.datetime.now(datetime.UTC),
    ))


def handle_user_message(
    db: Session,
    notification: Notifications,
    user_message: str,
) -> str | None:
    """
    Given an incoming user message tied to a notification, run the conversational
    agent and return Claude's response. Returns None if the agent is not configured.
    """
    if not settings.llm_configured:
        logger.warning("No LLM API key set — skipping conversation")
        return None

    insight = notification.insight
    if insight is None:
        logger.warning("Notification %s has no loaded insight", notification.id)
        return None

    channel = notification.channel or "slack"
    conversation = get_or_create_conversation(db, insight, channel)
    history = _load_history(db, conversation)

    if insight.anomaly_id is not None and insight.anomaly is not None:
        response = run_conversation(
            user_message=user_message,
            history=history,
            insight=insight,
            anomaly=insight.anomaly,
            db=db,
        )
    elif insight.trend_id is not None and insight.trend is not None:
        response = run_trend_conversation(
            user_message=user_message,
            history=history,
            insight=insight,
            trend=insight.trend,
            db=db,
        )
    else:
        logger.warning("Notification %s insight has neither anomaly nor trend loaded", notification.id)
        return None

    _save_message(db, conversation, "user", user_message)
    _save_message(db, conversation, "assistant", response)
    db.commit()

    return response
