"""
Twilio SMS webhook.

Receives inbound SMS messages, acknowledges immediately with empty TwiML,
then processes the conversation in the background and replies via the Twilio API.

Setup required in your Twilio console:
  1. Phone Numbers → your number → Messaging Configuration
  2. "A message comes in" → Webhook → https://<your-ngrok-url>/sms (HTTP POST)
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator

from app.config import settings
from app.database.session import get_db
from app.integrations.twilio import send_reply
from app.models.notification import Notifications
from app.services.conversation import handle_user_message

router = APIRouter(prefix="/sms", tags=["sms"])
logger = logging.getLogger(__name__)

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _verify_twilio_signature(url: str, params: dict, signature: str) -> bool:  # type: ignore[type-arg]
    """Verify the request actually came from Twilio."""
    if not settings.twilio_auth_token:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    return bool(validator.validate(url, params, signature))


def _process_sms(
    db: Session,
    from_number: str,
    user_message: str,
) -> None:
    """Background task: find the conversation context, run the agent, send reply."""
    notification = (
        db.query(Notifications)
        .filter(
            Notifications.channel == "sms",
            Notifications.external_message_id == from_number,
        )
        .order_by(Notifications.delivered_at.desc())
        .first()
    )
    if notification is None:
        logger.debug("No SMS notification found for %s — ignoring", from_number)
        return

    db.refresh(notification, ["insight"])
    if notification.insight:
        db.refresh(notification.insight, ["anomaly", "trend"])

    response = handle_user_message(db, notification, user_message)
    if response is None:
        return

    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number:
        send_reply(
            to_number=from_number,
            from_number=settings.twilio_from_number,
            text=response,
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
        )


@router.post("")
async def sms_inbound(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> Response:
    form_data = await request.form()
    params = dict(form_data)

    signature = request.headers.get("X-Twilio-Signature", "")
    if not _verify_twilio_signature(str(request.url), params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    from_number = str(params.get("From", ""))
    body = str(params.get("Body", ""))

    if not from_number:
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    background_tasks.add_task(
        _process_sms,
        db=db,
        from_number=from_number,
        user_message=body,
    )

    return Response(content=_EMPTY_TWIML, media_type="application/xml")
