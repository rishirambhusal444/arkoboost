import logging
import json
import secrets
from collections import Counter
from datetime import timedelta
from urllib.parse import urlparse
from urllib.parse import parse_qs

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Sum, Value, When
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    FacebookProfile,
    FacebookTaskAssing,
    SubscriberProfile,
    TopUserSubscribeTask,
    Video,
    VideoWatchTask,
)
from .services import (
    FacebookOAuthError,
    YouTubeOAuthError,
    FACEBOOK_BASIC_SCOPE,
    build_facebook_authorize_url,
    build_google_authorize_url,
    calculate_token_expiry,
    exchange_code_for_token,
    exchange_facebook_code_for_token,
    fetch_facebook_pages,
    fetch_facebook_userinfo,
    fetch_my_subscriptions, # Used for list_subscriptions
    fetch_my_subscribers,   # Used for list_subscribers
    ensure_valid_access_token,
    scan_profile,
    subscribe_to_channel,
    fetch_authenticated_channel_summary,
    fetch_userinfo,
    recalculate_profile_score,
    transfer_video_score_to_available,
    use_video_score,
)

logger = logging.getLogger(__name__)

HEARTBEAT_MIN_SECONDS = 2
HEARTBEAT_MAX_SECONDS = 15
MAX_VALID_SECONDS_PER_MINUTE = 45
MAX_SEEKS_PER_WINDOW = 6
MAX_PAUSES_PER_WINDOW = 12


def _required_google_settings_missing() -> bool:
    return not all(
        [
            settings.YOUTUBE_CLIENT_ID,
            settings.YOUTUBE_CLIENT_SECRET,
            settings.YOUTUBE_REDIRECT_URI,
        ]
    )


def _required_facebook_settings_missing() -> bool:
    return not all(
        [
            settings.FACEBOOK_CLIENT_ID,
            settings.FACEBOOK_CLIENT_SECRET,
            settings.FACEBOOK_REDIRECT_URI,
        ]
    )


def _extract_youtube_video_id(raw_url: str) -> str:
    """Extract a YouTube video id from common URL formats."""
    url = (raw_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if "youtu.be" in host:
        return path.strip("/").split("/")[0]
    if "youtube.com" in host:
        if path == "/watch":
            return (parse_qs(parsed.query).get("v") or [""])[0]
        if path.startswith("/shorts/") or path.startswith("/embed/"):
            return path.strip("/").split("/")[1] if len(path.strip("/").split("/")) > 1 else ""
    return ""


def _featured_videos_for_watch_page(current_profile: SubscriberProfile) -> list[dict]:
    """
    Build a distributed video task pool for active users.
    Rule:
    - only videos from active users with video_score > 0 are included
    - each video row counts as one task item
    """
    eligible_owner_ids = list(
        SubscriberProfile.objects.filter(active_status=True, video_score__gt=0)
        .exclude(id=current_profile.id)
        .values_list("user_id", flat=True)
    )
    if not eligible_owner_ids:
        return []

    owner_profiles_by_user_id = {
        row["user_id"]: row["category"]
        for row in SubscriberProfile.objects.filter(user_id__in=eligible_owner_ids).values("user_id", "category")
    }
    current_category = (current_profile.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    same_category_owner_ids = [
        user_id
        for user_id, category in owner_profiles_by_user_id.items()
        if (category or SubscriberProfile.CATEGORY_OTHER).strip().lower() == current_category
    ]
    other_owner_ids = [user_id for user_id in eligible_owner_ids if user_id not in set(same_category_owner_ids)]
    prioritized_owner_ids = same_category_owner_ids + other_owner_ids
    if not prioritized_owner_ids:
        return []

    videos = (
        Video.objects.select_related("owner_user")
        .filter(owner_user_id__in=prioritized_owner_ids)
        .order_by("-updated_at")
    )
    featured_videos = []
    owner_priority = {user_id: idx for idx, user_id in enumerate(prioritized_owner_ids)}
    sorted_videos = sorted(
        videos,
        key=lambda item: (
            owner_priority.get(item.owner_user_id, len(owner_priority) + 1),
            -(int(item.id or 0)),
        ),
    )
    for item in sorted_videos:
        yt_id = _extract_youtube_video_id(item.video_url) or "1jtVBdA7Q9A"
        featured_videos.append(
            {
                "db_id": item.id,
                "video_id": yt_id,
                "title": yt_id,
                "thumbnail": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                "channel_title": (item.owner_user.username if item.owner_user else "YouTube Channel"),
                "video_url": item.video_url,
                "video_score": 0,
            }
        )
    return featured_videos



def _get_or_create_user_for_facebook(userinfo: dict):
    User = get_user_model()
    facebook_subject_id = (userinfo.get("id") or "").strip()
    email = (userinfo.get("email") or "").strip().lower()
    name = (userinfo.get("name") or "").strip()

    existing_facebook_profile = (
        FacebookProfile.objects.select_related("user")
        .filter(facebook_subject_id=facebook_subject_id)
        .first()
    )
    if existing_facebook_profile:
        return existing_facebook_profile.user, existing_facebook_profile

    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()

    if user is None:
        username_seed = email.split("@")[0] if email else f"facebook_{facebook_subject_id[:16]}"
        user = User.objects.create_user(
            username=_build_unique_username(username_seed),
            email=email,
        )
    elif email and user.email.lower() != email:
        user.email = email
        user.save(update_fields=["email"])

    facebook_profile, _ = FacebookProfile.objects.get_or_create(user=user)
    if name and not user.handle:
        _rename_user_to_handle(user, name)
    return user, facebook_profile
    
def _normalize_handle(value: str) -> str:
    return (value or "").strip().lower().lstrip("@")


def _is_same_category(receiver: SubscriberProfile, target: SubscriberProfile) -> bool:
    receiver_category = (receiver.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    target_category = (target.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    return receiver_category == target_category


def _transfer_score_for_verified_task(task: TopUserSubscribeTask) -> None:
    with transaction.atomic():
        target_updated = SubscriberProfile.objects.filter(
            id=task.target_profile_id,
            score__gt=0,
        ).update(
            score=F("score") - 1,
            reserved_score=Case(
                When(reserved_score__gt=0, then=F("reserved_score") - 1),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        if target_updated:
            SubscriberProfile.objects.filter(id=task.profile_id).update(score=F("score") + 1)


def _release_unverified_task_reservations(task_qs) -> None:
    task_rows = list(task_qs.values_list("id", "target_profile_id"))
    if not task_rows:
        return

    task_ids = [task_id for task_id, _ in task_rows]
    reserved_by_target_id = Counter(target_id for _, target_id in task_rows)
    with transaction.atomic():
        TopUserSubscribeTask.objects.filter(id__in=task_ids).delete()
        for target_id, released_count in reserved_by_target_id.items():
            SubscriberProfile.objects.filter(id=target_id).update(
                reserved_score=Case(
                    When(
                        reserved_score__gte=released_count,
                        then=F("reserved_score") - released_count,
                    ),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )


def _reserve_targets_for_tasks(tasks: list[TopUserSubscribeTask]) -> None:
    if not tasks:
        return

    reserved_by_target_id = Counter(task.target_profile_id for task in tasks)
    with transaction.atomic():
        TopUserSubscribeTask.objects.bulk_create(tasks)
        for target_id, reserved_count in reserved_by_target_id.items():
            SubscriberProfile.objects.filter(id=target_id).update(
                reserved_score=F("reserved_score") + reserved_count
            )


def _sync_top_user_task_verified_status(
    profile: SubscriberProfile,
    *,
    force: bool = False,
    cooldown_seconds: int = 120,
) -> bool:
    if not profile.google_subject_id:
        return False
    now = timezone.now()
    if (
        not force
        and profile.last_verified_sync_at
        and (now - profile.last_verified_sync_at) < timedelta(seconds=cooldown_seconds)
    ):
        return False
    try:
        access_token = ensure_valid_access_token(profile)
        subscriptions = fetch_my_subscriptions(access_token)
    except Exception:
        return False

    subscribed_handles = {
        _normalize_handle(item.get("handle", ""))
        for item in subscriptions
        if item.get("handle")
    }
    tasks = list(
        TopUserSubscribeTask.objects.select_related("target_profile")
        .filter(profile=profile)
    )
    now = timezone.now()
    changed_tasks = []
    for task in tasks:
        target_handle = _normalize_handle(task.target_profile.handle)
        is_verified = bool(target_handle and target_handle in subscribed_handles)
        if is_verified and not task.verified_status:
            task.verified_status = True
            task.updated_at = now
            changed_tasks.append(task)
            _transfer_score_for_verified_task(task)
    if changed_tasks:
        TopUserSubscribeTask.objects.bulk_update(
            changed_tasks,
            ["verified_status", "updated_at"],
        )
    profile.last_verified_sync_at = now
    profile.save(update_fields=["last_verified_sync_at", "updated_at"])
    return True


def _rebalance_active_top_user_tasks(now, *, online_minutes: int = 10) -> None:
    window_start = now - timedelta(minutes=online_minutes)
    active_profiles = list(
        SubscriberProfile.objects.select_related("user")
        .filter(
            user__is_active=True,
            active_status=True,
            last_tasks_entry_at__gte=window_start,
        )
        .order_by("id")
    )
    if not active_profiles:
        _release_unverified_task_reservations(
            TopUserSubscribeTask.objects.filter(verified_status=False)
        )
        return

    receivers = active_profiles
    _release_unverified_task_reservations(
        TopUserSubscribeTask.objects.filter(verified_status=False)
    )

    targets = list(
        SubscriberProfile.objects.select_related("user")
        .filter(
            user__is_active=True,
            score__gt=F("reserved_score"),
        )
        .exclude(channel_id="")
        .order_by("-score", "id")
    )
    if not targets:
        return

    target_capacity_by_id = {
        p.id: max(int(p.score or 0) - int(p.reserved_score or 0), 0)
        for p in targets
    }
    target_capacity_by_id = {
        target_id: capacity
        for target_id, capacity in target_capacity_by_id.items()
        if capacity > 0
    }
    if not target_capacity_by_id:
        return

    total_capacity = sum(target_capacity_by_id.values())
    receiver_count = len(receivers)
    if total_capacity <= 0 or receiver_count <= 0:
        return

    base_quota = total_capacity // receiver_count
    remainder = total_capacity % receiver_count
    quota_by_receiver_id = {}
    for index, receiver in enumerate(receivers):
        quota_by_receiver_id[receiver.id] = base_quota + (1 if index < remainder else 0)

    target_score_by_id = {p.id: int(p.score or 0) for p in targets}
    target_by_id = {p.id: p for p in targets}
    remaining_by_target_id = target_capacity_by_id.copy()
    allocated_target_ids_by_receiver_id = {p.id: set() for p in receivers}
    receiver_ids = [p.id for p in receivers]
    existing_pairs = set(
        TopUserSubscribeTask.objects.filter(profile_id__in=receiver_ids)
        .values_list("profile_id", "target_profile_id")
    )
    max_quota = max(quota_by_receiver_id.values()) if quota_by_receiver_id else 0

    for _ in range(max_quota):
        for receiver in receivers:
            receiver_id = receiver.id
            if len(allocated_target_ids_by_receiver_id[receiver_id]) >= quota_by_receiver_id[receiver_id]:
                continue

            candidate_ids = [
                target_id
                for target_id, remaining in remaining_by_target_id.items()
                if remaining > 0
                and target_id != receiver_id
                and target_id not in allocated_target_ids_by_receiver_id[receiver_id]
                and (receiver_id, target_id) not in existing_pairs
            ]
            if not candidate_ids:
                continue

            best_target_id = max(
                candidate_ids,
                key=lambda target_id: (
                    1 if _is_same_category(receiver, target_by_id[target_id]) else 0,
                    remaining_by_target_id[target_id],
                    target_score_by_id[target_id],
                    -target_id,
                ),
            )
            allocated_target_ids_by_receiver_id[receiver_id].add(best_target_id)
            existing_pairs.add((receiver_id, best_target_id))
            remaining_by_target_id[best_target_id] -= 1

    rows_to_create = []
    for receiver_id, target_ids_for_receiver in allocated_target_ids_by_receiver_id.items():
        for target_id in target_ids_for_receiver:
            rows_to_create.append(
                TopUserSubscribeTask(
                    profile_id=receiver_id,
                    target_profile_id=target_id,
                    verified_status=False,
                )
            )
    _reserve_targets_for_tasks(rows_to_create)


def _build_unique_username(base_value: str) -> str:
    User = get_user_model()
    seed = (base_value or "youtube_user").strip()[:150]
    candidate = seed
    suffix = 1
    while User.objects.filter(username=candidate).exists():
        suffix += 1
        short_base = seed[: max(150 - len(str(suffix)) - 1, 1)]
        candidate = f"{short_base}_{suffix}"
    return candidate


def _rename_user_to_handle(user, handle: str) -> None:
    handle = (handle or "").strip()
    if not handle or (user.username == handle and user.handle == handle):
        return

    User = get_user_model()
    candidate = handle[:150]
    suffix = 1
    while User.objects.filter(username=candidate).exclude(pk=user.pk).exists():
        suffix += 1
        short_base = handle[: max(150 - len(str(suffix)) - 1, 1)]
        candidate = f"{short_base}_{suffix}"

    user.username = candidate
    user.handle = handle
    user.save(update_fields=["username", "handle"])


def _get_or_create_user_for_google(userinfo: dict):
    User = get_user_model()
    google_subject_id = (userinfo.get("sub") or "").strip()
    email = (userinfo.get("email") or "").strip().lower()

    existing_profile = (
        SubscriberProfile.objects.select_related("user")
        .filter(google_subject_id=google_subject_id)
        .first()
    )
    if existing_profile:
        return existing_profile.user, existing_profile

    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()

    is_staff_email = email.startswith("rishirambhusal")

    if user is None:
        username_seed = email.split("@")[0] if email else f"youtube_{google_subject_id[:16]}"
        user = User.objects.create_user(
            username=_build_unique_username(username_seed),
            email=email,
            is_staff=is_staff_email,
        )
    else:
        updated = False
        if is_staff_email and not user.is_staff:
            user.is_staff = True
            updated = True
        if email and user.email.lower() != email:
            user.email = email
            updated = True
        if updated:
            user.save()

    profile, _ = SubscriberProfile.objects.get_or_create(user=user)
    return user, profile


def home(request):
    if request.user.is_authenticated:
        profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
        if profile.active_status_for_video or profile.active_status_for_youtube:
            profile.active_status_for_video = False
            profile.active_status_for_youtube = False
            profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "updated_at"])
    return render(request, "subscribers/home.html")


def login_page(request):
    if request.user.is_authenticated:
        return redirect("subscribers:home")
    return render(
        request,
        "subscribers/login.html",
        {
            "oauth_ready": not _required_google_settings_missing(),
            "facebook_oauth_ready": not _required_facebook_settings_missing(),
        },
    )


def signup(request):
    """Handle user signup with email/password or redirect to Google OAuth."""
    if request.user.is_authenticated:
        return redirect("subscribers:home")
    
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        
        if not email or not password:
            messages.error(request, "Email and password are required.")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })
        
        if password != password_confirm:
            messages.error(request, "Passwords do not match.")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })
        
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "An account with this email already exists.")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })
        
        try:
            username = _build_unique_username(email.split("@")[0])
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=email.startswith("rishirambhusal")
            )
            login(request, user)
            profile, _ = SubscriberProfile.objects.get_or_create(user=user)
            messages.success(request, "Account created successfully. Please connect your Google account to use analytics.")
            return redirect("subscribers:home")
        except Exception as exc:
            messages.error(request, f"Signup failed: {exc}")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })
    
    return render(
        request,
        "subscribers/signup.html",
        {
            "oauth_ready": not _required_google_settings_missing(),
            "facebook_oauth_ready": not _required_facebook_settings_missing(),
        },
    )


@login_required
def list_subscriptions(request):
    """View to display the list of channels the authenticated user is subscribed to."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    if not profile.google_subject_id:
        messages.warning(request, "Please connect your Google account to see your subscriptions.")
        return redirect("subscribers:home")

    try:
        access_token = ensure_valid_access_token(profile)
        subscriptions = fetch_my_subscriptions(access_token) # Fetch channels user is subscribed to
        return render(request, "subscribers/subscriptions_list.html", {"subscriptions": subscriptions})
    except YouTubeOAuthError as exc:
        # Handle case where user might not have a channel
        if "channelNotFound" in str(exc) or "403" in str(exc):
            messages.error(request, "We couldn't find a YouTube channel associated with this Google account or access was denied.")
            return redirect("subscribers:home")
            
        messages.error(request, f"Google Authentication issue: {exc}")
        return redirect("subscribers:home")
    except Exception as exc:
        messages.error(request, f"Could not fetch subscriptions: {exc}")
        return redirect("subscribers:home")


@login_required
def list_subscribers(request):
    """View to display the list of public subscribers to the authenticated user's channel."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    if not profile.google_subject_id:
        messages.warning(request, "Please connect your Google account to see your channel's subscribers.")
        return redirect("subscribers:home")

    try:
        access_token = ensure_valid_access_token(profile)
        subscribers = fetch_my_subscribers(access_token) # Fetch actual subscribers to the user's channel
        return render(request, "subscribers/subscribers_list.html", {"subscribers": subscribers})
    except YouTubeOAuthError as exc:
        if "channelNotFound" in str(exc) or "403" in str(exc):
            messages.error(request, "We couldn't find a YouTube channel associated with this Google account or access was denied.")
            return redirect("subscribers:home")
        messages.error(request, f"Google Authentication issue: {exc}")
        return redirect("subscribers:home")
    except Exception as exc:
        messages.error(request, f"Could not fetch subscribers: {exc}")
        return redirect("subscribers:home")


@login_required
def scan_now(request):
    """Trigger an immediate scan of the user's YouTube channel."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    if not profile.google_subject_id:
        messages.warning(request, "Please connect your Google account first.")
        return redirect("subscribers:home")

    try:
        scan_profile(profile)
        _sync_top_user_task_verified_status(profile, force=True, cooldown_seconds=120)
        recalculate_profile_score(profile)
        messages.success(request, "Channel scan completed successfully!")
    except Exception as exc:
        messages.error(request, f"Scan failed: {exc}")

    return redirect("subscribers:youtube_tasks")


@login_required
def dashboard(request):
    return redirect("subscribers:home")


@login_required
def enter_youtube_tasks(request):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    profile.active_status_for_youtube = True
    profile.active_status_for_video = False
    profile.last_tasks_entry_at = timezone.now()
    profile.save(update_fields=["active_status_for_youtube", "active_status_for_video", "last_tasks_entry_at", "updated_at"])
    subscriptions_preview = []
    if profile.google_subject_id:
        try:
            access_token = ensure_valid_access_token(profile)
            subscriptions_preview = fetch_my_subscriptions(access_token, limit=6)
        except Exception:
            subscriptions_preview = []

    context = {
        "profile": profile,
        "google_connected": bool(profile.google_subject_id),
        "subscriptions_preview": subscriptions_preview,
        "total_subscribed": int(profile.subscribed_channel_count or 0),
        "channel_subscriber_total": int(profile.channel_subscriber_count or 0),
        "new_subscribers": int(profile.subscriber_change_since_last_scan or 0),
        "video_score": int(profile.video_score or 0),
        "video_score_reserved": int(profile.video_score_reserved or 0),
    }
    return render(request, "subscribers/youtube_enter.html", context)


@login_required
def enter_watch_tasks(request):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    profile.active_status_for_video = True
    profile.active_status_for_youtube = False
    profile.last_tasks_entry_at = timezone.now()
    profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "last_tasks_entry_at", "updated_at"])
    watch_videos = (
        Video.objects.exclude(owner_user=request.user)
        .order_by("-updated_at")[:12]
    )
    my_videos = Video.objects.filter(owner_user=request.user).order_by("-updated_at")[:3]
    total_watch_seconds = sum(int(v.watched_time_seconds or 0) for v in watch_videos)
    total_watch_minutes = total_watch_seconds // 60
    completed_videos = sum(1 for v in watch_videos if v.status == Video.STATUS_COMPLETE)
    active_videos = sum(1 for v in watch_videos if v.status == Video.STATUS_PENDING)

    watch_video_ids = [v.id for v in watch_videos]
    task_status_by_video_id = {
        row["video_id"]: {
            "status": row["status"],
            "verified": bool(row["verified_status"]),
        }
        for row in VideoWatchTask.objects.filter(
            profile=profile,
            video_id__in=watch_video_ids,
        ).values("video_id", "status", "verified_status")
    }
    watch_video_cards = []
    for v in watch_videos:
        task_state = task_status_by_video_id.get(v.id)
        if not task_state:
            my_task_label = "Not Assigned"
        elif task_state.get("verified"):
            my_task_label = "Completed"
        else:
            my_task_label = f"Assigned ({task_state.get('status')})"
        watch_video_cards.append(
            {
                "video": v,
                "my_task_label": my_task_label,
            }
        )

    context = {
        "profile": profile,
        "watch_videos": watch_videos,
        "watch_video_cards": watch_video_cards,
        "my_videos": my_videos,
        "watch_pool_count": len(watch_videos),
        "total_watch_minutes": total_watch_minutes,
        "completed_videos": completed_videos,
        "active_videos": active_videos,
        "video_score": int(profile.video_score or 0),
        "video_score_reserved": int(profile.video_score_reserved or 0),
    }
    return render(request, "subscribers/watch_enter.html", context)


@login_required
@require_POST
def save_watch_video_link(request):
    """Create/update a user's watch video link from watch entry page."""
    video_url = (request.POST.get("video_url") or "").strip()
    video_pk = (request.POST.get("video_pk") or "").strip()
    owned_qs = Video.objects.filter(owner_user=request.user).order_by("-updated_at")
    owned_count = owned_qs.count()

    if not video_url:
        messages.error(request, "Please enter a YouTube video URL.")
        return redirect("subscribers:enter_watch_tasks")

    yt_id = _extract_youtube_video_id(video_url)
    if not yt_id:
        messages.error(request, "Invalid YouTube URL. Please paste a valid watch/shorts/embed URL.")
        return redirect("subscribers:enter_watch_tasks")

    if video_pk:
        video = Video.objects.filter(id=video_pk, owner_user=request.user).first()
        if not video:
            messages.error(request, "Video not found for update.")
            return redirect("subscribers:enter_watch_tasks")
        video.video_url = video_url
        video.watched_time_seconds = 0
        video.status = Video.STATUS_PENDING
        video.save(update_fields=["video_url", "watched_time_seconds", "status", "updated_at"])
        messages.success(request, "Video link updated.")
    else:
        if owned_count >= 3:
            messages.error(request, "Maximum 3 video links allowed. Please edit an existing link.")
            return redirect("subscribers:enter_watch_tasks")
        Video.objects.get_or_create(
            owner_user=request.user,
            video_url=video_url,
            defaults={
                "duration_seconds": 0,
                "watched_time_seconds": 0,
                "status": Video.STATUS_PENDING,
            },
        )
        messages.success(request, "Video link added.")

    return redirect("subscribers:enter_watch_tasks")


@login_required
def profile_page(request):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        slot_urls = [
            (request.POST.get("video_url_1") or "").strip(),
            (request.POST.get("video_url_2") or "").strip(),
            (request.POST.get("video_url_3") or "").strip(),
        ]
        video_ids = []
        for idx, slot_url in enumerate(slot_urls, start=1):
            if not slot_url:
                continue
            video_id = _extract_youtube_video_id(slot_url)
            if not video_id:
                messages.error(request, f"Slot {idx}: invalid YouTube URL.")
                return redirect("subscribers:profile")
            video_ids.append((video_id, slot_url))

        profile.manual_video_url_1 = slot_urls[0]
        profile.manual_video_url_2 = slot_urls[1]
        profile.manual_video_url_3 = slot_urls[2]
        profile.save(update_fields=["manual_video_url_1", "manual_video_url_2", "manual_video_url_3", "updated_at"])

        for slot_index, slot_url in enumerate(slot_urls, start=1):
            if not slot_url:
                Video.objects.filter(added_by=request.user, source_slot=slot_index).delete()
                continue

            video_id = _extract_youtube_video_id(slot_url)
            Video.objects.update_or_create(
                added_by=request.user,
                source_slot=slot_index,
                defaults={
                    "source_profile": profile,
                    "youtube_video_id": video_id,
                    "title": f"Manual Video {slot_index}",
                    "channel_id": profile.channel_id or "",
                    "channel_title": profile.channel_title or profile.handle or request.user.username,
                    "video_url": slot_url,
                    "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    "added_by": request.user,
                },
            )

        messages.success(request, "Your 3 video slots were saved to the video table.")
        return redirect("subscribers:profile")

    facebook_profile = getattr(request.user, "facebook_profile", None)
    facebook_connected = bool(facebook_profile and facebook_profile.facebook_subject_id)
    google_connected = bool(profile.google_subject_id)
    profile_theme = "facebook" if facebook_connected and not google_connected else "google"
    now = timezone.now()
    profile.last_tasks_entry_at = now
    if not profile.active_status:
        profile.active_status = True
        profile.save(update_fields=["active_status", "last_tasks_entry_at", "updated_at"])
    else:
        profile.save(update_fields=["last_tasks_entry_at", "updated_at"])
    recalculate_profile_score(profile)
    _rebalance_active_top_user_tasks(now, online_minutes=10)

    user_email = (request.user.email or "").lower()
    is_special_staff = user_email.startswith("rishirambhusal")
    is_admin_user = request.user.is_staff or is_special_staff or request.user.is_superuser
    top_user_subscribe_tasks = (
        TopUserSubscribeTask.objects.select_related("target_profile__user")
        .filter(profile=profile)
        .order_by("-updated_at", "-created_at")
    )
    assigned_task_mode = "youtube"
    assigned_tasks = list(top_user_subscribe_tasks)

    if profile_theme == "facebook":
        assigned_task_mode = "facebook"
        facebook_candidates = list(
            FacebookProfile.objects.select_related("user", "user__subscriber_profile")
            .exclude(user=request.user)
            .exclude(facebook_subject_id__isnull=True)
            .exclude(facebook_subject_id="")
            .order_by("-connected_at", "-updated_at")
        )
        target_profile_ids = [
            fb.user.subscriber_profile.id
            for fb in facebook_candidates
            if hasattr(fb.user, "subscriber_profile")
        ]
        task_map = {
            task.target_facebook_profile_id: task
            for task in FacebookTaskAssing.objects.filter(
                profile=profile,
                target_facebook_profile_id__in=[fb.id for fb in facebook_candidates],
            )
        }
        assigned_tasks = []
        for fb in facebook_candidates:
            target_profile = getattr(fb.user, "subscriber_profile", None)
            if not target_profile:
                continue
            existing_task = task_map.get(fb.id)
            assigned_tasks.append(
                {
                    "target_profile": target_profile,
                    "target_facebook": fb,
                    "facebook_followed_status": bool(existing_task and existing_task.followed_status),
                    "facebook_followed_at": existing_task.followed_at if existing_task else None,
                    "last_attempt_at": existing_task.last_attempt_at if existing_task else None,
                }
            )

    youtube_users = []
    youtube_user_cards = []
    if is_admin_user:
        youtube_users = list(
            SubscriberProfile.objects.select_related("user")
            .all()
            .order_by("-updated_at")
        )
        for user_profile in youtube_users:
            youtube_user_cards.append(
                {
                    "profile": user_profile,
                    "total_view_hours": round((user_profile.channel_total_view_count or 0) / 60, 2),
                }
            )

    my_total_view_hours = round((profile.channel_total_view_count or 0) / 60, 2)

    return render(
        request,
        "subscribers/profile.html",
        {
            "profile": profile,
            "profile_theme": profile_theme,
            "google_connected": google_connected,
            "facebook_connected": False,
            "facebook_profile": facebook_profile,
            "is_admin_user": is_admin_user,
            "youtube_users": youtube_users,
            "youtube_user_cards": youtube_user_cards,
            "my_total_view_hours": my_total_view_hours,
            "assigned_task_mode": assigned_task_mode,
            "assigned_tasks": assigned_tasks,
            "top_user_subscribe_tasks": top_user_subscribe_tasks,
        },
    )


@login_required
@require_POST
def update_facebook_profile(request):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    facebook_profile_url = (request.POST.get("facebook_profile_url") or "").strip()
    facebook_followers_count_raw = (request.POST.get("facebook_followers_count") or "0").strip()
    redirect_name = "subscribers:tasks" if request.POST.get("next") == "tasks" else "subscribers:profile"

    if facebook_profile_url:
        parsed_url = urlparse(facebook_profile_url)
        host = parsed_url.netloc.lower()
        if parsed_url.scheme not in {"http", "https"} or not (
            host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.com" or host.endswith(".fb.com")
        ):
            messages.error(request, "Please enter a valid Facebook profile or page URL.")
            return redirect(redirect_name)

    if not facebook_followers_count_raw.isdigit():
        messages.error(request, "Facebook followers must be a whole number.")
        return redirect(redirect_name)

    profile.facebook_profile_url = facebook_profile_url
    profile.facebook_followers_count = int(facebook_followers_count_raw)
    profile.save(update_fields=["facebook_profile_url", "facebook_followers_count", "updated_at"])
    messages.success(request, "Facebook profile details updated.")
    return redirect(redirect_name)


@login_required
def user_tasks(request, task_mode=None):

    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    now = timezone.now()
    profile.last_tasks_entry_at = now
    if not profile.active_status:
        profile.active_status = True
        profile.save(update_fields=["active_status", "last_tasks_entry_at", "updated_at"])
    else:
        profile.save(update_fields=["last_tasks_entry_at", "updated_at"])
    is_google_connected = bool(profile.google_subject_id)
    if is_google_connected:
        _sync_top_user_task_verified_status(profile, force=True, cooldown_seconds=0)
    facebook_profile = getattr(request.user, "facebook_profile", None)
    is_facebook_connected = bool(facebook_profile and facebook_profile.facebook_subject_id)

    if task_mode not in {"youtube", "facebook"}:
        if is_facebook_connected and not is_google_connected:
            task_mode = "facebook"
        else:
            task_mode = "youtube"
    recalculate_profile_score(profile)
    _rebalance_active_top_user_tasks(now, online_minutes=10)
    is_user_active = bool(profile.active_status)
    unresolved_task_count = TopUserSubscribeTask.objects.filter(
        profile=profile,
    ).exclude(
        Q(verified_status=True)
    ).count()
    youtube_subscribed_sample = []
    youtube_subscribed_count = 0
    if is_google_connected and unresolved_task_count > 0:
        try:
            access_token = ensure_valid_access_token(profile)
            youtube_subscribed_sample = fetch_my_subscriptions(
                access_token,
                limit=unresolved_task_count,
            )
            youtube_subscribed_count = len(youtube_subscribed_sample)
        except Exception:
            youtube_subscribed_sample = []
            youtube_subscribed_count = 0
    total_view_hours = round((profile.channel_total_view_count or 0) / 60, 2)
    assigned_channels = []
    verified_subscribed_channels = []
    subscribed_channel_rows = []
    assigned_task_qs = (
        TopUserSubscribeTask.objects.select_related("target_profile__user")
        .filter(profile=profile)
        .exclude(Q(verified_status=True))
        .order_by("-target_profile__score", "-updated_at", "-created_at")
    )
    top_score_profiles = [task.target_profile for task in assigned_task_qs]
    top_user_subscribe_tasks = (
        TopUserSubscribeTask.objects.select_related("target_profile__user")
        .filter(profile=profile)
        .order_by("-updated_at", "-created_at")
    )
    facebook_follow_tasks = []
    if task_mode == "facebook":
        facebook_candidates = list(
            FacebookProfile.objects.select_related("user", "user__subscriber_profile")
            .exclude(user=request.user)
            .exclude(facebook_subject_id__isnull=True)
            .exclude(facebook_subject_id="")
            .order_by("-connected_at", "-updated_at")
        )
        target_profile_ids = [
            fb.user.subscriber_profile.id
            for fb in facebook_candidates
            if hasattr(fb.user, "subscriber_profile")
        ]
        task_map = {
            task.target_facebook_profile_id: task
            for task in FacebookTaskAssing.objects.filter(
                profile=profile,
                target_facebook_profile_id__in=[fb.id for fb in facebook_candidates],
            )
        }
        for fb in facebook_candidates:
            target_profile = getattr(fb.user, "subscriber_profile", None)
            if not target_profile:
                continue
            existing_task = task_map.get(fb.id)
            facebook_follow_tasks.append(
                {
                    "target_profile": target_profile,
                    "target_facebook": fb,
                    "facebook_followed_status": bool(existing_task and existing_task.followed_status),
                }
            )

    return render(
        request,
        "subscribers/tasks.html",
        {
            "profile": profile,
            "task_mode": task_mode,
            "is_user_active": is_user_active,
            "is_google_connected": is_google_connected,
            "is_facebook_connected": False,
            "facebook_profile": facebook_profile,
            "facebook_oauth_ready": False,
            "total_subscriber": profile.subscriber_change_since_last_scan,
            "total_subscribed_channel": profile.subscribed_channel_count,
            "your_added_subscribed_count": profile.subscriber_change_since_last_scan,
            "video_total_view": profile.channel_total_view_count,
            "total_view_hours": total_view_hours,
            "video_total_count": profile.channel_video_count,
            "unresolved_task_count": unresolved_task_count,
            "youtube_subscribed_count": youtube_subscribed_count,
            "youtube_subscribed_sample": youtube_subscribed_sample,
            "assigned_channels": assigned_channels,
            "verified_subscribed_channels": verified_subscribed_channels,
            "subscribed_channel_rows": subscribed_channel_rows,
            "top_score_profiles": top_score_profiles,
            "top_user_subscribe_tasks": top_user_subscribe_tasks,
            "facebook_follow_tasks": facebook_follow_tasks,
        },
    )


@login_required
@require_POST
def subscribe_assigned_channel(request):
    messages.info(request, "Assigned-channel tasks were removed in the cleanup.")
    return redirect("subscribers:youtube_tasks")


@login_required
@require_POST
def mark_facebook_followed(request):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    target_profile_id_raw = (request.POST.get("target_profile_id") or "").strip()
    if not target_profile_id_raw.isdigit():
        messages.error(request, "Invalid target profile id.")
        return redirect("subscribers:facebook_tasks")

    target_profile = (
        SubscriberProfile.objects.select_related("user")
        .filter(id=int(target_profile_id_raw))
        .first()
    )
    if target_profile is None:
        messages.error(request, "Target user profile not found.")
        return redirect("subscribers:facebook_tasks")

    if target_profile.user_id == request.user.id:
        messages.warning(request, "You cannot follow your own Facebook profile as a task.")
        return redirect("subscribers:facebook_tasks")

    target_facebook_profile = getattr(target_profile.user, "facebook_profile", None)
    if not target_facebook_profile or not target_facebook_profile.facebook_subject_id:
        messages.error(request, "This user has not connected a Facebook profile yet.")
        return redirect("subscribers:facebook_tasks")

    task, _ = FacebookTaskAssing.objects.get_or_create(
        profile=profile,
        target_facebook_profile=target_facebook_profile,
    )
    if not task.followed_status:
        task.followed_status = True
        task.followed_at = timezone.now()
        task.save(
            update_fields=[
                "followed_status",
                "followed_at",
                "last_attempt_at",
                "updated_at",
            ]
        )
        messages.success(request, f"Facebook follow marked for {target_profile.user.username}.")
    else:
        messages.info(request, "Facebook follow was already marked for this task.")
    return redirect("subscribers:facebook_tasks")


@login_required
@require_POST
def subscribe_top_user_channel(request):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    target_profile_id_raw = (request.POST.get("target_profile_id") or "").strip()
    if not target_profile_id_raw.isdigit():
        messages.error(request, "Invalid target profile id.")
        return redirect("subscribers:youtube_tasks")

    target_profile = (
        SubscriberProfile.objects.select_related("user")
        .filter(id=int(target_profile_id_raw))
        .first()
    )
    if target_profile is None:
        messages.error(request, "Target user profile not found.")
        return redirect("subscribers:youtube_tasks")

    if target_profile.user_id == request.user.id:
        messages.warning(request, "You cannot subscribe to your own channel.")
        return redirect("subscribers:youtube_tasks")

    top_user_task, _ = TopUserSubscribeTask.objects.get_or_create(
        profile=profile,
        target_profile=target_profile,
    )

    if not profile.google_subject_id:
        top_user_task.error_message = "Google account not connected."
        top_user_task.save(update_fields=["error_message", "last_attempt_at", "updated_at"])
        messages.error(request, "Connect Google first to subscribe directly from app.")
        return redirect("subscribers:youtube_tasks")

    if not target_profile.channel_id:
        top_user_task.error_message = "Target user is missing channel_id."
        top_user_task.save(update_fields=["error_message", "last_attempt_at", "updated_at"])
        messages.error(
            request,
            f"User '{target_profile.user.username}' is missing channel_id.",
        )
        return redirect("subscribers:youtube_tasks")

    try:
        access_token = ensure_valid_access_token(profile)
        subscribe_to_channel(access_token, target_profile.channel_id)
        messages.success(request, f"Subscribed to {target_profile.user.username} successfully.")
        was_verified = top_user_task.verified_status
        top_user_task.verified_status = True
        top_user_task.subscribed_at = timezone.now()
        top_user_task.error_message = ""
        top_user_task.save(
            update_fields=["verified_status", "subscribed_at", "error_message", "last_attempt_at", "updated_at"]
        )
        if not was_verified:
            _transfer_score_for_verified_task(top_user_task)
        recalculate_profile_score(profile)
    except YouTubeOAuthError as exc:
        error_text = str(exc)
        if "subscriptionDuplicate" in error_text:
            messages.info(request, f"You are already subscribed to {target_profile.user.username}.")
            was_verified = top_user_task.verified_status
            top_user_task.verified_status = True
            if top_user_task.subscribed_at is None:
                top_user_task.subscribed_at = timezone.now()
            top_user_task.error_message = ""
            top_user_task.save(
                update_fields=["verified_status", "subscribed_at", "error_message", "last_attempt_at", "updated_at"]
            )
            if not was_verified:
                _transfer_score_for_verified_task(top_user_task)
            recalculate_profile_score(profile)
        elif "insufficientPermissions" in error_text:
            top_user_task.error_message = "Missing YouTube write permission."
            top_user_task.save(update_fields=["error_message", "last_attempt_at", "updated_at"])
            messages.error(
                request,
                "Missing YouTube write permission. Reconnect Google and allow subscription access.",
            )
        elif "Subscribing to your own channel is not supported" in error_text:
            top_user_task.error_message = "Cannot subscribe to own channel."
            top_user_task.save(update_fields=["error_message", "last_attempt_at", "updated_at"])
            messages.warning(request, "You cannot subscribe to your own channel.")
        else:
            top_user_task.error_message = error_text[:1000]
            top_user_task.save(update_fields=["error_message", "last_attempt_at", "updated_at"])
            messages.error(request, f"Subscribe failed: {exc}")

    return redirect("subscribers:youtube_tasks")


@require_GET
def facebook_connect(request):
    if _required_facebook_settings_missing():
        messages.error(
            request,
            "Facebook OAuth settings are missing. Add FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET first.",
        )
        return redirect("subscribers:login")

    state = secrets.token_urlsafe(24)
    request.session["facebook_oauth_state"] = state
    use_basic_scope = request.GET.get("basic") == "1"
    request.session["facebook_oauth_basic_scope"] = use_basic_scope
    requested_scope = FACEBOOK_BASIC_SCOPE if use_basic_scope else None
    return redirect(build_facebook_authorize_url(state, scope=requested_scope))


@require_GET
def facebook_callback(request):
    if request.GET.get("error"):
        error_desc = request.GET.get("error_description", "No description provided")
        error_code = request.GET.get("error") or ""
        if "invalid scope" in error_desc.lower() or "invalid_scope" in error_code.lower():
            messages.warning(
                request,
                "Page permissions are not available yet on this Meta app. Retrying with basic Facebook login.",
            )
            return redirect(f"{reverse('subscribers:facebook_connect')}?basic=1")
        messages.error(request, f"Facebook OAuth Error ({error_code}): {error_desc}")
        return redirect("subscribers:login")

    expected_state = request.session.pop("facebook_oauth_state", "")
    using_basic_scope = bool(request.session.pop("facebook_oauth_basic_scope", False))
    incoming_state = request.GET.get("state", "")
    if not expected_state or incoming_state != expected_state:
        messages.error(request, "Invalid Facebook OAuth state. Please try again.")
        return redirect("subscribers:login")

    code = request.GET.get("code", "")
    if not code:
        messages.error(request, "Facebook did not return an authorization code.")
        return redirect("subscribers:login")

    try:
        token_data = exchange_facebook_code_for_token(code)
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise FacebookOAuthError("No access token returned from Facebook.")

        userinfo = fetch_facebook_userinfo(access_token)
        facebook_subject_id = (userinfo.get("id") or "").strip()
        if not facebook_subject_id:
            raise FacebookOAuthError("Facebook user ID not returned.")

        picture_data = ((userinfo.get("picture") or {}).get("data") or {})
        user, facebook_profile = _get_or_create_user_for_facebook(userinfo)
        login(request, user)
        facebook_profile.facebook_subject_id = facebook_subject_id
        facebook_profile.facebook_email = (userinfo.get("email") or "").strip().lower()
        facebook_profile.name = (userinfo.get("name") or "").strip()
        facebook_profile.profile_picture_url = picture_data.get("url") or ""
        facebook_profile.profile_url = f"https://www.facebook.com/{facebook_subject_id}"
        facebook_profile.access_token = access_token
        facebook_profile.token_expiry = calculate_token_expiry(token_data.get("expires_in"))
        facebook_profile.connected_at = timezone.now()

        pages = fetch_facebook_pages(access_token)
        if pages and not using_basic_scope:
            def _page_followers_value(row):
                return int(row.get("followers_count") or row.get("fan_count") or 0)

            best_page = max(pages, key=_page_followers_value)
            facebook_profile.page_id = (best_page.get("id") or "").strip()
            facebook_profile.page_name = (best_page.get("name") or "").strip()
            facebook_profile.page_url = (best_page.get("link") or "").strip()
            facebook_profile.page_access_token = (best_page.get("access_token") or "").strip()
            facebook_profile.page_followers_count = _page_followers_value(best_page)

        facebook_profile.save()

        profile, _ = SubscriberProfile.objects.get_or_create(user=user)
        profile.facebook_profile_url = facebook_profile.page_url or facebook_profile.profile_url
        profile.facebook_followers_count = int(facebook_profile.page_followers_count or 0)
        profile.save(update_fields=["facebook_profile_url", "facebook_followers_count", "updated_at"])

        messages.success(request, "Facebook profile connected successfully.")
    except FacebookOAuthError as exc:
        messages.error(request, f"Facebook connection failed: {exc}")
    except Exception as exc:
        messages.error(request, f"Unexpected Facebook connection error: {exc}")

    return redirect("subscribers:facebook_tasks")


@require_GET
def google_connect(request):
    if _required_google_settings_missing():
        messages.error(
            request,
            "Google OAuth settings are missing. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET first.",
        )
        return redirect("subscribers:login")

    state = secrets.token_urlsafe(24)
    request.session["google_oauth_state"] = state
    return redirect(build_google_authorize_url(state))


@require_GET
def google_callback(request):
    if request.GET.get("error"):
        error_desc = request.GET.get("error_description", "No description provided")
        error_code = request.GET.get("error")
        messages.error(request, f'Google OAuth Error ({error_code}): {error_desc}')
        return redirect("subscribers:login")

    expected_state = request.session.pop("google_oauth_state", "")
    incoming_state = request.GET.get("state", "")
    if not expected_state or incoming_state != expected_state:
        messages.error(request, "Invalid OAuth state. Please try again.")
        return redirect("subscribers:login")

    code = request.GET.get("code", "")
    if not code:
        messages.error(request, "Google did not return an authorization code.")
        return redirect("subscribers:login")

    try:
        token_data = exchange_code_for_token(code)
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise YouTubeOAuthError("No access token returned from Google.")
        userinfo = fetch_userinfo(access_token)
        if not userinfo.get("sub"):
            raise YouTubeOAuthError("Google user ID not returned.")

        user, profile = _get_or_create_user_for_google(userinfo)
        login(request, user)

        profile.google_subject_id = userinfo.get("sub", "")
        profile.google_email = (userinfo.get("email") or "").strip().lower()
        profile.access_token = access_token
        if token_data.get("refresh_token"):
            profile.refresh_token = token_data["refresh_token"]
        profile.token_expiry = calculate_token_expiry(token_data.get("expires_in"))
        profile.save()

        # Try to at least get the basic channel handle and title immediately
        try:
            summary = fetch_authenticated_channel_summary(access_token)
            profile.channel_id = summary['channel_id']
            profile.channel_title = summary['channel_title']
            profile.handle = summary['handle']
            profile.channel_avatar = summary['thumbnail']
            profile.channel_total_view_count = summary['view_count']
            profile.channel_video_count = summary['video_count']
            profile.save()
            _rename_user_to_handle(user, profile.handle)
        except Exception as exc:
            messages.warning(request, f"Connected to Google, but channel summary fetch failed: {exc}")

        # Perform a full scan to populate subscription counts and verification
        try:
            scan_profile(profile)
        except Exception as exc:
            messages.warning(request, f"Google connected, but initial channel scan failed: {exc}")

        messages.success(request, "Google login successful!")
    except Exception as exc:
        messages.error(request, f"Google login failed: {exc}")
        return redirect("subscribers:login")

    return redirect("subscribers:home")


@login_required
def sign_out(request):
    logout(request)
    messages.info(request, "You are logged out.")
    return redirect("subscribers:home")


# ============================================================================
# VIDEO PLAYBACK AND WATCH SYSTEM VIEWS
# ============================================================================

@login_required
@require_GET
def watch_video_root(request):
    """Video-table-only watch page root."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    first_video = Video.objects.order_by("-updated_at").first()
    if first_video:
        return redirect("subscribers:watch_video", task_id=first_video.id)
    featured_videos = []
    try:
        featured_videos = _featured_videos_for_watch_page(profile)
    except Exception:
        logger.exception("Failed loading featured videos for watch root")

    return render(
        request,
        "subscribers/watch_video.html",
        {
            "profile": profile,
            "watch_task": None,
            "video": None,
            "progress": {"watch_time_seconds": 0, "progress_percentage": 0},
            "is_completed": False,
            "featured_videos": featured_videos,
            "initial_video_id": (featured_videos[0]["video_id"] if featured_videos else "1jtVBdA7Q9A"),
            "initial_video_title": (featured_videos[0]["title"] if featured_videos else "Starting Video"),
            "initial_video_channel": (
                featured_videos[0]["channel_title"] if featured_videos else "YouTube Channel"
            ),
        },
    )


def _get_or_create_channel_video(
    video_url: str,
    owner_user=None,
) -> Video:
    defaults = {
        "owner_user": owner_user,
        "duration_seconds": 0,
    }
    video, _ = Video.objects.get_or_create(video_url=video_url, defaults=defaults)
    if video.owner_user_id is None and owner_user:
        video.owner_user = owner_user
        video.save(update_fields=["owner_user", "updated_at"])
    return video


@login_required
@require_POST
def share_channel_video(request, youtube_video_id):
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)

    available_score = available_video_score(profile)
    if available_score <= 0:
        messages.error(request, "You need available video score to share a video with other users.")
        return redirect('subscribers:watch_video_root')

    video_url = request.POST.get('video_url', '').strip() or f'https://www.youtube.com/watch?v={youtube_video_id}'

    video = _get_or_create_channel_video(
        video_url=video_url,
        owner_user=request.user,
    )

    current_category = (profile.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    target_profiles_qs = SubscriberProfile.objects.filter(
        active_status=True,
        active_status_for_video=True,
    ).exclude(id=profile.id).order_by('-video_score', '-updated_at')
    target_profiles = list(target_profiles_qs)
    if not target_profiles:
        messages.warning(request, "No other active users are available to receive your video right now.")
        return redirect('subscribers:watch_video_root')
    target_profiles.sort(
        key=lambda p: (
            0 if (p.category or SubscriberProfile.CATEGORY_OTHER).strip().lower() == current_category else 1,
            -int(p.video_score or 0),
            -int(p.id or 0),
        )
    )

    # Dynamic target formula from remaining score:
    # 1-4 minutes -> 1 user, 5-8 -> 2 users, 9-12 -> 3 users, etc.
    # Equivalent to: floor((remaining_score - 1) / 4) + 1
    # Also capped by number of candidate targets and available score.
    if available_score <= 0:
        max_targets = 0
    else:
        max_targets = ((available_score - 1) // 4) + 1
        max_targets = min(max_targets, available_score, len(target_profiles))

    assigned_count = assign_video_from_source_profile(
        source_profile=profile,
        video=video,
        target_profiles=target_profiles,
        minutes_per_target=1,
        max_targets=max_targets,
        min_watch_time_seconds=60,
    )

    if assigned_count > 0:
        messages.success(request, f"Your video was shared with {assigned_count} other user(s).")
    else:
        messages.warning(request, "No new tasks were assigned. Other users may already have this video or the source score is reserved.")

    return redirect('subscribers:watch_video_root')


@login_required
@require_GET
def watch_video(request, task_id):
    """Display the video player using video table only (task_id treated as video id)."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)

    video = Video.objects.filter(id=task_id).first()
    if not video:
        messages.error(request, "Video not found.")
        return redirect("subscribers:watch_video_root")

    min_watch = max((video.duration_seconds or 0) // 2, 1) if (video.duration_seconds or 0) > 0 else 60
    progress_pct = min(int((video.watched_time_seconds / min_watch) * 100), 100) if min_watch > 0 else 0
    progress = {
        "watch_time_seconds": int(video.watched_time_seconds or 0),
        "progress_percentage": progress_pct,
    }
    featured_videos = []
    try:
        featured_videos = _featured_videos_for_watch_page(profile)
    except Exception:
        logger.exception("Failed loading featured videos for watch page")

    initial_video_id = _extract_youtube_video_id(video.video_url) or "1jtVBdA7Q9A"
    initial_video_title = initial_video_id
    initial_video_channel = "YouTube Channel"
    
    context = {
        'profile': profile,
        'watch_task': None,
        'video': video,
        'progress': progress,
        'is_completed': video.status == Video.STATUS_COMPLETE,
        'featured_videos': featured_videos,
        'initial_video_id': initial_video_id,
        'initial_video_title': initial_video_title,
        'initial_video_channel': initial_video_channel,
    }
    
    return render(request, 'subscribers/watch_video.html', context)


@login_required
@require_POST
def start_watch_session(request, task_id):
    """Initialize a simple session for video-table-only flow."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    video = Video.objects.filter(id=task_id).first()
    if not video:
        return JsonResponse({"success": False, "error": "Video not found"}, status=404)

    now = timezone.now()
    session_id = secrets.token_urlsafe(24)
    request.session[f"watch_session_video_{task_id}"] = session_id
    request.session.modified = True
    if not profile.active_status_for_video or not profile.active_status_for_youtube:
        profile.active_status_for_video = True
        profile.active_status_for_youtube = True
        profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "updated_at"])
    profile.last_tasks_entry_at = now
    profile.save(update_fields=["last_tasks_entry_at", "updated_at"])

    min_watch = max((video.duration_seconds or 0) // 2, 1) if (video.duration_seconds or 0) > 0 else 60
    return JsonResponse(
        {
            "success": True,
            "session_id": session_id,
            "task_id": video.id,
            "min_watch_time_seconds": min_watch,
            "already_watched_seconds": int(video.watched_time_seconds or 0),
        }
    )


def _get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@login_required
@require_POST
def save_watch_time(request):
    """Save active watch-time and credit both viewer and source owner."""
    viewer_profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid JSON body"}, status=400)

    video_id = (data.get("video_id") or "").strip()
    watch_time = int(data.get("watch_time", 0) or 0)
    watch_time = max(min(watch_time, 60), 0)

    if not video_id:
        return JsonResponse({"success": False, "error": "video_id is required"}, status=400)
    if watch_time <= 0:
        return JsonResponse({"success": False, "error": "watch_time must be greater than 0"}, status=400)

    owner_profile = None
    video = Video.objects.filter(video_url__icontains=video_id).order_by("-updated_at").first()
    if video:
        if video.owner_user_id:
            owner_profile = getattr(video.owner_user, "subscriber_profile", None)

    with transaction.atomic():
        owner_reserved_added = 0

        video_status = None
        if video:
            if video.owner_user_id is None:
                video.owner_user = request.user
            video.watched_time_seconds = F("watched_time_seconds") + watch_time
            video.save(update_fields=["owner_user", "watched_time_seconds", "updated_at"])
            video.refresh_from_db(fields=["duration_seconds", "watched_time_seconds", "status"])
            if video.duration_seconds > 0 and video.watched_time_seconds >= max(video.duration_seconds // 2, 1):
                video.status = Video.STATUS_COMPLETE
            elif video.status == Video.STATUS_COMPLETE:
                video.status = Video.STATUS_PENDING
            video.save(update_fields=["status", "updated_at"])
            video_status = video.status

        assignment_completed = False
        if video:
            watch_task = (
                VideoWatchTask.objects.select_related("source_profile")
                .filter(profile=viewer_profile, video=video, verified_status=False)
                .order_by("-created_at")
                .first()
            )
            if watch_task:
                watch_task.watch_time_seconds = int(watch_task.watch_time_seconds or 0) + watch_time
                completion_threshold = max(int((int(video.duration_seconds or 0)) * 0.8), 1)
                if watch_task.watch_time_seconds >= completion_threshold:
                    watch_task.verified_status = True
                    watch_task.status = VideoWatchTask.STATUS_COMPLETE
                    watch_task.verified_at = timezone.now()
                    assignment_completed = True

                    reward_minutes = max(int(watch_task.assigned_video_score or 0), 1)
                    viewer_profile.video_score = F("video_score") + reward_minutes
                    viewer_profile.save(update_fields=["video_score", "updated_at"])

                    if watch_task.source_profile_id:
                        owner_for_task = watch_task.source_profile
                        owner_for_task.refresh_from_db(fields=["video_score", "video_score_reserved"])
                        owner_for_task.video_score_reserved = max(int(owner_for_task.video_score_reserved or 0) - reward_minutes, 0)
                        owner_for_task.video_score = max(int(owner_for_task.video_score or 0) - reward_minutes, 0)
                        owner_for_task.save(update_fields=["video_score", "video_score_reserved", "updated_at"])
                else:
                    watch_task.status = VideoWatchTask.STATUS_ACTIVE
                watch_task.save(update_fields=["watch_time_seconds", "verified_status", "verified_at", "status", "updated_at"])

    viewer_profile.refresh_from_db(fields=["video_score"])

    return JsonResponse(
        {
            "success": True,
            "message": "Watch time saved and owner reserve updated",
            "video_id": video_id,
            "added_watch_time": watch_time,
            "viewer_video_score": int(viewer_profile.video_score or 0),
            "owner_reserved_added": owner_reserved_added,
            "owner_profile_id": (owner_profile.id if owner_profile else None),
            "video_status": video_status,
            "assignment_completed": assignment_completed,
        }
    )


@login_required
@require_POST
def update_watch_time(request, task_id):
    """Heartbeat endpoint using video table only."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    video = Video.objects.filter(id=task_id).first()
    if not video:
        return JsonResponse({"success": False, "error": "Video not found"}, status=404)

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid request data"}, status=400)

    session_id = (data.get("session_id") or "").strip()
    expected_session = request.session.get(f"watch_session_video_{task_id}", "")
    if not session_id or not expected_session or session_id != expected_session:
        return JsonResponse({"success": False, "error": "Invalid or expired watch session"}, status=403)

    is_tab_active = bool(data.get("is_tab_active", False))
    is_player_playing = bool(data.get("is_player_playing", False))
    valid_seconds = int(data.get("valid_watch_seconds", 0) or 0)
    valid_seconds = max(0, min(valid_seconds, HEARTBEAT_MAX_SECONDS))

    now = timezone.now()
    if is_tab_active and is_player_playing:
        profile.active_status_for_video = True
        profile.active_status_for_youtube = True
        profile.last_tasks_entry_at = now
        accepted_seconds = valid_seconds
    else:
        profile.active_status_for_video = False
        profile.active_status_for_youtube = False
        accepted_seconds = 0
    profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "last_tasks_entry_at", "updated_at"])

    # Single source of truth:
    # watch-time accumulation is persisted only via save_watch_time endpoint.
    # Heartbeat endpoint now tracks activity/session state only.

    video.refresh_from_db(fields=["duration_seconds", "watched_time_seconds", "status", "updated_at"])
    completion_threshold = max((video.duration_seconds or 0) // 2, 1) if (video.duration_seconds or 0) > 0 else 60
    if video.watched_time_seconds >= completion_threshold:
        video.status = Video.STATUS_COMPLETE
    else:
        inactive_for_release = (not is_tab_active or not is_player_playing) and profile.last_tasks_entry_at and (now - profile.last_tasks_entry_at >= timedelta(minutes=10))
        if inactive_for_release:
            video.status = Video.STATUS_RELEASE
            releasable_task = (
                VideoWatchTask.objects.select_related("source_profile")
                .filter(profile=profile, video=video, verified_status=False, status__in=[VideoWatchTask.STATUS_PENDING, VideoWatchTask.STATUS_ACTIVE])
                .order_by("-created_at")
                .first()
            )
            if releasable_task and releasable_task.source_profile_id:
                release_minutes = max(int(releasable_task.assigned_video_score or 0), 0)
                if release_minutes > 0:
                    owner_release = releasable_task.source_profile
                    owner_release.refresh_from_db(fields=["video_score_reserved"])
                    owner_release.video_score_reserved = max(int(owner_release.video_score_reserved or 0) - release_minutes, 0)
                    owner_release.save(update_fields=["video_score_reserved", "updated_at"])
                releasable_task.status = VideoWatchTask.STATUS_RELEASE
                releasable_task.save(update_fields=["status", "updated_at"])
        elif now - video.updated_at >= timedelta(minutes=30):
            video.status = Video.STATUS_HOLD
        else:
            video.status = Video.STATUS_PENDING
    video.save(update_fields=["status", "updated_at"])

    progress_pct = min(int((video.watched_time_seconds / completion_threshold) * 100), 100)
    return JsonResponse({
        "success": True,
        "accepted": accepted_seconds > 0,
        "accepted_seconds": 0,
        "message": "Watch heartbeat processed (no watch-time write here)",
        "progress": {
            "watch_time_seconds": int(video.watched_time_seconds or 0),
            "min_watch_time_seconds": int(completion_threshold),
            "remaining_seconds": max(int(completion_threshold - video.watched_time_seconds), 0),
            "progress_percentage": progress_pct,
            "is_completed": video.status == Video.STATUS_COMPLETE,
            "status": video.status,
        },
        "is_completed": video.status == Video.STATUS_COMPLETE,
    })


@login_required
@require_POST
def complete_watch_task(request, task_id):
    """Mark video complete based on video-table-only threshold."""
    video = Video.objects.filter(id=task_id).first()
    if not video:
        return JsonResponse({"success": False, "error": "Video not found"}, status=404)

    completion_threshold = max((video.duration_seconds or 0) // 2, 1) if (video.duration_seconds or 0) > 0 else 60
    if int(video.watched_time_seconds or 0) >= completion_threshold:
        video.status = Video.STATUS_COMPLETE
        video.save(update_fields=["status", "updated_at"])
        return JsonResponse({
            "success": True,
            "message": "Video completed.",
            "is_completed": True,
            "progress": {
                "watch_time_seconds": int(video.watched_time_seconds or 0),
                "min_watch_time_seconds": int(completion_threshold),
                "remaining_seconds": 0,
                "progress_percentage": 100,
                "is_completed": True,
                "status": video.status,
            },
        })

    remaining = max(completion_threshold - int(video.watched_time_seconds or 0), 0)
    return JsonResponse(
        {
            "success": False,
            "message": f"Minimum watch time not met. {remaining} seconds remaining.",
            "is_completed": False,
            "progress": {
                "watch_time_seconds": int(video.watched_time_seconds or 0),
                "min_watch_time_seconds": int(completion_threshold),
                "remaining_seconds": remaining,
                "progress_percentage": min(int((int(video.watched_time_seconds or 0) / completion_threshold) * 100), 100),
                "is_completed": False,
                "status": video.status,
            },
        },
        status=400,
    )


@login_required
@require_GET
def video_score_details(request):
    """Show video score and reserved score details."""
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    user_video_qs = Video.objects.filter(owner_user=request.user)
    total_watch_seconds = sum(int(v.watched_time_seconds or 0) for v in user_video_qs)
    total_watch_time_minutes = total_watch_seconds // 60
    
    context = {
        'profile': profile,
        'video_score': profile.video_score,
        'video_score_reserved': profile.video_score_reserved,
        'total_video_score': profile.video_score + profile.video_score_reserved,
        'total_watch_time_minutes': total_watch_time_minutes,
        'watch_events_count': user_video_qs.count(),
        'recent_watch_events': user_video_qs.order_by('-updated_at')[:10],
    }
    
    return render(request, 'subscribers/video_score_details.html', context)


# Import video-related functions from services at module level for views
from .services import (  # noqa: E402
    transfer_video_score_to_available,
    use_video_score,
    available_video_score,
    assign_video_from_source_profile,
)
