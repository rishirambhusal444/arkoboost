import logging 

from django.contrib.messages import get_messages 
from django.utils import timezone

from .models import ManualSubscribeProfile, SubscriberProfile, VideoProfile


logger = logging.getLogger("subscribers.messages")


class CaptureDjangoMessagesMiddleware: 
    """Capture flash messages and write them to app logs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            storage = get_messages(request)
            for msg in storage:
                level = (getattr(msg, "level_tag", "") or "info").upper()
                logger.info("[%s] %s", level, str(msg))
        except Exception:
            logger.exception("Failed to capture Django messages")
        return response 


class TaskPresenceMiddleware:
    """
    Keep task presence status up to date without AJAX:
    when an authenticated user navigates away from task/watch URLs,
    mark presence flags inactive.
    """

    TASK_ACTIVE_PREFIXES = (
        "/tasks",
        "/videos/watch",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return response

        path = (getattr(request, "path", "") or "").lower()
        if path.startswith("/static/") or path.startswith("/admin/"):
            return response

        on_task_pages = any(path.startswith(prefix) for prefix in self.TASK_ACTIVE_PREFIXES)
        if on_task_pages:
            return response

        now = timezone.now()
        try:
            SubscriberProfile.objects.filter(user=user, active_status=True).update(
                active_status=False,
                updated_at=now,
            )
            ManualSubscribeProfile.objects.filter(
                user=user,
                active_status_for_subscribe=True,
            ).update(
                active_status_for_subscribe=False,
                updated_at=now,
            )
            VideoProfile.objects.filter(
                user=user,
                active_status_for_video=True,
            ).update(
                active_status_for_video=False,
                updated_at=now,
            )
        except Exception:
            logger.exception("Failed to update task presence state")
        return response
