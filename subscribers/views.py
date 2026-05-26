import logging
import json
import io
import os
import secrets
import re
import shutil
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import parse_qs

from django.conf import settings 
from django.core.cache import cache
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Sum, Value, When
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    AdminVideo,
    FacebookProfile,
    FacebookTaskAssing,
    ManualFacebookFollowTaskAssign,
    ManualFacebookProfile,
    ManualSubscribeProfile,
    ManualSubscribeTaskAssign,
    SubscriberProfile,
    TopUserSubscribeTask,
    User,
    VerificationImage,
    Video,
    VideoProfile,
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
from .ocr import get_ocr_text

logger = logging.getLogger(__name__)

HEARTBEAT_MIN_SECONDS = 2
HEARTBEAT_MAX_SECONDS = 15
MAX_VALID_SECONDS_PER_MINUTE = 45
MAX_SEEKS_PER_WINDOW = 6
MAX_PAUSES_PER_WINDOW = 12
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{7,11}$")
STRONG_PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{6,}$")
HANDLE_REGEX = re.compile(r"@([a-z0-9._-]{3,40})", re.IGNORECASE)
NEW_ACTIVITY_REGEX = re.compile(r"\bnew\s+activity\b", re.IGNORECASE)
MOST_RELEVANT_REGEX = re.compile(r"\bmost\s+relevant\b", re.IGNORECASE)
LOYAL_SCORE_KEYWORDS = [ 
    "all subscriptions",
    "new activity",
    "home",
    "shorts",
    "subscriptions",
    "you",
] 

REBALANCE_COOLDOWN_SECONDS = int(getattr(settings, "REBALANCE_COOLDOWN_SECONDS", 30))
ACTIVITY_WINDOW_MINUTES = int(getattr(settings, "TASK_ACTIVITY_WINDOW_MINUTES", 5))
LOCAL_ASSIGN_CAP = int(getattr(settings, "LOCAL_ASSIGN_CAP", 3))
MANUAL_PENDING_STATUSES = (
    ManualSubscribeTaskAssign.STATUS_ASSIGNED,
    ManualSubscribeTaskAssign.STATUS_UNVERIFIED,
)
FACEBOOK_PENDING_STATUSES = (
    ManualFacebookFollowTaskAssign.STATUS_ASSIGNED,
    ManualFacebookFollowTaskAssign.STATUS_UNVERIFIED,
)


def _admin_video_urls() -> dict:
    row = AdminVideo.objects.filter(pk=1).first()

    def _file_url(file_field_name: str) -> str:
        file_obj = getattr(row, file_field_name, None) if row else None
        return getattr(file_obj, "url", "") or ""

    home_video_url = _file_url("home_video_file")

    return {
        "home_video_url": home_video_url,
        "home_video_is_file": bool(home_video_url),
        "task_video_url_subscribe": home_video_url,
        "task_video_url_subscribe_is_file": bool(home_video_url),
        "task_video_url_subscribe_verify": home_video_url,
        "task_video_url_subscribe_verify_is_file": bool(home_video_url),
        "task_video_url_facebook": home_video_url,
        "task_video_url_facebook_is_file": bool(home_video_url),
        "task_video_url_facebook_verify": home_video_url,
        "task_video_url_facebook_verify_is_file": bool(home_video_url),
    }


def _run_throttled_rebalance(now, mode: str, *, online_minutes: int = 10) -> bool:
    """
    Run rebalance at most once per cooldown window per mode.
    Uses cache lock + timestamp to avoid repeated heavy rebalance queries
    when many users load task pages at the same time.
    """
    cache_key_due = f"rebalance:{mode}:last_run_ts"
    cache_key_lock = f"rebalance:{mode}:lock"
    now_ts = int(now.timestamp())
    last_run_ts = int(cache.get(cache_key_due) or 0)
    if now_ts - last_run_ts < REBALANCE_COOLDOWN_SECONDS:
        return False
    lock_acquired = cache.add(cache_key_lock, "1", timeout=15)
    if not lock_acquired:
        return False
    try:
        # Double-check after lock to avoid thundering herd at boundary.
        last_run_ts = int(cache.get(cache_key_due) or 0)
        if now_ts - last_run_ts < REBALANCE_COOLDOWN_SECONDS:
            return False
        if mode == "google":
            _rebalance_active_top_user_tasks(now, online_minutes=online_minutes)
        elif mode == "manual":
            _rebalance_active_manual_subscribe_tasks(now, online_minutes=online_minutes)
        else:
            return False
        cache.set(cache_key_due, now_ts, timeout=REBALANCE_COOLDOWN_SECONDS * 4)
        return True
    finally:
        cache.delete(cache_key_lock)


def _is_google_user(user) -> bool:
    if getattr(user, "account_mode", User.ACCOUNT_MODE_MANUAL) != User.ACCOUNT_MODE_GOOGLE:
        return False
    profile = SubscriberProfile.objects.filter(user=user).only("google_subject_id").first()
    return bool(profile and profile.google_subject_id)


def _manual_profile_defaults(user, manual_profile=None):
    handle = (getattr(user, "handle", "") or getattr(user, "username", "")).strip()
    category = SubscriberProfile.CATEGORY_OTHER
    if manual_profile and manual_profile.category:
        category = manual_profile.category
    return {
        "handle": handle,
        "category": category,
        "subscribed_channel_count": 0,
        "channel_subscriber_count": 0,
        "subscriber_change_since_last_scan": 0,
        "video_score": 0,
        "video_score_reserved": 0,
        "channel_total_view_count": 0,
        "channel_video_count": 0,
        "active_status": True,
        "active_status_for_youtube": True,
        "active_status_for_video": False,
    }


def _get_or_create_video_profile(user):
    return VideoProfile.objects.get_or_create(user=user)


def _get_google_profile(user):
    return SubscriberProfile.objects.filter(user=user).first()


def _has_valid_manual_handle(manual_profile, user, profile=None) -> bool:
    if manual_profile and (manual_profile.handle or "").strip().startswith("@"):
        return True
    if profile and (profile.handle or "").strip().startswith("@"):
        return True
    return bool((getattr(user, "handle", "") or "").strip().startswith("@"))


def _get_or_create_distribution_profile_for_manual(manual_profile: ManualSubscribeProfile) -> SubscriberProfile | None:
    if not manual_profile or not manual_profile.user_id:
        return None
    profile = SubscriberProfile.objects.filter(user_id=manual_profile.user_id).first()
    desired_handle = (manual_profile.handle or "").strip()
    desired_category = (manual_profile.category or SubscriberProfile.CATEGORY_OTHER).strip() or SubscriberProfile.CATEGORY_OTHER
    if profile is None:
        profile = SubscriberProfile.objects.create(
            user_id=manual_profile.user_id,
            handle=desired_handle,
            category=desired_category,
            active_status=True,
        )
        return profile

    updates = []
    if desired_handle and profile.handle != desired_handle:
        profile.handle = desired_handle
        updates.append("handle")
    if profile.category != desired_category:
        profile.category = desired_category
        updates.append("category")
    if not profile.active_status:
        profile.active_status = True
        updates.append("active_status")
    if updates:
        updates.append("updated_at")
        profile.save(update_fields=updates)
    return profile


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


def _featured_videos_for_watch_page(current_profile: SubscriberProfile | None, current_user) -> list[dict]:
    """
    Build a distributed video task pool for active users.
    Rule:
    - only videos from active users with video_score > 0 are included
    - each video row counts as one task item
    """
    eligible_owner_ids = list(
        VideoProfile.objects.filter(active_status_for_video=True, video_score__gt=0)
        .exclude(user_id=current_user.id)
        .values_list("user_id", flat=True)
    )
    if not eligible_owner_ids:
        return []

    owner_profiles_by_user_id = {
        row["user_id"]: row["category"]
        for row in SubscriberProfile.objects.filter(user_id__in=eligible_owner_ids).values("user_id", "category")
    }
    current_category = (
        (current_profile.category if current_profile else SubscriberProfile.CATEGORY_OTHER)
        or SubscriberProfile.CATEGORY_OTHER
    ).strip().lower()
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
        yt_id = _extract_youtube_video_id(item.video_url) or "dQw4w9WgXcQ"
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


def _is_same_manual_category(receiver: ManualSubscribeProfile, target: ManualSubscribeProfile) -> bool:
    receiver_category = (receiver.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    target_category = (target.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    return receiver_category == target_category


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
                score=F("score") + released_count
            ) 


def _reserve_targets_for_tasks(tasks: list[TopUserSubscribeTask]) -> None: 
    if not tasks:
        return

    reserved_by_target_id = Counter(task.target_profile_id for task in tasks) 
    with transaction.atomic(): 
        TopUserSubscribeTask.objects.bulk_create(tasks) 
        for target_id, reserved_count in reserved_by_target_id.items(): 
            SubscriberProfile.objects.filter(id=target_id).update( 
                score=Case(
                    When(score__gte=reserved_count, then=F("score") - reserved_count),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ) 


def _release_unverified_manual_task_reservations(task_qs) -> None:  
    task_rows = list(task_qs.values_list("id", "target_profile_id"))
    if not task_rows:
        return

    task_ids = [task_id for task_id, _ in task_rows]
    reserved_by_target_id = Counter(target_id for _, target_id in task_rows) 
    with transaction.atomic():  
        ManualSubscribeTaskAssign.objects.filter(id__in=task_ids).update(
            subscribed_status=ManualSubscribeTaskAssign.STATUS_RELEASED,
            active_status=False,
        )
        for target_id, released_count in reserved_by_target_id.items():  
            ManualSubscribeProfile.objects.filter(user__subscriber_profiles__id=target_id).update(  
                sub_score=F("sub_score") + released_count 
            )  


def _reserve_targets_for_manual_tasks(tasks: list[ManualSubscribeTaskAssign]) -> None:  
    if not tasks:
        return

    reserved_by_target_id = Counter(task.target_profile_id for task in tasks)
    with transaction.atomic():
        receiver_user_ids = list({task.user_id for task in tasks})
        target_profile_ids = list({task.target_profile_id for task in tasks})
        existing_by_pair = {
            (row.user_id, row.target_profile_id): row
            for row in ManualSubscribeTaskAssign.objects.filter(
                user_id__in=receiver_user_ids,
                target_profile_id__in=target_profile_ids,
            )
        }
        rows_to_create = []
        rows_to_update = []
        for task in tasks:
            pair = (task.user_id, task.target_profile_id)
            existing = existing_by_pair.get(pair)
            if existing:
                existing.manual_subscribe_profile_id = task.manual_subscribe_profile_id
                existing.subscribed_status = ManualSubscribeTaskAssign.STATUS_ASSIGNED
                existing.active_status = True
                rows_to_update.append(existing)
            else:
                task.subscribed_status = ManualSubscribeTaskAssign.STATUS_ASSIGNED
                task.active_status = True
                rows_to_create.append(task)
        if rows_to_create:
            ManualSubscribeTaskAssign.objects.bulk_create(rows_to_create)
        if rows_to_update:
            ManualSubscribeTaskAssign.objects.bulk_update(
                rows_to_update,
                ["manual_subscribe_profile", "subscribed_status", "active_status", "updated_at"],
            )
        for target_id, reserved_count in reserved_by_target_id.items():  
            ManualSubscribeProfile.objects.filter(user__subscriber_profiles__id=target_id).update(  
                sub_score=Case( 
                    When(sub_score__gte=reserved_count, then=F("sub_score") - reserved_count), 
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ) 


def _rebalance_active_manual_subscribe_tasks(now, *, online_minutes: int = ACTIVITY_WINDOW_MINUTES) -> None:   
    window_start = now - timedelta(minutes=online_minutes) 
    stale_unverified_manual_tasks = ManualSubscribeTaskAssign.objects.filter( 
        subscribed_status__in=MANUAL_PENDING_STATUSES
    ).exclude(
        user__is_active=True,
        user__manual_subscribe_profile_user__active_status_for_subscribe=True,
        user__manual_subscribe_profile_user__last_tasks_entry_at__gte=window_start,
    )
    receiver_manual_profiles = list( 
        ManualSubscribeProfile.objects.select_related("user") 
        .filter( 
            user__is_active=True, 
            last_tasks_entry_at__gte=window_start, 
            active_status_for_subscribe=True,
        )
        .order_by("user_id")
    ) 
    if not receiver_manual_profiles: 
        _release_unverified_manual_task_reservations(stale_unverified_manual_tasks) 
        return 
 
    _release_unverified_manual_task_reservations(stale_unverified_manual_tasks) 

    targets = list( 
        ManualSubscribeProfile.objects.select_related("user") 
        .filter( 
            user__is_active=True, 
            sub_score__gt=0, 
            handle__startswith="@", 
        ) 
        .order_by("-sub_score", "id") 
    ) 
    if not targets:
        return
    for target_row in targets:
        _get_or_create_distribution_profile_for_manual(target_row)

    target_capacity_by_profile_id = { 
        row.user_id: max(int(row.sub_score or 0), 0)
        for row in targets 
    } 
    target_capacity_by_profile_id = {
        target_id: capacity for target_id, capacity in target_capacity_by_profile_id.items() if capacity > 0
    }
    if not target_capacity_by_profile_id:
        return

    total_capacity = sum(target_capacity_by_profile_id.values())
    receiver_count = len(receiver_manual_profiles)
    if total_capacity <= 0 or receiver_count <= 0:
        return

    base_quota = total_capacity // receiver_count
    remainder = total_capacity % receiver_count
    quota_by_receiver_profile_id = {}
    for index, receiver_manual_profile in enumerate(receiver_manual_profiles):
        quota_by_receiver_profile_id[receiver_manual_profile.user_id] = base_quota + (1 if index < remainder else 0)

    target_score_by_profile_id = {row.user_id: int(row.sub_score or 0) for row in targets}
    target_manual_by_profile_id = {row.user_id: row for row in targets}
    remaining_by_target_profile_id = target_capacity_by_profile_id.copy()
    allocated_target_ids_by_receiver_profile_id = {p.user_id: set() for p in receiver_manual_profiles}

    receiver_user_ids = [p.user_id for p in receiver_manual_profiles]
    target_user_ids = list(target_capacity_by_profile_id.keys())
    existing_pair_rows = list(
        ManualSubscribeTaskAssign.objects.filter(
            user_id__in=receiver_user_ids,
            target_profile__user_id__in=target_user_ids,
        ).values_list(
            "user_id",
            "target_profile__user_id",
            "subscribed_status",
        )
    )
    verified_pair_set = {
        (user_id, target_user_id)
        for user_id, target_user_id, status in existing_pair_rows
        if status == ManualSubscribeTaskAssign.STATUS_VERIFIED
    }
    existing_unverified_set = {
        (user_id, target_user_id)
        for user_id, target_user_id, status in existing_pair_rows
        if status in MANUAL_PENDING_STATUSES
    }

    pending_count_by_receiver = Counter(
        user_id
        for user_id, _, status in existing_pair_rows
        if status in MANUAL_PENDING_STATUSES
    )
    effective_quota_by_receiver_profile_id = {
        receiver_id: max(quota_by_receiver_profile_id.get(receiver_id, 0) - pending_count_by_receiver.get(receiver_id, 0), 0)
        for receiver_id in quota_by_receiver_profile_id
    }
    receivers_ordered = sorted(
        receiver_manual_profiles,
        key=lambda row: (
            pending_count_by_receiver.get(row.user_id, 0),
            row.user_id,
        ),
    )
    max_quota = max(effective_quota_by_receiver_profile_id.values()) if effective_quota_by_receiver_profile_id else 0
    skip_stats = Counter()
    for _ in range(max_quota):  
        for receiver_manual_profile in receivers_ordered:  
            receiver_profile_id = receiver_manual_profile.user_id 
            if len(allocated_target_ids_by_receiver_profile_id[receiver_profile_id]) >= effective_quota_by_receiver_profile_id[receiver_profile_id]: 
                continue 

            candidate_target_profile_ids = []
            for target_profile_id, remaining in remaining_by_target_profile_id.items():
                if remaining <= 0:
                    skip_stats["no_capacity"] += 1
                    continue
                if target_profile_id == receiver_profile_id:
                    skip_stats["self"] += 1
                    continue
                if target_profile_id in allocated_target_ids_by_receiver_profile_id[receiver_profile_id]:
                    skip_stats["already_allocated_this_round"] += 1
                    continue
                if (receiver_profile_id, target_profile_id) in verified_pair_set:
                    skip_stats["already_verified_pair"] += 1
                    continue
                if (receiver_profile_id, target_profile_id) in existing_unverified_set:
                    skip_stats["already_pending_pair"] += 1
                    continue
                candidate_target_profile_ids.append(target_profile_id)
            if not candidate_target_profile_ids: 
                continue 

            best_target_profile_id = max(
                candidate_target_profile_ids,
                key=lambda target_profile_id: (
                    1
                    if _is_same_manual_category(
                        receiver_manual_profile,
                        target_manual_by_profile_id[target_profile_id],
                    )
                    else 0,
                    remaining_by_target_profile_id[target_profile_id],
                    target_score_by_profile_id[target_profile_id],
                    -target_profile_id,
                ),
            )
            allocated_target_ids_by_receiver_profile_id[receiver_profile_id].add(best_target_profile_id) 
            existing_unverified_set.add((receiver_profile_id, best_target_profile_id))  
            remaining_by_target_profile_id[best_target_profile_id] -= 1   
            skip_stats["assigned"] += 1 

    # Fallback pass: consume remaining capacity by assigning to any eligible active receiver.
    if any(remaining > 0 for remaining in remaining_by_target_profile_id.values()):
        receiver_ids_ordered = [row.user_id for row in receivers_ordered]
        for target_profile_id, remaining in list(remaining_by_target_profile_id.items()):
            if remaining <= 0:
                continue
            for receiver_profile_id in receiver_ids_ordered:
                if remaining <= 0:
                    break
                if target_profile_id == receiver_profile_id:
                    continue
                if target_profile_id in allocated_target_ids_by_receiver_profile_id[receiver_profile_id]:
                    continue
                if (receiver_profile_id, target_profile_id) in verified_pair_set:
                    continue
                if (receiver_profile_id, target_profile_id) in existing_unverified_set:
                    continue
                allocated_target_ids_by_receiver_profile_id[receiver_profile_id].add(target_profile_id)
                existing_unverified_set.add((receiver_profile_id, target_profile_id))
                remaining -= 1
                skip_stats["fallback_assigned"] += 1
            remaining_by_target_profile_id[target_profile_id] = remaining

    rows_to_create = []
    for receiver_manual_profile in receiver_manual_profiles:
        if receiver_manual_profile is None:
            continue
        for target_profile_id in allocated_target_ids_by_receiver_profile_id[receiver_manual_profile.user_id]:
            target_profile = SubscriberProfile.objects.filter(user_id=target_profile_id).first()
            if not target_profile:
                target_manual = target_manual_by_profile_id.get(target_profile_id)
                if target_manual:
                    target_profile = _get_or_create_distribution_profile_for_manual(target_manual)
            if not target_profile:
                continue
            rows_to_create.append(
                ManualSubscribeTaskAssign(
                    user_id=receiver_manual_profile.user_id,
                    manual_subscribe_profile=receiver_manual_profile,
                    target_profile=target_profile,
                    subscribed_status=ManualSubscribeTaskAssign.STATUS_ASSIGNED, 
                    active_status=True, 
                ) 
            ) 
    _reserve_targets_for_manual_tasks(rows_to_create) 
    logger.info( 
        "manual_rebalance summary receivers=%s targets=%s assigned=%s fallback_assigned=%s skip_self=%s skip_verified=%s skip_pending=%s skip_no_capacity=%s", 
        len(receiver_manual_profiles), 
        len(target_capacity_by_profile_id), 
        skip_stats.get("assigned", 0), 
        skip_stats.get("fallback_assigned", 0),
        skip_stats.get("self", 0), 
        skip_stats.get("already_verified_pair", 0), 
        skip_stats.get("already_pending_pair", 0), 
        skip_stats.get("no_capacity", 0), 
    )


def _build_manual_suggested_targets_for_user( 
    user,
    now,
    *,
    online_minutes: int = ACTIVITY_WINDOW_MINUTES, 
) -> list[SubscriberProfile]: 
    window_start = now - timedelta(minutes=online_minutes)
    receiver_manual_profiles = list(
        ManualSubscribeProfile.objects.select_related("user")
        .filter(
            user__is_active=True,
            last_tasks_entry_at__gte=window_start,
            active_status_for_subscribe=True,
        )
        .order_by("user_id")
    )
    if not receiver_manual_profiles:
        return []

    receiver_by_user_id = {row.user_id: row for row in receiver_manual_profiles}
    current_receiver = receiver_by_user_id.get(user.id)
    if current_receiver is None:
        return []

    targets = list( 
        ManualSubscribeProfile.objects.select_related("user") 
        .filter( 
            user__is_active=True, 
            sub_score__gt=0, 
            handle__startswith="@", 
        ) 
        .order_by("-sub_score", "id") 
    ) 
    if not targets:
        return []
    for target_row in targets:
        _get_or_create_distribution_profile_for_manual(target_row)

    target_capacity_by_profile_id = { 
        row.user_id: max(int(row.sub_score or 0), 0)
        for row in targets 
    } 
    target_capacity_by_profile_id = {
        target_id: capacity for target_id, capacity in target_capacity_by_profile_id.items() if capacity > 0
    }
    if not target_capacity_by_profile_id:
        return []

    total_capacity = sum(target_capacity_by_profile_id.values())
    receiver_count = len(receiver_manual_profiles)
    if total_capacity <= 0 or receiver_count <= 0:
        return []

    base_quota = total_capacity // receiver_count
    remainder = total_capacity % receiver_count
    quota_by_receiver_profile_id = {}
    for index, receiver_manual_profile in enumerate(receiver_manual_profiles):
        quota_by_receiver_profile_id[receiver_manual_profile.user_id] = base_quota + (1 if index < remainder else 0)

    target_score_by_profile_id = {row.user_id: int(row.sub_score or 0) for row in targets}
    target_manual_by_profile_id = {row.user_id: row for row in targets}
    remaining_by_target_profile_id = target_capacity_by_profile_id.copy()
    allocated_target_ids_by_receiver_profile_id = {p.user_id: set() for p in receiver_manual_profiles}

    receiver_user_ids = [p.user_id for p in receiver_manual_profiles]
    target_user_ids = list(target_capacity_by_profile_id.keys())
    existing_pair_rows = list(
        ManualSubscribeTaskAssign.objects.filter(
            user_id__in=receiver_user_ids,
            target_profile__user_id__in=target_user_ids,
        ).values_list(
            "user_id",
            "target_profile__user_id",
            "subscribed_status",
        )
    )
    verified_pair_set = {
        (user_id, target_user_id)
        for user_id, target_user_id, status in existing_pair_rows
        if status == ManualSubscribeTaskAssign.STATUS_VERIFIED
    }
    existing_unverified_set = {
        (user_id, target_user_id)
        for user_id, target_user_id, status in existing_pair_rows
        if status in MANUAL_PENDING_STATUSES
    }

    pending_count_by_receiver = Counter(
        user_id
        for user_id, _, status in existing_pair_rows
        if status in MANUAL_PENDING_STATUSES
    )
    effective_quota_by_receiver_profile_id = {
        receiver_id: max(quota_by_receiver_profile_id.get(receiver_id, 0) - pending_count_by_receiver.get(receiver_id, 0), 0)
        for receiver_id in quota_by_receiver_profile_id
    }
    receivers_ordered = sorted(
        receiver_manual_profiles,
        key=lambda row: (
            pending_count_by_receiver.get(row.user_id, 0),
            row.user_id,
        ),
    )
    max_quota = max(effective_quota_by_receiver_profile_id.values()) if effective_quota_by_receiver_profile_id else 0
    for _ in range(max_quota): 
        for receiver_manual_profile in receivers_ordered: 
            receiver_profile_id = receiver_manual_profile.user_id 
            if len(allocated_target_ids_by_receiver_profile_id[receiver_profile_id]) >= effective_quota_by_receiver_profile_id[receiver_profile_id]: 
                continue 

            candidate_target_profile_ids = [
                target_profile_id
                for target_profile_id, remaining in remaining_by_target_profile_id.items()
                if remaining > 0
                and target_profile_id != receiver_profile_id
                and target_profile_id not in allocated_target_ids_by_receiver_profile_id[receiver_profile_id]
                and (receiver_profile_id, target_profile_id) not in verified_pair_set
                and (receiver_profile_id, target_profile_id) not in existing_unverified_set
            ] 
            if not candidate_target_profile_ids:
                continue

            best_target_profile_id = max(
                candidate_target_profile_ids,
                key=lambda target_profile_id: (
                    1 if _is_same_manual_category(receiver_manual_profile, target_manual_by_profile_id[target_profile_id]) else 0,
                    remaining_by_target_profile_id[target_profile_id],
                    target_score_by_profile_id[target_profile_id],
                    -target_profile_id,
                ),
            )
            allocated_target_ids_by_receiver_profile_id[receiver_profile_id].add(best_target_profile_id)
            existing_unverified_set.add((receiver_profile_id, best_target_profile_id))
            remaining_by_target_profile_id[best_target_profile_id] -= 1 

    target_ids_for_current = allocated_target_ids_by_receiver_profile_id.get(current_receiver.user_id, set())
    if not target_ids_for_current:
        return []

    profiles_by_id = {
        row.user_id: row
        for row in SubscriberProfile.objects.select_related("user").filter(user_id__in=target_ids_for_current)
    }
    missing_target_user_ids = [uid for uid in target_ids_for_current if uid not in profiles_by_id]
    if missing_target_user_ids:
        targets_by_user_id = {row.user_id: row for row in targets}
        for missing_uid in missing_target_user_ids:
            target_manual = targets_by_user_id.get(missing_uid)
            if target_manual:
                created_profile = _get_or_create_distribution_profile_for_manual(target_manual)
                if created_profile:
                    profiles_by_id[missing_uid] = created_profile
    return [profiles_by_id[target_id] for target_id in sorted(target_ids_for_current) if target_id in profiles_by_id]


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
    if changed_tasks:
        TopUserSubscribeTask.objects.bulk_update(
            changed_tasks,
            ["verified_status", "updated_at"],
        )
    profile.last_verified_sync_at = now
    profile.save(update_fields=["last_verified_sync_at", "updated_at"])
    return True


def _rebalance_active_top_user_tasks(now, *, online_minutes: int = ACTIVITY_WINDOW_MINUTES) -> None: 
    window_start = now - timedelta(minutes=online_minutes) 
    stale_unverified_top_user_tasks = TopUserSubscribeTask.objects.filter(
        verified_status=False
    ).exclude(
        profile__user__is_active=True,
        profile__active_status=True,
        profile__last_tasks_entry_at__gte=window_start,
    )
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
        _release_unverified_task_reservations(stale_unverified_top_user_tasks) 
        return 
 
    receivers = active_profiles 
    _release_unverified_task_reservations(stale_unverified_top_user_tasks) 

    targets = list( 
        SubscriberProfile.objects.select_related("user") 
        .filter( 
            user__is_active=True, 
            score__gt=0, 
        ) 
        .exclude(channel_id="") 
        .order_by("-score", "id") 
    ) 
    if not targets:
        return

    target_capacity_by_id = { 
        p.id: max(int(p.score or 0), 0)
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
    target_ids = list(target_capacity_by_id.keys())
    existing_pair_rows = list(
        TopUserSubscribeTask.objects.filter(
            profile_id__in=receiver_ids,
            target_profile_id__in=target_ids,
        ).values_list("profile_id", "target_profile_id", "verified_status")
    )
    verified_pair_set = {
        (profile_id, target_profile_id)
        for profile_id, target_profile_id, verified in existing_pair_rows
        if verified
    }
    existing_unverified_set = {
        (profile_id, target_profile_id)
        for profile_id, target_profile_id, verified in existing_pair_rows
        if not verified
    }
    pending_count_by_receiver = Counter(
        profile_id
        for profile_id, _, verified in existing_pair_rows
        if not verified
    )
    effective_quota_by_receiver_id = {
        receiver_id: max(quota_by_receiver_id.get(receiver_id, 0) - pending_count_by_receiver.get(receiver_id, 0), 0)
        for receiver_id in quota_by_receiver_id
    }
    receivers_ordered = sorted(
        receivers,
        key=lambda row: (
            pending_count_by_receiver.get(row.id, 0),
            row.id,
        ),
    )
    max_quota = max(effective_quota_by_receiver_id.values()) if effective_quota_by_receiver_id else 0
    skip_stats = Counter()

    for _ in range(max_quota): 
        for receiver in receivers_ordered: 
            receiver_id = receiver.id 
            if len(allocated_target_ids_by_receiver_id[receiver_id]) >= effective_quota_by_receiver_id[receiver_id]: 
                continue 

            candidate_ids = []
            for target_id, remaining in remaining_by_target_id.items():
                if remaining <= 0:
                    skip_stats["no_capacity"] += 1
                    continue
                if target_id == receiver_id:
                    skip_stats["self"] += 1
                    continue
                if target_id in allocated_target_ids_by_receiver_id[receiver_id]:
                    skip_stats["already_allocated_this_round"] += 1
                    continue
                if (receiver_id, target_id) in verified_pair_set:
                    skip_stats["already_verified_pair"] += 1
                    continue
                if (receiver_id, target_id) in existing_unverified_set:
                    skip_stats["already_pending_pair"] += 1
                    continue
                candidate_ids.append(target_id)
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
            existing_unverified_set.add((receiver_id, best_target_id)) 
            remaining_by_target_id[best_target_id] -= 1  
            skip_stats["assigned"] += 1

    # Fallback pass: consume remaining capacity by assigning to any eligible active receiver.
    if any(remaining > 0 for remaining in remaining_by_target_id.values()):
        receiver_ids_ordered = [row.id for row in receivers_ordered]
        for target_id, remaining in list(remaining_by_target_id.items()):
            if remaining <= 0:
                continue
            for receiver_id in receiver_ids_ordered:
                if remaining <= 0:
                    break
                if target_id == receiver_id:
                    continue
                if target_id in allocated_target_ids_by_receiver_id[receiver_id]:
                    continue
                if (receiver_id, target_id) in verified_pair_set:
                    continue
                if (receiver_id, target_id) in existing_unverified_set:
                    continue
                allocated_target_ids_by_receiver_id[receiver_id].add(target_id)
                existing_unverified_set.add((receiver_id, target_id))
                remaining -= 1
                skip_stats["fallback_assigned"] += 1
            remaining_by_target_id[target_id] = remaining

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
    logger.info(
        "google_rebalance summary receivers=%s targets=%s assigned=%s fallback_assigned=%s skip_self=%s skip_verified=%s skip_pending=%s skip_no_capacity=%s",
        len(receivers),
        len(target_capacity_by_id),
        skip_stats.get("assigned", 0),
        skip_stats.get("fallback_assigned", 0),
        skip_stats.get("self", 0),
        skip_stats.get("already_verified_pair", 0),
        skip_stats.get("already_pending_pair", 0),
        skip_stats.get("no_capacity", 0),
    )


def _assign_tasks_for_google_receiver(profile: SubscriberProfile, *, cap: int = LOCAL_ASSIGN_CAP) -> int:
    if cap <= 0:
        return 0
    targets = list(
        SubscriberProfile.objects.select_related("user")
        .filter(user__is_active=True, score__gt=0)
        .exclude(channel_id="")
        .exclude(id=profile.id)
        .order_by("-score", "id")
    )
    if not targets:
        return 0
    target_ids = [row.id for row in targets]
    existing_rows = list(
        TopUserSubscribeTask.objects.filter(
            profile_id=profile.id,
            target_profile_id__in=target_ids,
        ).values_list("target_profile_id", "verified_status")
    )
    verified_ids = {target_id for target_id, verified in existing_rows if verified}
    pending_ids = {target_id for target_id, verified in existing_rows if not verified}
    available_slots = max(cap - len(pending_ids), 0)
    if available_slots <= 0:
        return 0

    candidate_ids = [
        row.id
        for row in targets
        if row.id not in verified_ids and row.id not in pending_ids
    ][:available_slots]
    if not candidate_ids:
        return 0
    rows = [
        TopUserSubscribeTask(
            profile_id=profile.id,
            target_profile_id=target_id,
            verified_status=False,
        )
        for target_id in candidate_ids
    ]
    _reserve_targets_for_tasks(rows)
    return len(rows)


def _assign_tasks_for_manual_receiver(
    user,
    manual_profile: ManualSubscribeProfile,
    *,
    cap: int = LOCAL_ASSIGN_CAP,
) -> int:
    if cap <= 0:
        return 0
    targets = list(
        ManualSubscribeProfile.objects.select_related("user")
        .filter(user__is_active=True, sub_score__gt=0, handle__startswith="@")
        .exclude(user_id=user.id)
        .order_by("-sub_score", "id")
    )
    if not targets:
        return 0
    target_user_ids = [row.user_id for row in targets]
    existing_rows = list(
        ManualSubscribeTaskAssign.objects.filter(
            user_id=user.id,
            target_profile__user_id__in=target_user_ids,
        ).values_list("target_profile__user_id", "subscribed_status")
    )
    verified_user_ids = {
        target_user_id
        for target_user_id, status in existing_rows
        if status == ManualSubscribeTaskAssign.STATUS_VERIFIED
    }
    pending_user_ids = {
        target_user_id
        for target_user_id, status in existing_rows
        if status in MANUAL_PENDING_STATUSES
    }
    available_slots = max(cap - len(pending_user_ids), 0)
    if available_slots <= 0:
        return 0

    candidate_user_ids = [
        row.user_id
        for row in targets
        if row.user_id not in verified_user_ids and row.user_id not in pending_user_ids
    ][:available_slots]
    if not candidate_user_ids:
        return 0
    target_profiles = {
        row.user_id: row
        for row in SubscriberProfile.objects.select_related("user").filter(user_id__in=candidate_user_ids)
    }
    targets_by_user_id = {row.user_id: row for row in targets}
    rows = []
    for target_user_id in candidate_user_ids:
        target_profile = target_profiles.get(target_user_id)
        if not target_profile:
            target_profile = _get_or_create_distribution_profile_for_manual(
                targets_by_user_id.get(target_user_id)
            )
            if not target_profile:
                continue
        rows.append(
            ManualSubscribeTaskAssign(
                user_id=user.id,
                manual_subscribe_profile=manual_profile,
                target_profile=target_profile,
                subscribed_status=ManualSubscribeTaskAssign.STATUS_ASSIGNED,
                active_status=True,
            )
        )
    _reserve_targets_for_manual_tasks(rows)
    return len(rows)


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
    if not handle or user.handle == handle:
        return

    user.handle = handle
    user.save(update_fields=["handle"])


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
            account_mode=User.ACCOUNT_MODE_GOOGLE,
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
    if user.account_mode != User.ACCOUNT_MODE_GOOGLE:
        user.account_mode = User.ACCOUNT_MODE_GOOGLE
        user.save(update_fields=["account_mode"])

    profile, _ = SubscriberProfile.objects.get_or_create(user=user)
    return user, profile


def home(request):
    google_connected = False
    if request.user.is_authenticated:
        profile = SubscriberProfile.objects.filter(user=request.user).first()
        google_connected = _is_google_user(request.user) or bool(profile and profile.google_subject_id)
        video_profile = VideoProfile.objects.filter(user=request.user).first()
        if video_profile and (video_profile.active_status_for_video or video_profile.active_status_for_youtube):
            video_profile.active_status_for_video = False
            video_profile.active_status_for_youtube = False
            video_profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "updated_at"])
    video_urls = _admin_video_urls()
    return render(
        request,
        "subscribers/home.html",
        {
            "google_connected": google_connected,
            **video_urls,
        },
    )


@login_required
def logfile_page(request):
    """Show recent app messages/notifications from log file."""
    log_path = Path(settings.BASE_DIR) / "logs" / "app.log"
    lines = []
    if log_path.exists():
        try:
            raw = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            filtered = [line for line in raw if (" INFO " in line or " WARNING " in line)]
            lines = filtered[-500:]
        except Exception:
            lines = ["Unable to read messages file."]
    else:
        lines = ["No messages yet."]
    return render(request, "subscribers/logfile.html", {"log_lines": lines, "log_path": str(log_path)})


def login_page(request):
    if request.user.is_authenticated:
        return redirect("subscribers:home")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip().lower()
        password = request.POST.get("password", "")

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return redirect("subscribers:login")

        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Invalid username or password.")
            return redirect("subscribers:login")

        login(request, user)
        messages.success(request, "Login successful.")
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
        username = (request.POST.get("username") or "").strip().lower()
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        
        if not username or not password:
            messages.error(request, "Username and password are required.")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })

        if not USERNAME_PATTERN.fullmatch(username):
            messages.error(
                request,
                "Username must be 8-12 characters, start with a lowercase letter, and use only lowercase letters, numbers, '.', '-' or '_'.",
            )
            return render(
                request,
                "subscribers/signup.html",
                {
                    "oauth_ready": not _required_google_settings_missing(),
                    "facebook_oauth_ready": not _required_facebook_settings_missing(),
                },
            )
        
        if password != password_confirm:
            messages.error(request, "Passwords do not match.")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })

        if not STRONG_PASSWORD_PATTERN.fullmatch(password):
            messages.error(
                request,
                "Password must be at least 6 characters and include at least one letter, one number, and one special character.",
            )
            return render(
                request,
                "subscribers/signup.html",
                {
                    "oauth_ready": not _required_google_settings_missing(),
                    "facebook_oauth_ready": not _required_facebook_settings_missing(),
                },
            )
        
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            messages.error(request, "This username is already taken.")
            return render(request, "subscribers/signup.html", 
                         {
                             "oauth_ready": not _required_google_settings_missing(),
                             "facebook_oauth_ready": not _required_facebook_settings_missing(),
                         })
        
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=username.startswith("rishirambhusal"),
                account_mode=User.ACCOUNT_MODE_MANUAL,
            )
            login(request, user)
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
    profile = _get_google_profile(request.user)
    if not _is_google_user(request.user) or not profile or not profile.google_subject_id:
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
    profile = _get_google_profile(request.user)
    if not _is_google_user(request.user) or not profile or not profile.google_subject_id:
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
    profile = _get_google_profile(request.user)
    if not _is_google_user(request.user) or not profile or not profile.google_subject_id:
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
@require_POST
def update_youtube_handle(request):
    profile = _get_google_profile(request.user)
    raw_value = (request.POST.get("youtube_handle") or "").strip()
    next_url = (request.POST.get("next_url") or "").strip() or reverse("subscribers:profile")

    handle = _normalize_youtube_handle(raw_value)

    if not handle:
        messages.error(request, "Please enter a valid YouTube handle.")
        return redirect(next_url)

    # Keep manual handle input aligned with YouTube handle format rules used elsewhere.
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", handle):
        messages.error(
            request,
            "Enter a valid handle (3-40 chars) using only letters, numbers, '.', '-' or '_'.",
        )
        return redirect(next_url)

    if not _youtube_handle_exists(handle):
        messages.error(request, "This YouTube handle could not be verified. Check and try again.")
        return redirect(next_url)

    normalized_handle = f"@{handle}"
    User = get_user_model()
    if User.objects.filter(handle__iexact=normalized_handle).exclude(pk=request.user.pk).exists():
        messages.error(request, "This YouTube handle is already used by another account.")
        return redirect(next_url)

    if profile:
        profile.handle = normalized_handle
        profile.save(update_fields=["handle", "updated_at"])
    try:
        _rename_user_to_handle(request.user, normalized_handle)
    except IntegrityError:
        messages.error(request, "This YouTube handle is already used by another account.")
        return redirect(next_url)

    messages.success(request, "YouTube handle updated successfully.")
    return redirect(next_url)


def _normalize_youtube_handle(raw_value: str) -> str:
    handle = (raw_value or "").strip().lower()
    if "youtube.com/" in handle:
        handle = handle.split("youtube.com/", 1)[1]
    return handle.strip().lstrip("@").split("/")[0].split("?")[0].strip()


def _youtube_handle_exists(handle: str) -> bool:
    if not handle:
        return False
    strict_verify = bool(getattr(settings, "YOUTUBE_HANDLE_STRICT_VERIFY", True))
    url = f"https://www.youtube.com/@{quote(handle)}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(req, timeout=6) as resp:
            final_url = (resp.geturl() or "").lower()
            code = getattr(resp, "status", 200) or 200
            return code == 200 and "/@" in final_url
    except HTTPError as exc:
        if exc.code in (404, 410):
            return False
        # On some hosts, YouTube can return 403/429 for bot/rate-limits.
        # Non-strict mode avoids blocking all valid handles in that case.
        if not strict_verify and exc.code in (401, 403, 429, 500, 502, 503, 504):
            return True
        return False
    except (URLError, TimeoutError, ValueError):
        return False if strict_verify else True


@login_required
@require_GET
def validate_youtube_handle(request):
    raw_value = (request.GET.get("youtube_handle") or "").strip()
    handle = _normalize_youtube_handle(raw_value)
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", handle):
        return JsonResponse({"ok": False, "exists": False, "normalized": f"@{handle}" if handle else "", "message": "Invalid format."})
    exists = _youtube_handle_exists(handle)
    return JsonResponse(
        {
            "ok": True,
            "exists": exists,
            "normalized": f"@{handle}",
            "message": "Handle verified." if exists else "Handle not found on YouTube.",
        }
    )


def _has_valid_task_handle(profile: SubscriberProfile) -> bool:
    return bool((profile.handle or "").strip().startswith("@"))


def _extract_text_from_image_file(image_path: str) -> str:
    try:
        with open(image_path, "rb") as fh:
            class _UploadedLike:
                def __init__(self, data: bytes):
                    self._data = data
                    self.file = io.BytesIO(data)
                def read(self):
                    return self.file.read()
                def seek(self, pos):
                    return self.file.seek(pos)
            return get_ocr_text(_UploadedLike(fh.read())) or ""
    except Exception:
        return ""


def _extract_text_from_uploaded_image(uploaded_file) -> str:
    try:
        return get_ocr_text(uploaded_file) or ""
    except Exception:
        return ""


def _extract_handles_from_text(raw_text: str) -> set[str]:
    return {match.group(1).strip().lower() for match in HANDLE_REGEX.finditer(raw_text or "") if match.group(1).strip()}


def _normalize_facebook_profile_url(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = f"https://{value}"
    parsed = urlparse(value)
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in {"facebook.com", "fb.com", "m.facebook.com"}:
        return ""
    if not parsed.path.strip("/"):
        return ""
    return value


def _facebook_url_slug(profile_url: str) -> str:
    parsed = urlparse(profile_url or "")
    path = parsed.path.strip("/")
    if not path:
        return ""
    first = path.split("/", 1)[0]
    return re.sub(r"[^a-z0-9]+", " ", first.lower()).strip()


def _facebook_match_tokens(profile: ManualFacebookProfile) -> set[str]:
    tokens = set()
    page_name = re.sub(r"[^a-z0-9]+", " ", (profile.page_name or "").lower()).strip()
    url_slug = _facebook_url_slug(profile.profile_url)
    # Prefer explicit page/profile name entered by user; fallback to URL slug only if name is empty.
    if page_name and len(page_name) >= 3:
        tokens.add(page_name)
    elif url_slug and len(url_slug) >= 3:
        tokens.add(url_slug)
    return tokens


def _build_facebook_app_url(profile_url: str) -> str:
    slug = _facebook_url_slug(profile_url)
    if slug:
        return f"fb://facewebmodal/f?href={profile_url}"
    return "fb://"


def _assign_manual_facebook_follow_tasks(user, facebook_profile: ManualFacebookProfile, *, cap: int = LOCAL_ASSIGN_CAP) -> int:
    existing_rows = list(
        ManualFacebookFollowTaskAssign.objects.filter(user=user).values_list(
            "target_profile_id",
            "followed_status",
        )
    )
    verified_ids = {
        target_id
        for target_id, status in existing_rows
        if status == ManualFacebookFollowTaskAssign.STATUS_VERIFIED
    }
    pending_ids = {
        target_id
        for target_id, status in existing_rows
        if status in FACEBOOK_PENDING_STATUSES
    }
    available_slots = max(cap - len(pending_ids), 0)
    if available_slots <= 0:
        return 0

    targets = list(
        ManualFacebookProfile.objects.filter(
            active_status_for_follow=True,
            follow_score__gt=0,
        )
        .exclude(user=user)
        .exclude(id__in=verified_ids | pending_ids)
        .order_by("-follow_score", "-updated_at")[:available_slots]
    )
    if not targets:
        return 0

    rows = [
        ManualFacebookFollowTaskAssign(
            user=user,
            manual_facebook_profile=facebook_profile,
            target_profile=target,
            followed_status=ManualFacebookFollowTaskAssign.STATUS_ASSIGNED,
            active_status=True,
        )
        for target in targets
    ]
    with transaction.atomic():
        ManualFacebookFollowTaskAssign.objects.bulk_create(rows, ignore_conflicts=True)
        ManualFacebookProfile.objects.filter(id__in=[target.id for target in targets]).update(
            follow_score=Case(
                When(follow_score__gt=0, then=F("follow_score") - 1),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
    return len(rows)


@login_required
def enter_facebook_tasks_manual(request):
    facebook_profile = ManualFacebookProfile.objects.filter(user=request.user).first()

    if request.method == "POST":
        page_name = (request.POST.get("page_name") or "").strip()
        profile_url = _normalize_facebook_profile_url(request.POST.get("profile_url") or "")
        if not profile_url:
            messages.error(request, "Enter a valid Facebook page/profile link.")
            return redirect("subscribers:facebook_tasks_manual_enter")
        if not page_name:
            page_name = _facebook_url_slug(profile_url) or "facebook-page"

        facebook_profile, _ = ManualFacebookProfile.objects.get_or_create(user=request.user)
        facebook_profile.page_name = page_name
        facebook_profile.profile_url = profile_url
        facebook_profile.active_status_for_follow = True
        facebook_profile.last_tasks_entry_at = timezone.now()
        facebook_profile.save(
            update_fields=[
                "page_name",
                "profile_url",
                "active_status_for_follow",
                "last_tasks_entry_at",
                "updated_at",
            ]
        )
        messages.success(request, "Facebook profile link saved.")
        return redirect("subscribers:facebook_tasks_manual")

    return render(
        request,
        "subscribers/facebook_enter_manual.html",
        {
            "facebook_profile": facebook_profile,
            "has_facebook_profile": bool(facebook_profile and facebook_profile.profile_url),
        },
    )


@login_required
def manual_facebook_tasks(request):
    facebook_profile = ManualFacebookProfile.objects.filter(user=request.user).first()
    if not facebook_profile or not facebook_profile.profile_url:
        messages.warning(request, "Add your Facebook page/profile link before entering follow tasks.")
        return redirect("subscribers:facebook_tasks_manual_enter")

    now = timezone.now()
    facebook_profile.active_status_for_follow = True
    facebook_profile.last_tasks_entry_at = now
    facebook_profile.save(update_fields=["active_status_for_follow", "last_tasks_entry_at", "updated_at"])

    _assign_manual_facebook_follow_tasks(request.user, facebook_profile)

    pending_rows = list(
        ManualFacebookFollowTaskAssign.objects.select_related("target_profile__user")
        .filter(user=request.user, followed_status__in=FACEBOOK_PENDING_STATUSES)
        .order_by("-updated_at", "-created_at")
    )
    verified_target_ids = {
        row["target_profile_id"]
        for row in ManualFacebookFollowTaskAssign.objects.filter(
            user=request.user,
            followed_status=ManualFacebookFollowTaskAssign.STATUS_VERIFIED,
        ).values("target_profile_id")
    }
    unverified_target_ids = {
        row["target_profile_id"]
        for row in ManualFacebookFollowTaskAssign.objects.filter(
            user=request.user,
            followed_status=ManualFacebookFollowTaskAssign.STATUS_UNVERIFIED,
        ).values("target_profile_id")
    }
    video_urls = _admin_video_urls()
    return render(
        request,
        "subscribers/facebook_tasks_manual.html",
        {
            "facebook_profile": facebook_profile,
            "pending_rows": pending_rows,
            "verified_target_ids": verified_target_ids,
            "unverified_target_ids": unverified_target_ids,
            "facebook_verify_error": request.session.pop("facebook_verify_error", ""),
            "facebook_last_scan_matched": int(request.session.pop("facebook_last_scan_matched", 0) or 0),
            "show_filter_guide": bool(request.session.pop("show_filter_guide", False)),
            **video_urls,
        },
    )


@login_required
@require_POST
def manual_facebook_follow_task_assign(request):
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    target_profile_id_raw = (request.POST.get("target_profile_id") or "").strip()
    if not target_profile_id_raw.isdigit():
        if wants_json:
            return JsonResponse({"ok": False, "message": "Invalid target profile id."}, status=400)
        messages.error(request, "Invalid target profile id.")
        return redirect("subscribers:facebook_tasks_manual")

    target_profile = ManualFacebookProfile.objects.filter(id=int(target_profile_id_raw)).first()
    if target_profile is None:
        if wants_json:
            return JsonResponse({"ok": False, "message": "Target Facebook profile not found."}, status=404)
        messages.error(request, "Target Facebook profile not found.")
        return redirect("subscribers:facebook_tasks_manual")
    if target_profile.user_id == request.user.id:
        if wants_json:
            return JsonResponse({"ok": False, "message": "You cannot follow your own profile as a task."}, status=400)
        messages.warning(request, "You cannot follow your own profile as a task.")
        return redirect("subscribers:facebook_tasks_manual")

    facebook_profile, _ = ManualFacebookProfile.objects.get_or_create(user=request.user)
    with transaction.atomic():
        existing_task = ManualFacebookFollowTaskAssign.objects.filter(
            user=request.user,
            target_profile=target_profile,
        ).first()
        needs_hold = not (
            existing_task
            and existing_task.followed_status in FACEBOOK_PENDING_STATUSES
        )
        if needs_hold:
            held_rows = ManualFacebookProfile.objects.filter(id=target_profile.id, follow_score__gt=0).update(
                follow_score=F("follow_score") - 1
            )
            if held_rows == 0:
                if wants_json:
                    return JsonResponse(
                        {"ok": False, "message": "Target score is not available right now. Please refresh tasks."},
                        status=409,
                    )
                messages.warning(request, "Target score is not available right now. Please refresh tasks.")
                return redirect("subscribers:facebook_tasks_manual")

        ManualFacebookFollowTaskAssign.objects.update_or_create(
            user=request.user,
            target_profile=target_profile,
            defaults={
                "manual_facebook_profile": facebook_profile,
                "followed_status": ManualFacebookFollowTaskAssign.STATUS_UNVERIFIED,
                "active_status": True,
            },
        )

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "status": ManualFacebookFollowTaskAssign.STATUS_UNVERIFIED,
                "label": "Unverified",
                "web_url": target_profile.profile_url,
                "app_url": _build_facebook_app_url(target_profile.profile_url),
            }
        )
    return redirect(target_profile.profile_url)


@login_required
@require_POST
def make_facebook_verify_from_image(request):
    uploaded_file = request.FILES.get("verification_image")
    if not uploaded_file:
        request.session["facebook_verify_error"] = "Please choose a screenshot and then click Make Verify."
        return redirect("subscribers:facebook_tasks_manual")

    pending_assignments = list(
        ManualFacebookFollowTaskAssign.objects.select_related("target_profile")
        .filter(user=request.user, followed_status__in=FACEBOOK_PENDING_STATUSES)
    )
    if not pending_assignments:
        messages.info(request, "No unverified Facebook follow tasks were found.")
        return redirect("subscribers:facebook_tasks_manual")

    extracted_text = _extract_text_from_uploaded_image(uploaded_file)
    request.session["last_ocr_text"] = (extracted_text or "")[:5000]
    lowered_text = re.sub(r"[^a-z0-9]+", " ", (extracted_text or "").lower())
    has_follow_signal = "following" in lowered_text
    if not has_follow_signal:
        request.session["show_filter_guide"] = True
        request.session["facebook_verify_error"] = "Screenshot must clearly show 'Following' state. Please reupload."
        return redirect("subscribers:facebook_tasks_manual")
    matched_rows = []
    if has_follow_signal:
        for row in pending_assignments:
            if any(token in lowered_text for token in _facebook_match_tokens(row.target_profile)):
                matched_rows.append(row)

    if not matched_rows:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        VerificationImage.objects.create(
            user=request.user,
            image=uploaded_file,
            scanned_status=False,
            scanned_at=None,
            extracted_text=extracted_text.strip(),
        )
        request.session["show_filter_guide"] = True
        request.session["facebook_verify_error"] = "Upload a screenshot that clearly shows the followed Facebook page/profile."
        return redirect("subscribers:facebook_tasks_manual")

    facebook_profile, _ = ManualFacebookProfile.objects.get_or_create(user=request.user)
    owner_verified_increments = {}
    verified_count = 0
    with transaction.atomic():
        for row in matched_rows:
            if row.followed_status != ManualFacebookFollowTaskAssign.STATUS_VERIFIED:
                row.followed_status = ManualFacebookFollowTaskAssign.STATUS_VERIFIED
                row.save(update_fields=["followed_status", "updated_at"])
                verified_count += 1
                owner_verified_increments[row.target_profile_id] = owner_verified_increments.get(row.target_profile_id, 0) + 1

        facebook_profile.loyal_score = int(facebook_profile.loyal_score or 0) + 1
        facebook_profile.follow_score = int(facebook_profile.follow_score or 0) + verified_count
        facebook_profile.save(update_fields=["loyal_score", "follow_score", "updated_at"])

        for target_profile_id, increment in owner_verified_increments.items():
            ManualFacebookProfile.objects.filter(id=target_profile_id).update(
                total_verified=F("total_verified") + increment
            )

    request.session["facebook_last_scan_matched"] = verified_count
    messages.success(request, f"Facebook verify complete. Tasks verified: {verified_count}.")
    return redirect("subscribers:facebook_tasks_manual")


@login_required
def enter_youtube_tasks(request):
    if not _is_google_user(request.user):
        return redirect("subscribers:enter_youtube_tasks_manual")
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    if not profile.google_subject_id:
        return redirect("subscribers:enter_youtube_tasks_manual")
    if _has_valid_task_handle(profile):
        return redirect("subscribers:subscribe_tasks")
    video_profile, _ = _get_or_create_video_profile(request.user)
    video_profile.active_status_for_youtube = True
    video_profile.active_status_for_video = False
    video_profile.last_video_entry_at = timezone.now()
    video_profile.save(update_fields=["active_status_for_youtube", "active_status_for_video", "last_video_entry_at", "updated_at"])
    profile.last_tasks_entry_at = timezone.now()
    profile.save(update_fields=["last_tasks_entry_at", "updated_at"])
    subscriptions_preview = []
    try:
        access_token = ensure_valid_access_token(profile)
        subscriptions_preview = fetch_my_subscriptions(access_token, limit=6)
    except Exception:
        subscriptions_preview = []

    context = {
        "profile": profile,
        "google_connected": bool(profile.google_subject_id),
        "has_handle": _has_valid_task_handle(profile),
        "subscriptions_preview": subscriptions_preview,
        "total_subscribed": int(profile.subscribed_channel_count or 0),
        "channel_subscriber_total": int(profile.channel_subscriber_count or 0),
        "new_subscribers": int(profile.subscriber_change_since_last_scan or 0),
        "video_score": int(video_profile.video_score or 0),
        "video_score_reserved": int(video_profile.video_score_reserved or 0),
    }
    return render(request, "subscribers/youtube_enter.html", context)


@login_required
def enter_youtube_tasks_manual(request):
    if _is_google_user(request.user):
        return redirect("subscribers:enter_youtube_tasks")
    profile = SubscriberProfile.objects.filter(user=request.user).first()

    video_profile, _ = _get_or_create_video_profile(request.user)
    now = timezone.now()
    video_profile.active_status_for_youtube = True
    video_profile.active_status_for_video = False
    video_profile.last_video_entry_at = now
    video_profile.save(update_fields=["active_status_for_youtube", "active_status_for_video", "last_video_entry_at", "updated_at"])
    if profile:
        profile.last_tasks_entry_at = now
        profile.save(update_fields=["last_tasks_entry_at", "updated_at"])

    manual_profile, _ = ManualSubscribeProfile.objects.get_or_create(
        user=request.user,
        defaults={
            "handle": (profile.handle if profile else request.user.handle) or request.user.username,
            "category": profile.category if profile else SubscriberProfile.CATEGORY_OTHER,
            "last_tasks_entry_at": now,
        },
    )
    profile_fields_to_update = []
    if manual_profile.user_id != request.user.id:
        manual_profile.user = request.user
        profile_fields_to_update.append("user")
    desired_handle = (profile.handle if profile else request.user.handle) or request.user.username
    if _has_valid_manual_handle(manual_profile, request.user, profile):
        return redirect("subscribers:subscribe_tasks_manual")
    if manual_profile.handle != desired_handle:
        manual_profile.handle = desired_handle
        profile_fields_to_update.append("handle")
    desired_category = profile.category if profile else SubscriberProfile.CATEGORY_OTHER
    if manual_profile.category != desired_category:
        manual_profile.category = desired_category
        profile_fields_to_update.append("category")
    if manual_profile.last_tasks_entry_at != now:
        manual_profile.last_tasks_entry_at = now
        profile_fields_to_update.append("last_tasks_entry_at")
    if not manual_profile.active_status_for_subscribe:
        manual_profile.active_status_for_subscribe = True
        profile_fields_to_update.append("active_status_for_subscribe")
    if profile_fields_to_update:
        profile_fields_to_update.append("updated_at")
        manual_profile.save(update_fields=profile_fields_to_update)

    context = {
        "profile": profile or _manual_profile_defaults(request.user, manual_profile),
        "has_handle": bool((desired_handle or "").strip().startswith("@")),
        "total_subscribed": int(profile.subscribed_channel_count or 0) if profile else 0,
        "channel_subscriber_total": int(profile.channel_subscriber_count or 0) if profile else 0,
        "new_subscribers": int(profile.subscriber_change_since_last_scan or 0) if profile else 0,
        "video_score": int(video_profile.video_score or 0),
        "video_score_reserved": int(video_profile.video_score_reserved or 0),
    }
    return render(request, "subscribers/youtube_enter_manual.html", context)


@login_required
def enter_watch_tasks(request):
    profile = _get_google_profile(request.user)
    video_profile, _ = _get_or_create_video_profile(request.user)
    now = timezone.now()
    video_profile.active_status_for_video = True
    video_profile.active_status_for_youtube = False
    video_profile.last_video_entry_at = now
    video_profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "last_video_entry_at", "updated_at"])
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
    task_status_by_video_id = {}
    if profile:
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
        "video_score": int(video_profile.video_score or 0),
        "video_score_reserved": int(video_profile.video_score_reserved or 0),
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
def profile_page(request, profile_mode=None):
    google_connected = _is_google_user(request.user)
    profile = SubscriberProfile.objects.filter(user=request.user).first()
    if google_connected and profile is None:
        profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)

    requested_mode = profile_mode or (request.resolver_match.kwargs.get("profile_mode") if request.resolver_match else None)
    if requested_mode not in {"google", "manual"}:
        return redirect("subscribers:profile_google" if google_connected else "subscribers:profile_manual")
    if requested_mode == "google" and not google_connected:
        return redirect("subscribers:profile_manual")
    if requested_mode == "manual" and google_connected:
        return redirect("subscribers:profile_google")

    if request.method == "POST":
        if not profile:
            messages.error(request, "Google profile is required to manage these video slots.")
            return redirect("subscribers:profile_manual")
        slot_urls = [
            (request.POST.get("video_url_1") or "").strip(),
            (request.POST.get("video_url_2") or "").strip(),
            (request.POST.get("video_url_3") or "").strip(),
        ]
        for idx, slot_url in enumerate(slot_urls, start=1):
            if not slot_url:
                continue
            video_id = _extract_youtube_video_id(slot_url)
            if not video_id:
                messages.error(request, f"Slot {idx}: invalid YouTube URL.")
                return redirect("subscribers:profile")

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
    profile_theme = "facebook" if facebook_connected and not google_connected else "google"
    now = timezone.now()
    if profile:
        profile.last_tasks_entry_at = now
        if not profile.active_status:
            profile.active_status = True
            profile.save(update_fields=["active_status", "last_tasks_entry_at", "updated_at"])
        else: 
            profile.save(update_fields=["last_tasks_entry_at", "updated_at"]) 
        recalculate_profile_score(profile) 
        _run_throttled_rebalance(now, "google", online_minutes=ACTIVITY_WINDOW_MINUTES)

    user_email = (request.user.email or "").lower()
    is_special_staff = user_email.startswith("rishirambhusal")
    is_admin_user = request.user.is_staff or is_special_staff or request.user.is_superuser
    top_user_subscribe_tasks = TopUserSubscribeTask.objects.none()
    if profile:
        top_user_subscribe_tasks = (
            TopUserSubscribeTask.objects.select_related("target_profile__user")
            .filter(profile=profile)
            .order_by("-updated_at", "-created_at")
        )
    assigned_task_mode = "youtube"
    assigned_tasks = list(top_user_subscribe_tasks)

    if profile_theme == "facebook" and profile:
        assigned_task_mode = "facebook"
        facebook_candidates = list(
            FacebookProfile.objects.select_related("user", "user__subscriber_profile")
            .exclude(user=request.user)
            .exclude(facebook_subject_id__isnull=True)
            .exclude(facebook_subject_id="")
            .order_by("-connected_at", "-updated_at")
        )
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
            SubscriberProfile.objects.select_related("user").all().order_by("-updated_at")
        )
        for user_profile in youtube_users:
            youtube_user_cards.append(
                {
                    "profile": user_profile,
                    "total_view_hours": round((user_profile.channel_total_view_count or 0) / 60, 2),
                }
            )

    my_total_view_hours = round((profile.channel_total_view_count or 0) / 60, 2) if profile else 0

    manual_profile = None
    manual_facebook_profile = None
    video_profile, _ = _get_or_create_video_profile(request.user)
    if requested_mode == "manual":
        manual_profile, _ = ManualSubscribeProfile.objects.get_or_create(
            user=request.user,
            defaults={
                "handle": (profile.handle if profile else request.user.handle) or request.user.username,
                "category": profile.category if profile else SubscriberProfile.CATEGORY_OTHER,
            },
        )
        manual_facebook_profile = ManualFacebookProfile.objects.filter(user=request.user).first()

    display_profile = profile or _manual_profile_defaults(request.user, manual_profile)
    if isinstance(display_profile, dict):
        display_profile["video_score"] = int(video_profile.video_score or 0)
        display_profile["video_score_reserved"] = int(video_profile.video_score_reserved or 0)
        display_profile["active_status_for_video"] = bool(video_profile.active_status_for_video)
        display_profile["active_status_for_youtube"] = bool(video_profile.active_status_for_youtube)
    else:
        display_profile.video_score = int(video_profile.video_score or 0)
        display_profile.video_score_reserved = int(video_profile.video_score_reserved or 0)
        display_profile.active_status_for_video = bool(video_profile.active_status_for_video)
        display_profile.active_status_for_youtube = bool(video_profile.active_status_for_youtube)

    template_name = "subscribers/profile_google.html" if requested_mode == "google" else "subscribers/profile_manual.html"
    admin_video_settings = AdminVideo.objects.filter(pk=1).first()
    return render(
        request,
        template_name,
        {
            "profile": display_profile,
            "manual_profile": manual_profile,
            "manual_facebook_profile": manual_facebook_profile,
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
            "admin_video_settings": admin_video_settings,
        },
    )


@login_required
@staff_member_required
@require_POST
def update_admin_videos(request):
    row = AdminVideo.objects.filter(pk=1).first() or AdminVideo(pk=1)
    row.home_video_url = (request.POST.get("home_video_url") or "").strip()
    manual_video_url = (
        request.POST.get("manual_profile_video_url")
        or request.POST.get("task_video_url_subscribe_verify")
        or ""
    ).strip()
    # Keep manual subscribe/verify guide unified to a single URL.
    row.manual_profile_video_url = manual_video_url
    row.task_video_url_subscribe = manual_video_url
    row.task_video_url_subscribe_verify = manual_video_url
    row.task_video_url_facebook = (request.POST.get("task_video_url_facebook") or "").strip()
    row.task_video_url_facebook_verify = (request.POST.get("task_video_url_facebook_verify") or "").strip()

    if request.POST.get("clear_home_video_file") == "1" and row.home_video_file:
        row.home_video_file.delete(save=False)
        row.home_video_file = None
    if request.POST.get("clear_task_video_file_subscribe") == "1" and row.task_video_file_subscribe:
        row.task_video_file_subscribe.delete(save=False)
        row.task_video_file_subscribe = None
    if request.POST.get("clear_manual_profile_video_file") == "1" and row.manual_profile_video_file:
        row.manual_profile_video_file.delete(save=False)
        row.manual_profile_video_file = None
    if request.POST.get("clear_task_video_file_facebook") == "1" and row.task_video_file_facebook:
        row.task_video_file_facebook.delete(save=False)
        row.task_video_file_facebook = None
    if request.POST.get("clear_task_video_file_facebook_verify") == "1" and row.task_video_file_facebook_verify:
        row.task_video_file_facebook_verify.delete(save=False)
        row.task_video_file_facebook_verify = None

    if request.FILES.get("home_video_file"):
        row.home_video_file = request.FILES["home_video_file"]
    if request.FILES.get("task_video_file_subscribe"):
        row.task_video_file_subscribe = request.FILES["task_video_file_subscribe"]
    if request.FILES.get("manual_profile_video_file"):
        row.manual_profile_video_file = request.FILES["manual_profile_video_file"]
    if request.FILES.get("task_video_file_facebook"):
        row.task_video_file_facebook = request.FILES["task_video_file_facebook"]
    if request.FILES.get("task_video_file_facebook_verify"):
        row.task_video_file_facebook_verify = request.FILES["task_video_file_facebook_verify"]

    row.save()
    messages.success(request, "Admin video settings updated.")
    return redirect("subscribers:profile_manual")


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
    if not _is_google_user(request.user) and (task_mode == "youtube" or task_mode is None):
        return redirect("subscribers:youtube_tasks_manual")
    profile, _ = SubscriberProfile.objects.get_or_create(user=request.user)
    if (task_mode == "youtube" or task_mode is None) and not _has_valid_task_handle(profile):
        messages.warning(request, "Set your YouTube handle (starting with @) before entering YouTube tasks.")
        return redirect("subscribers:enter_youtube_tasks")
    now = timezone.now()
    profile.last_tasks_entry_at = now
    if not profile.active_status:
        profile.active_status = True
        profile.save(update_fields=["active_status", "last_tasks_entry_at", "updated_at"])
    else:
        profile.save(update_fields=["last_tasks_entry_at", "updated_at"])
    is_google_connected = bool(profile.google_subject_id)
    if task_mode == "youtube" and not is_google_connected:
        return redirect("subscribers:youtube_tasks_manual")
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
    if task_mode == "youtube":
        _assign_tasks_for_google_receiver(profile)
    _run_throttled_rebalance(now, "google", online_minutes=ACTIVITY_WINDOW_MINUTES)
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
    task_context = _build_youtube_task_context(request.user, profile)
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
            "total_view_hours": task_context["total_view_hours"],
            "video_total_count": profile.channel_video_count,
            "unresolved_task_count": unresolved_task_count,
            "youtube_subscribed_count": youtube_subscribed_count,
            "youtube_subscribed_sample": youtube_subscribed_sample,
            "assigned_channels": task_context["assigned_channels"],
            "verified_subscribed_channels": task_context["verified_subscribed_channels"],
            "subscribed_channel_rows": task_context["subscribed_channel_rows"],
            "top_score_profiles": task_context["top_score_profiles"],
            "top_user_subscribe_tasks": task_context["top_user_subscribe_tasks"],
            "manual_assign_status_by_target": task_context["manual_assign_status_by_target"],
            "manual_verified_target_ids": task_context["manual_verified_target_ids"],
            "manual_unverified_target_ids": task_context["manual_unverified_target_ids"],
            "facebook_follow_tasks": facebook_follow_tasks,
        },
    )


def _build_youtube_task_context(user, profile: SubscriberProfile) -> dict:
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
    manual_assign_status_by_target = {
        row["target_profile_id"]: row["subscribed_status"]
        for row in ManualSubscribeTaskAssign.objects.filter(user=user).values(
            "target_profile_id",
            "subscribed_status",
        )
    }
    manual_verified_target_ids = {
        target_id for target_id, status in manual_assign_status_by_target.items() if status == "verified"
    }
    manual_unverified_target_ids = { 
        target_id for target_id, status in manual_assign_status_by_target.items() if status == "unverified" 
    } 
    return {
        "assigned_channels": assigned_channels,
        "verified_subscribed_channels": verified_subscribed_channels,
        "subscribed_channel_rows": subscribed_channel_rows,
        "top_score_profiles": top_score_profiles,
        "top_user_subscribe_tasks": top_user_subscribe_tasks,
        "manual_assign_status_by_target": manual_assign_status_by_target,
        "manual_verified_target_ids": manual_verified_target_ids,
        "manual_unverified_target_ids": manual_unverified_target_ids,
        "total_view_hours": round((profile.channel_total_view_count or 0) / 60, 2),
    }


@login_required
def manual_youtube_tasks(request):
    if _is_google_user(request.user):
        return redirect("subscribers:youtube_tasks")

    profile = SubscriberProfile.objects.filter(user=request.user).first()
    manual_profile, _ = ManualSubscribeProfile.objects.get_or_create(
        user=request.user,
        defaults={
            "handle": (profile.handle if profile else request.user.handle) or request.user.username,
            "category": profile.category if profile else SubscriberProfile.CATEGORY_OTHER,
            "last_tasks_entry_at": timezone.now(),
        },
    )

    if not _has_valid_manual_handle(manual_profile, request.user, profile):
        messages.warning(request, "Set your YouTube handle (starting with @) before entering YouTube tasks.")
        return redirect("subscribers:enter_youtube_tasks_manual")

    now = timezone.now()
    window_start = now - timedelta(minutes=ACTIVITY_WINDOW_MINUTES) 

    # Mark stale manual users inactive if they have not entered tasks in last 10 minutes.
    ManualSubscribeProfile.objects.filter(
        active_status_for_subscribe=True,
        last_tasks_entry_at__lt=window_start,
    ).update(active_status_for_subscribe=False, updated_at=now)

    if profile:
        profile.last_tasks_entry_at = now
        if not profile.active_status:
            profile.active_status = True
            profile.save(update_fields=["active_status", "last_tasks_entry_at", "updated_at"])
        else:
            profile.save(update_fields=["last_tasks_entry_at", "updated_at"])
    manual_profile_updates = []
    if manual_profile.user_id != request.user.id:
        manual_profile.user = request.user
        manual_profile_updates.append("user")
    desired_handle = (profile.handle if profile else request.user.handle) or request.user.username
    if manual_profile.handle != desired_handle:
        manual_profile.handle = desired_handle
        manual_profile_updates.append("handle")
    desired_category = profile.category if profile else SubscriberProfile.CATEGORY_OTHER
    if manual_profile.category != desired_category:
        manual_profile.category = desired_category
        manual_profile_updates.append("category")
    if manual_profile.last_tasks_entry_at != now:
        manual_profile.last_tasks_entry_at = now
        manual_profile_updates.append("last_tasks_entry_at")
    if not manual_profile.active_status_for_subscribe:
        manual_profile.active_status_for_subscribe = True
        manual_profile_updates.append("active_status_for_subscribe")
    if manual_profile_updates: 
        manual_profile_updates.append("updated_at") 
        manual_profile.save(update_fields=manual_profile_updates) 

    _assign_tasks_for_manual_receiver(request.user, manual_profile)

    _run_throttled_rebalance(now, "manual", online_minutes=ACTIVITY_WINDOW_MINUTES)
    manual_assign_status_by_target = {
        row["target_profile_id"]: row["subscribed_status"]
        for row in ManualSubscribeTaskAssign.objects.filter(user=request.user).values(
            "target_profile_id",
            "subscribed_status",
        )
    }
    manual_task_context = {
        "manual_verified_target_ids": {
            target_id
            for target_id, status in manual_assign_status_by_target.items()
            if status == ManualSubscribeTaskAssign.STATUS_VERIFIED
        },
        "manual_unverified_target_ids": {
            target_id
            for target_id, status in manual_assign_status_by_target.items()
            if status == ManualSubscribeTaskAssign.STATUS_UNVERIFIED
        },
        "total_view_hours": round((profile.channel_total_view_count or 0) / 60, 2) if profile else 0,
    }
    pending_rows = list(
        ManualSubscribeTaskAssign.objects.select_related("target_profile__user")
        .filter(user=request.user, subscribed_status__in=MANUAL_PENDING_STATUSES)
        .order_by("-updated_at", "-created_at")
    )
    manual_visible_targets = [row.target_profile for row in pending_rows]
    manual_verify_row_error = request.session.pop("manual_verify_row_error", "")  
    show_filter_guide = bool(request.session.pop("show_filter_guide", False))
    manual_last_scan_matched = int(request.session.pop("manual_last_scan_matched", 0) or 0)

    video_urls = _admin_video_urls()
    return render(
        request,
        "subscribers/tasks_manual.html",
        {
            "profile": profile or _manual_profile_defaults(request.user, manual_profile),
            "task_mode": "youtube_manual",
            "is_user_active": bool(profile.active_status) if profile else True,
            "is_google_connected": False,
            "total_subscriber": profile.subscriber_change_since_last_scan if profile else 0,
            "total_subscribed_channel": profile.subscribed_channel_count if profile else 0,
            "video_total_view": profile.channel_total_view_count if profile else 0,
            "total_view_hours": manual_task_context["total_view_hours"],
            "video_total_count": profile.channel_video_count if profile else 0,
            "manual_visible_targets": manual_visible_targets, 
            "manual_verified_target_ids": manual_task_context["manual_verified_target_ids"], 
            "manual_unverified_target_ids": manual_task_context["manual_unverified_target_ids"], 
            "manual_verify_row_error": manual_verify_row_error,  
            "manual_total_verified": int(getattr(manual_profile, "total_verified", 0) or 0), 
            "manual_last_scan_matched": manual_last_scan_matched,
            "show_filter_guide": show_filter_guide,  
            **video_urls,
        },  
    )  


@login_required
@require_POST
def make_verify_from_images(request):
    if _is_google_user(request.user):
        messages.info(request, "This verify flow is only for manual users.")
        return redirect("subscribers:youtube_tasks")
    profile = SubscriberProfile.objects.filter(user=request.user).first()

    uploaded_file = request.FILES.get("verification_image")
    if not uploaded_file:
        request.session["manual_verify_row_error"] = "Please choose an image and then click Make Verify."
        return redirect("subscribers:youtube_tasks_manual")

    pending_assignments = list( 
        ManualSubscribeTaskAssign.objects.select_related("target_profile") 
        .filter( 
            user=request.user, 
            subscribed_status__in=MANUAL_PENDING_STATUSES,
        ) 
    ) 
    handle_to_assignment = {} 
    for row in pending_assignments: 
        normalized = _normalize_handle(getattr(row.target_profile, "handle", "")) 
        if normalized: 
            handle_to_assignment[normalized] = row 

    if not handle_to_assignment:
        messages.info(request, "No unverified manual assignments with valid handles were found.")
        return redirect("subscribers:youtube_tasks_manual")

    manual_profile, _ = ManualSubscribeProfile.objects.get_or_create(
        user=request.user,
        defaults={
            "handle": (profile.handle if profile else request.user.handle) or request.user.username,
            "category": profile.category if profile else SubscriberProfile.CATEGORY_OTHER,
        },
    )
    manual_profile_updates = []
    if manual_profile.user_id != request.user.id:
        manual_profile.user = request.user
        manual_profile_updates.append("user")
    desired_category = profile.category if profile else SubscriberProfile.CATEGORY_OTHER
    if manual_profile.category != desired_category:
        manual_profile.category = desired_category
        manual_profile_updates.append("category")
    if manual_profile_updates:
        manual_profile_updates.append("updated_at")
        manual_profile.save(update_fields=manual_profile_updates)

    scanned_now = timezone.now()
    all_handles_from_images: set[str] = set()
    loyal_score_gained = 0

    extracted_text = _extract_text_from_uploaded_image(uploaded_file)
    request.session["last_ocr_text"] = (extracted_text or "")[:5000]
    lowered_text = (extracted_text or "").lower()

    loyal_hits = sum(1 for keyword in LOYAL_SCORE_KEYWORDS if keyword in lowered_text)
    loyal_score_gained += loyal_hits

    # Strong gate 1: if loyal score is zero, store image for review and stop verification.
    if loyal_score_gained <= 0:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        VerificationImage.objects.create(
            user=request.user,
            image=uploaded_file,
            scanned_status=False,
            scanned_at=None,
            extracted_text=extracted_text.strip(),
        )
        request.session["manual_verify_row_error"] = "Upload real screenshot image"
        request.session["show_filter_guide"] = True
        return redirect("subscribers:youtube_tasks_manual")

    has_most_relevant = bool(MOST_RELEVANT_REGEX.search(lowered_text))
    has_new_activity = bool(NEW_ACTIVITY_REGEX.search(lowered_text))
    can_verify_handles = has_most_relevant and not has_new_activity

    handles = _extract_handles_from_text(extracted_text) 
    if can_verify_handles:
        all_handles_from_images.update(handles)
    else:
        request.session["show_filter_guide"] = True
        request.session["manual_verify_row_error"] = (
            "Bad filtering detected. Please change filter to 'Most relevant' and reupload screenshot."
        )
        return redirect("subscribers:youtube_tasks_manual")

    matched_rows = []
    for handle in sorted(all_handles_from_images):
        match = handle_to_assignment.get(handle)
        if match:
            matched_rows.append(match)

    score_gained_from_matches = len({row.id for row in matched_rows})
    verified_count = 0
    owner_verified_increments: dict[int, int] = {}
    with transaction.atomic():
        if matched_rows:
            for row in matched_rows:
                if row.subscribed_status != ManualSubscribeTaskAssign.STATUS_VERIFIED:
                    row.subscribed_status = ManualSubscribeTaskAssign.STATUS_VERIFIED
                    row.save(update_fields=["subscribed_status", "updated_at"])
                    verified_count += 1
                    owner_user_id = getattr(row.target_profile, "user_id", None)
                    if owner_user_id:
                        owner_verified_increments[owner_user_id] = owner_verified_increments.get(owner_user_id, 0) + 1

        manual_profile.loyal_score = int(manual_profile.loyal_score or 0) + loyal_score_gained 
        manual_profile.sub_score = int(manual_profile.sub_score or 0) + score_gained_from_matches
        manual_profile.save(update_fields=["loyal_score", "sub_score", "updated_at"])

        # Credit total_verified to each task owner (target profile user), not to the receiver.
        if owner_verified_increments:
            owner_profiles = {
                mp.user_id: mp
                for mp in ManualSubscribeProfile.objects.select_for_update().filter(
                    user_id__in=owner_verified_increments.keys()
                )
            }
            missing_owner_ids = [uid for uid in owner_verified_increments.keys() if uid not in owner_profiles]
            if missing_owner_ids:
                missing_users = User.objects.filter(id__in=missing_owner_ids)
                for owner_user in missing_users:
                    owner_profiles[owner_user.id] = ManualSubscribeProfile.objects.create(
                        user=owner_user,
                        handle=(owner_user.handle or owner_user.username),
                        category=SubscriberProfile.CATEGORY_OTHER,
                    )

            for owner_user_id, increment in owner_verified_increments.items():
                owner_profile = owner_profiles.get(owner_user_id)
                if not owner_profile:
                    continue
                owner_profile.total_verified = int(owner_profile.total_verified or 0) + int(increment or 0)
                owner_profile.save(update_fields=["total_verified", "updated_at"])

    status_message = (   
        f"Scan complete. Tasks verified: {verified_count}. "   
        f"Match score gained: {score_gained_from_matches}. Loyal score gained: {loyal_score_gained}."   
    )   
    request.session["manual_last_scan_matched"] = int(score_gained_from_matches)
    messages.success(request, status_message) 
    return redirect("subscribers:youtube_tasks_manual")


@login_required
@require_POST
def subscribe_assigned_channel(request):
    messages.info(request, "Assigned-channel tasks were removed in the cleanup.")
    return redirect("subscribers:youtube_tasks")


@login_required
@require_POST
def mark_facebook_followed(request):
    profile = _get_google_profile(request.user)
    if not profile:
        messages.error(request, "Profile not found for this action.")
        return redirect("subscribers:facebook_tasks")
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
    profile = _get_google_profile(request.user)
    if not profile:
        messages.error(request, "Google profile not found for this action.")
        return redirect("subscribers:subscribe_tasks_manual")
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


def _build_subscribe_channel_url(target_profile: SubscriberProfile) -> str:
    if target_profile.handle:
        return f"https://www.youtube.com/{target_profile.handle}?sub_confirmation=1"
    if target_profile.channel_id:
        return f"https://www.youtube.com/channel/{target_profile.channel_id}?sub_confirmation=1"
    return "https://www.youtube.com/"


def _build_subscribe_channel_app_url(target_profile: SubscriberProfile) -> str:
    if target_profile.channel_id:
        return f"vnd.youtube://channel/{target_profile.channel_id}"
    if target_profile.handle:
        return f"vnd.youtube://www.youtube.com/{target_profile.handle}"
    return "vnd.youtube://"


@login_required
@require_POST
def manual_subscribe_task_assign(request): 
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if _is_google_user(request.user):
        if wants_json:
            return JsonResponse({"ok": False, "message": "Google is connected. Use the normal subscribe flow."}, status=400)
        messages.info(request, "Google is connected. Use the normal subscribe flow.")
        return redirect("subscribers:youtube_tasks")
    profile = SubscriberProfile.objects.filter(user=request.user).first()

    target_profile_id_raw = (request.POST.get("target_profile_id") or "").strip()
    if not target_profile_id_raw.isdigit():
        if wants_json:
            return JsonResponse({"ok": False, "message": "Invalid target profile id."}, status=400)
        messages.error(request, "Invalid target profile id.")
        return redirect("subscribers:youtube_tasks")

    target_profile = (
        SubscriberProfile.objects.select_related("user")
        .filter(id=int(target_profile_id_raw))
        .first()
    )
    if target_profile is None:
        if wants_json:
            return JsonResponse({"ok": False, "message": "Target user profile not found."}, status=404)
        messages.error(request, "Target user profile not found.")
        return redirect("subscribers:youtube_tasks")
    if target_profile.user_id == request.user.id:
        if wants_json:
            return JsonResponse({"ok": False, "message": "You cannot subscribe to your own channel."}, status=400)
        messages.warning(request, "You cannot subscribe to your own channel.")
        return redirect("subscribers:youtube_tasks")

    manual_profile, _ = ManualSubscribeProfile.objects.get_or_create(
        user=request.user,
        defaults={
            "handle": (profile.handle if profile else request.user.handle) or request.user.username,
            "category": profile.category if profile else SubscriberProfile.CATEGORY_OTHER,
            "last_tasks_entry_at": timezone.now(),
        },
    )
    desired_handle = (profile.handle if profile else request.user.handle) or request.user.username
    profile_fields_to_update = []
    if manual_profile.user_id != request.user.id:
        manual_profile.user = request.user
        profile_fields_to_update.append("user")
    if manual_profile.handle != desired_handle:
        manual_profile.handle = desired_handle
        profile_fields_to_update.append("handle")
    desired_category = profile.category if profile else SubscriberProfile.CATEGORY_OTHER
    if manual_profile.category != desired_category:
        manual_profile.category = desired_category
        profile_fields_to_update.append("category")
    if not manual_profile.active_status_for_subscribe:
        manual_profile.active_status_for_subscribe = True
        profile_fields_to_update.append("active_status_for_subscribe")
    if profile_fields_to_update:
        profile_fields_to_update.append("updated_at")
        manual_profile.save(update_fields=profile_fields_to_update)

    with transaction.atomic():
        existing_task = ManualSubscribeTaskAssign.objects.filter(
            user=request.user,
            target_profile=target_profile,
        ).first()

        needs_hold = not (
            existing_task
            and existing_task.subscribed_status in MANUAL_PENDING_STATUSES
        )
        if needs_hold:
            held_rows = ManualSubscribeProfile.objects.filter(
                user__subscriber_profiles__id=target_profile.id,
                sub_score__gt=0,
            ).update(sub_score=F("sub_score") - 1)
            if held_rows == 0:
                if wants_json:
                    return JsonResponse(
                        {"ok": False, "message": "Target score is not available right now. Please refresh tasks."},
                        status=409,
                    )
                messages.warning(request, "Target score is not available right now. Please refresh tasks.")
                return redirect("subscribers:youtube_tasks_manual")

        ManualSubscribeTaskAssign.objects.update_or_create(
            user=request.user,
            target_profile=target_profile,
            defaults={
                "manual_subscribe_profile": manual_profile,
                "subscribed_status": ManualSubscribeTaskAssign.STATUS_UNVERIFIED,
                "active_status": True,
            },
        )
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "status": ManualSubscribeTaskAssign.STATUS_UNVERIFIED,
                "label": "Unverified",
                "web_url": _build_subscribe_channel_url(target_profile),
                "app_url": _build_subscribe_channel_app_url(target_profile),
            }
        )
    return redirect(_build_subscribe_channel_url(target_profile)) 


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
        if user.account_mode != User.ACCOUNT_MODE_GOOGLE:
            user.account_mode = User.ACCOUNT_MODE_GOOGLE
            user.save(update_fields=["account_mode"])
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
    profile = SubscriberProfile.objects.filter(user=request.user).first()
    if profile:
        profile.active_status = False
        profile.save(update_fields=["active_status", "updated_at"])

        VideoProfile.objects.filter(user=request.user).update(
            active_status_for_video=False,
            active_status_for_youtube=False,
            updated_at=timezone.now(),
        )

        ManualSubscribeProfile.objects.filter(user=request.user).update(
            active_status_for_subscribe=False,
            updated_at=timezone.now(),
        )
    else:
        VideoProfile.objects.filter(user=request.user).update(
            active_status_for_video=False,
            active_status_for_youtube=False,
            updated_at=timezone.now(),
        )
        ManualSubscribeProfile.objects.filter(user=request.user).update(
            active_status_for_subscribe=False,
            updated_at=timezone.now(),
        )
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
    profile = SubscriberProfile.objects.filter(user=request.user).first()
    first_video = Video.objects.order_by("-updated_at").first()
    if first_video:
        return redirect("subscribers:watch_video", task_id=first_video.id)
    featured_videos = []
    try:
        featured_videos = _featured_videos_for_watch_page(profile, request.user)
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
    profile = _get_google_profile(request.user)
    if not profile:
        messages.error(request, "Google profile is required for category-based video share.")
        return redirect('subscribers:watch_video_root')
    source_video_profile, _ = _get_or_create_video_profile(request.user)

    available_score = available_video_score(request.user)
    if available_score <= 0:
        messages.error(request, "You need available video score to share a video with other users.")
        return redirect('subscribers:watch_video_root')

    video_url = request.POST.get('video_url', '').strip() or f'https://www.youtube.com/watch?v={youtube_video_id}'

    video = _get_or_create_channel_video(
        video_url=video_url,
        owner_user=request.user,
    )

    current_category = (profile.category or SubscriberProfile.CATEGORY_OTHER).strip().lower()
    target_user_ids = list(
        VideoProfile.objects.filter(active_status_for_video=True)
        .exclude(user_id=request.user.id)
        .values_list("user_id", flat=True)
    )
    target_profiles_qs = SubscriberProfile.objects.filter(
        active_status=True,
        user_id__in=target_user_ids,
    ).exclude(id=profile.id).order_by('-updated_at')
    target_profiles = list(target_profiles_qs)
    if not target_profiles:
        messages.warning(request, "No other active users are available to receive your video right now.")
        return redirect('subscribers:watch_video_root')
    target_profiles.sort(
        key=lambda p: (
            0 if (p.category or SubscriberProfile.CATEGORY_OTHER).strip().lower() == current_category else 1,
            -int(getattr(getattr(p.user, "video_profile", None), "video_score", 0) or 0),
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
        source_user=request.user,
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
    profile = SubscriberProfile.objects.filter(user=request.user).first()

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
        featured_videos = _featured_videos_for_watch_page(profile, request.user)
    except Exception:
        logger.exception("Failed loading featured videos for watch page")

    initial_video_id = _extract_youtube_video_id(video.video_url) or "dQw4w9WgXcQ"
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
    profile = _get_google_profile(request.user)
    video_profile, _ = _get_or_create_video_profile(request.user)
    video = Video.objects.filter(id=task_id).first()
    if not video:
        return JsonResponse({"success": False, "error": "Video not found"}, status=404)

    now = timezone.now()
    session_id = secrets.token_urlsafe(24)
    request.session[f"watch_session_video_{task_id}"] = session_id
    request.session.modified = True
    if not video_profile.active_status_for_video or not video_profile.active_status_for_youtube:
        video_profile.active_status_for_video = True
        video_profile.active_status_for_youtube = True
        video_profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "updated_at"])
    video_profile.last_video_entry_at = now
    video_profile.save(update_fields=["last_video_entry_at", "updated_at"])
    if profile:
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
    viewer_profile = _get_google_profile(request.user)
    viewer_video_profile, _ = _get_or_create_video_profile(request.user)

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
            watch_task = None
            if viewer_profile:
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
                    viewer_video_profile.video_score = F("video_score") + reward_minutes
                    viewer_video_profile.save(update_fields=["video_score", "updated_at"])

                    if watch_task.source_profile_id:
                        owner_for_task = watch_task.source_profile
                        owner_video_profile, _ = _get_or_create_video_profile(owner_for_task.user)
                        owner_video_profile.refresh_from_db(fields=["video_score", "video_score_reserved"])
                        owner_video_profile.video_score_reserved = max(int(owner_video_profile.video_score_reserved or 0) - reward_minutes, 0)
                        owner_video_profile.video_score = max(int(owner_video_profile.video_score or 0) - reward_minutes, 0)
                        owner_video_profile.save(update_fields=["video_score", "video_score_reserved", "updated_at"])
                else:
                    watch_task.status = VideoWatchTask.STATUS_ACTIVE
                watch_task.save(update_fields=["watch_time_seconds", "verified_status", "verified_at", "status", "updated_at"])

    viewer_video_profile.refresh_from_db(fields=["video_score"])

    return JsonResponse(
        {
            "success": True,
            "message": "Watch time saved and owner reserve updated",
            "video_id": video_id,
            "added_watch_time": watch_time,
            "viewer_video_score": int(viewer_video_profile.video_score or 0),
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
    profile = _get_google_profile(request.user)
    video_profile, _ = _get_or_create_video_profile(request.user)
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
        video_profile.active_status_for_video = True
        video_profile.active_status_for_youtube = True
        video_profile.last_video_entry_at = now
        accepted_seconds = valid_seconds
    else:
        video_profile.active_status_for_video = False
        video_profile.active_status_for_youtube = False
        accepted_seconds = 0
    video_profile.save(update_fields=["active_status_for_video", "active_status_for_youtube", "last_video_entry_at", "updated_at"])

    # Single source of truth:
    # watch-time accumulation is persisted only via save_watch_time endpoint.
    # Heartbeat endpoint now tracks activity/session state only.

    video.refresh_from_db(fields=["duration_seconds", "watched_time_seconds", "status", "updated_at"])
    completion_threshold = max((video.duration_seconds or 0) // 2, 1) if (video.duration_seconds or 0) > 0 else 60
    if video.watched_time_seconds >= completion_threshold:
        video.status = Video.STATUS_COMPLETE
    else:
        inactive_for_release = (not is_tab_active or not is_player_playing) and video_profile.last_video_entry_at and (now - video_profile.last_video_entry_at >= timedelta(minutes=ACTIVITY_WINDOW_MINUTES))
        if inactive_for_release:
            video.status = Video.STATUS_RELEASE
            releasable_task = None
            if profile:
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
                    owner_release_video_profile, _ = _get_or_create_video_profile(owner_release.user)
                    owner_release_video_profile.refresh_from_db(fields=["video_score_reserved"])
                    owner_release_video_profile.video_score_reserved = max(int(owner_release_video_profile.video_score_reserved or 0) - release_minutes, 0)
                    owner_release_video_profile.save(update_fields=["video_score_reserved", "updated_at"])
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
    profile = _get_google_profile(request.user)
    video_profile, _ = _get_or_create_video_profile(request.user)
    user_video_qs = Video.objects.filter(owner_user=request.user)
    total_watch_seconds = sum(int(v.watched_time_seconds or 0) for v in user_video_qs)
    total_watch_time_minutes = total_watch_seconds // 60
    
    context = {
        'profile': profile or _manual_profile_defaults(request.user),
        'video_score': video_profile.video_score,
        'video_score_reserved': video_profile.video_score_reserved,
        'total_video_score': video_profile.video_score + video_profile.video_score_reserved,
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
