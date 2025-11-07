import asyncio
import logging
from aioapns import APNs, NotificationRequest
from django.conf import settings
from history.models import DeviceToken

logger = logging.getLogger(__name__)


async def send_tech_status_push_async(user_id, new_status, data=None):
    """
    Send silent push notification for tech status update
    """
    # Get user's device tokens
    device_tokens = DeviceToken.objects.filter(
        user_id=user_id,
        platform='ios'
    ).values_list('device_token', flat=True)

    if not device_tokens:
        logger.info(f"No device tokens found for user {user_id}")
        return

    # Check if we have credentials
    if not settings.APNS_KEY_CONTENT:
        logger.error("APNS credentials not configured")
        return

    # Create APNs client
    client = APNs(
        key=settings.APNS_KEY_CONTENT,
        key_id=settings.APNS_KEY_ID,
        team_id=settings.APNS_TEAM_ID,
        topic=settings.APNS_BUNDLE_ID,
        use_sandbox=settings.APNS_USE_SANDBOX,
    )

    # Prepare silent push payload
    payload = {
        "aps": {
            "content-available": 1,  # This makes it SILENT
        },
        "tech_status": new_status,
    }

    if data:
        payload["data"] = data

    # Send to all devices
    for token in device_tokens:
        try:
            request = NotificationRequest(
                device_token=token,
                message=payload,
            )

            await client.send_notification(request)
            logger.info(f"✅ Sent tech status {new_status} to device: {token[:10]}...")

        except Exception as e:
            logger.error(f"❌ Failed to send to {token[:10]}: {e}")

    # Close connection
    await client.close()


def send_push_task(user_id, new_status, data=None):
    """
    Django-Q task function - runs async code
    """
    asyncio.run(send_tech_status_push_async(user_id, new_status, data))


def send_tech_status_push(user, new_status, data=None):
    """
    Queue push notification as background task
    Call this from your Django views
    """
    from django_q.tasks import async_task

    async_task(
        'history.push_notifications.send_push_task',
        user.id,
        new_status,
        data
    )
    logger.info(f"📤 Queued push notification for user {user.id}, status {new_status}")