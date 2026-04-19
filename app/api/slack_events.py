"""
Slack Events API webhook.

Receives message events from Slack, acknowledges immediately (Slack requires
a response within 3 seconds), then processes in the background.

Setup required in your Slack app dashboard:
  1. Event Subscriptions → Enable Events
  2. Request URL: https://<your-ngrok-url>/slack/events
  3. Subscribe to bot events: message.channels
  4. OAuth & Permissions → add scope: channels:history → reinstall app
"""
import hashlib
import hmac
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database.session import get_db
from app.integrations.slack import post_reply
from app.models.notification import Notifications
from app.services.conversation import handle_user_message

router = APIRouter(prefix="/slack", tags=["slack"])
logger = logging.getLogger(__name__)


def _verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    """Verify the request actually came from Slack."""
    if not settings.slack_signing_secret:
        return False
    if abs(time.time() - float(timestamp)) > 300:
        return False  # Replay attack protection: reject requests older than 5 minutes
    base = f"v0:{timestamp}:{request_body.decode()}"
    expected = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _process_message(
    db: Session,
    channel: str,
    thread_ts: str,
    user_message: str,
) -> None:
    """Background task: look up the notification, run the agent, post the reply."""
    notification = (
        db.query(Notifications)
        .filter(Notifications.external_message_id == thread_ts)
        .first()
    )
    if notification is None:
        logger.debug("No notification found for thread_ts=%s — ignoring", thread_ts)
        return

    # Load relationships needed by the agent
    db.refresh(notification, ["insight"])
    if notification.insight:
        db.refresh(notification.insight, ["anomaly", "trend"])

    response = handle_user_message(db, notification, user_message)
    if response is None:
        return

    if settings.slack_bot_token:
        post_reply(
            text=response,
            channel=channel,
            thread_ts=thread_ts,
            token=settings.slack_bot_token,
        )


@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = await request.body()
    payload = await request.json()

    # Handle URL verification before signature check — this is a one-time setup
    # step and carries no security risk since we're just echoing back a challenge.
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    event = payload.get("event", {})

    # Only handle messages — ignore bot messages and non-thread messages
    if (
        event.get("type") != "message"
        or event.get("bot_id")
        or event.get("subtype")
        or not event.get("thread_ts")
        or event.get("thread_ts") == event.get("ts")  # top-level message, not a reply
    ):
        return {"ok": True}

    background_tasks.add_task(
        _process_message,
        db=db,
        channel=event["channel"],
        thread_ts=event["thread_ts"],
        user_message=event.get("text", ""),
    )

    return {"ok": True}
