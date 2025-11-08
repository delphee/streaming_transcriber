import asyncio
import logging
from aioapns import APNs, NotificationRequest
from django.conf import settings
from history.models import DeviceToken

logger = logging.getLogger(__name__)


async def send_tech_status_push_async(device_tokens, new_status, appointment_id, data=None):
    """
    Send push notification for tech status update
    device_tokens: list of device token strings
    Returns list of bad tokens to be deleted
    """
    if not device_tokens:
        logger.info(f"No device tokens provided")
        return []

    # Check if we have credentials
    if not settings.APNS_KEY_CONTENT:
        logger.error("APNS credentials not configured")
        return []

    # Log which environment we're using
    env = "SANDBOX" if settings.APNS_USE_SANDBOX else "PRODUCTION"
    logger.info(f"🔧 Using APNs {env} environment with {len(device_tokens)} token(s)")

    # Create APNs client
    client = APNs(
        key=settings.APNS_KEY_CONTENT,
        key_id=settings.APNS_KEY_ID,
        team_id=settings.APNS_TEAM_ID,
        topic=settings.APNS_BUNDLE_ID,
        use_sandbox=settings.APNS_USE_SANDBOX,
    )

    # Prepare push payload - VISIBLE for testing
    payload = {
        "aps": {
            "alert": {
                "title": "Tech Status Update",
                "body": f"Status: {new_status}, Appointment: {appointment_id}"
            },
            "sound": "default",
            "content-available": 1,
        },
        "result": new_status,
        "appointment_id": appointment_id,
    }

    if data:
        payload["data"] = data

    bad_tokens = []

    # Send to all devices
    for token in device_tokens:
        try:
            request = NotificationRequest(
                device_token=token,
                message=payload,
            )

            response = await client.send_notification(request)

            # Check if the notification was successful
            if response.is_successful:
                logger.info(f"✅ Sent tech status {new_status} to device: {token[:10]}...")
            else:
                logger.error(f"❌ Failed to send to {token[:10]}: {response.description} (status: {response.status})")

                # Mark bad tokens for deletion (410 = Unregistered, 400 = BadDeviceToken)
                if response.status in [400, 410]:
                    bad_tokens.append(token)
                    logger.warning(f"🗑️ Marking invalid device token for removal: {token[:10]}...")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Exception sending to {token[:10]}: {e}")

            if "BadDeviceToken" in error_msg or "Unregistered" in error_msg:
                bad_tokens.append(token)

    # await client.close()
    return bad_tokens

def send_push_task(user_id, new_status, appointment_id, data=None):
    """
    Django-Q task function - runs async code
    """
    # Fetch device tokens in sync context BEFORE entering async
    device_tokens = list(DeviceToken.objects.filter(
        user_id=user_id,
        platform='ios'
    ).values_list('device_token', flat=True))

    # Pass tokens to async function
    bad_tokens = asyncio.run(send_tech_status_push_async(device_tokens, new_status, appointment_id, data))

    # Delete bad tokens in sync context
    if bad_tokens:
        deleted_count = DeviceToken.objects.filter(device_token__in=bad_tokens).delete()[0]
        logger.info(f"🗑️ Deleted {deleted_count} invalid device token(s)")


def send_tech_status_push(user, new_status, data=None, appointment_id=0):
    """
    Queue push notification as background task
    Call this from your Django views
    """
    # Check if user has any device tokens before queuing
    if not DeviceToken.objects.filter(user=user, platform='ios').exists():
        logger.info(f"⚠️ No device tokens found for user {user.id}, skipping push notification")
        return

    from django_q.tasks import async_task

    async_task(
        'history.push_notifications.send_push_task',
        user.id,
        new_status,
        appointment_id,
        data
    )
    logger.info(f"📤 Queued push notification for user {user.id}, status {new_status}, appointment {appointment_id}")
