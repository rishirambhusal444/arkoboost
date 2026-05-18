import datetime as dt
import json
import math
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    SubscriberProfile,
    TopUserSubscribeTask,
    Video,
    VideoProfile,
    VideoWatchTask,
    WatchEvent,
)

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'
YOUTUBE_CHANNELS_URL = 'https://www.googleapis.com/youtube/v3/channels'
YOUTUBE_SUBSCRIPTIONS_URL = 'https://www.googleapis.com/youtube/v3/subscriptions'
YOUTUBE_SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl'
FACEBOOK_SCOPE = 'public_profile,pages_show_list,pages_read_engagement'
FACEBOOK_BASIC_SCOPE = 'public_profile'


class YouTubeOAuthError(Exception):
    pass


class FacebookOAuthError(Exception):
    pass


def _facebook_api_base() -> str:
    version = (settings.FACEBOOK_GRAPH_VERSION or 'v22.0').strip().lstrip('/')
    return f'https://graph.facebook.com/{version}'


def build_facebook_authorize_url(state: str, scope: str | None = None) -> str:
    version = (settings.FACEBOOK_GRAPH_VERSION or 'v22.0').strip().lstrip('/')
    params = {
        'client_id': settings.FACEBOOK_CLIENT_ID,
        'redirect_uri': settings.FACEBOOK_REDIRECT_URI,
        'response_type': 'code',
        'scope': scope or FACEBOOK_SCOPE,
        'state': state,
    }
    return f'https://www.facebook.com/{version}/dialog/oauth?{urlencode(params)}'


def build_google_authorize_url(state: str) -> str:
    params = {
        'client_id': settings.YOUTUBE_CLIENT_ID,
        'redirect_uri': settings.YOUTUBE_REDIRECT_URI,
        'response_type': 'code',
        'scope': f'openid email profile {YOUTUBE_SCOPE}',
        'access_type': 'offline',
        'include_granted_scopes': 'true',
        'prompt': 'consent',
        'state': state,
    }
    return f'{GOOGLE_AUTH_URL}?{urlencode(params)}'


def _json_request(url: str, *, method: str = 'GET', data=None, headers=None):
    encoded_data = None
    request_headers = headers or {}
    if data is not None:
        encoded_data = urlencode(data).encode('utf-8')
        request_headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')

    request = Request(url, data=encoded_data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')
        raise YouTubeOAuthError(f'HTTP {exc.code}: {details}') from exc
    except URLError as exc:
        raise YouTubeOAuthError(f'Network error: {exc.reason}') from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YouTubeOAuthError('Received non-JSON response from Google API.') from exc


def _facebook_json_request(url: str, *, method: str = 'GET', data=None, headers=None):
    encoded_data = None
    request_headers = headers or {}
    if data is not None:
        encoded_data = urlencode(data).encode('utf-8')
        request_headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')

    request = Request(url, data=encoded_data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')
        raise FacebookOAuthError(f'HTTP {exc.code}: {details}') from exc
    except URLError as exc:
        raise FacebookOAuthError(f'Network error: {exc.reason}') from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FacebookOAuthError('Received non-JSON response from Facebook API.') from exc


def _json_request_raw(url: str, *, method: str = 'GET', raw_data: bytes | None = None, headers=None):
    request = Request(url, data=raw_data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')
        raise YouTubeOAuthError(f'HTTP {exc.code}: {details}') from exc
    except URLError as exc:
        raise YouTubeOAuthError(f'Network error: {exc.reason}') from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YouTubeOAuthError('Received non-JSON response from Google API.') from exc


def parse_google_api_error(error_text: str) -> dict:
    """Extract structured details from a Google API error string when possible."""
    prefix, _, payload = error_text.partition(': ')
    if not prefix.startswith('HTTP ') or not payload:
        return {}

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}

    error = data.get('error') or {}
    errors = error.get('errors') or []
    first_error = errors[0] if errors else {}
    details = error.get('details') or []

    activation_url = ''
    service_name = ''
    for detail in details:
        metadata = detail.get('metadata') or {}
        activation_url = activation_url or metadata.get('activationUrl', '')
        service_name = service_name or metadata.get('serviceTitle', '')

    return {
        'status_code': prefix.removeprefix('HTTP ').strip(),
        'message': error.get('message', ''),
        'reason': first_error.get('reason', ''),
        'activation_url': activation_url,
        'service_name': service_name,
    }


def recalculate_profile_score(profile: SubscriberProfile) -> int:
    """Score is remaining task capacity for this profile."""
    profile.refresh_from_db(fields=['score'])
    return int(profile.score or 0)


def exchange_code_for_token(code: str) -> dict:
    payload = {
        'client_id': settings.YOUTUBE_CLIENT_ID,
        'client_secret': settings.YOUTUBE_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': settings.YOUTUBE_REDIRECT_URI,
    }
    return _json_request(GOOGLE_TOKEN_URL, method='POST', data=payload)


def exchange_facebook_code_for_token(code: str) -> dict:
    params = {
        'client_id': settings.FACEBOOK_CLIENT_ID,
        'client_secret': settings.FACEBOOK_CLIENT_SECRET,
        'code': code,
        'redirect_uri': settings.FACEBOOK_REDIRECT_URI,
    }
    return _facebook_json_request(f'{_facebook_api_base()}/oauth/access_token?{urlencode(params)}')


def fetch_facebook_userinfo(access_token: str) -> dict:
    params = {
        'fields': 'id,name,email,picture.width(240).height(240)',
        'access_token': access_token,
    }
    return _facebook_json_request(f'{_facebook_api_base()}/me?{urlencode(params)}')


def fetch_facebook_pages(access_token: str) -> list[dict]:
    params = {
        'fields': 'id,name,access_token,link,followers_count,fan_count',
        'access_token': access_token,
    }
    data = _facebook_json_request(f'{_facebook_api_base()}/me/accounts?{urlencode(params)}')
    return data.get('data') or []


def refresh_access_token(refresh_token: str) -> dict:
    payload = {
        'client_id': settings.YOUTUBE_CLIENT_ID,
        'client_secret': settings.YOUTUBE_CLIENT_SECRET,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }
    return _json_request(GOOGLE_TOKEN_URL, method='POST', data=payload)


def fetch_userinfo(access_token: str) -> dict:
    headers = {'Authorization': f'Bearer {access_token}'}
    return _json_request(GOOGLE_USERINFO_URL, headers=headers)


def youtube_get(access_token: str, endpoint: str, params: dict) -> dict:
    query = urlencode(params)
    url = f'{endpoint}?{query}'
    headers = {'Authorization': f'Bearer {access_token}'}
    return _json_request(url, headers=headers)


def youtube_post(access_token: str, endpoint: str, params: dict, body: dict) -> dict:
    query = urlencode(params)
    url = f'{endpoint}?{query}'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    payload = json.dumps(body).encode('utf-8')
    return _json_request_raw(url, method='POST', raw_data=payload, headers=headers)


def calculate_token_expiry(expires_in) -> dt.datetime:
    seconds = int(expires_in or 0)
    if seconds <= 0:
        return timezone.now()
    return timezone.now() + dt.timedelta(seconds=max(seconds - 60, 0))


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def ensure_valid_access_token(profile: SubscriberProfile) -> str:
    now = timezone.now()
    if profile.access_token and profile.token_expiry and profile.token_expiry > now:
        return profile.access_token

    if not profile.refresh_token:
        raise YouTubeOAuthError('Refresh token is missing. User must re-connect Google.')

    token_data = refresh_access_token(profile.refresh_token)
    profile.access_token = token_data.get('access_token', '')
    if token_data.get('refresh_token'):
        profile.refresh_token = token_data['refresh_token']
    profile.token_expiry = calculate_token_expiry(token_data.get('expires_in'))
    profile.save(update_fields=['access_token', 'refresh_token', 'token_expiry', 'updated_at'])
    return profile.access_token


def fetch_channel_handles(access_token: str, channel_ids: list[str]) -> dict[str, str]:
    """Fetch handles (custom URLs) for a list of channel IDs in batches."""
    if not channel_ids:
        return {}

    handles = {}
    # YouTube allows up to 50 IDs per request
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        params = {
            'part': 'snippet',
            'id': ','.join(batch),
            'maxResults': 50
        }
        data = youtube_get(access_token, YOUTUBE_CHANNELS_URL, params)
        for item in data.get('items', []):
            handle = item.get('snippet', {}).get('customUrl', '')
            if handle and not handle.startswith('@'):
                handle = f'@{handle}'
            handles[item.get('id')] = handle
    return handles


def fetch_my_subscriptions(access_token: str, limit: int = None) -> list:
    """Fetch the list of channels the authenticated user is subscribed to."""
    subscribers = []
    page_token = None
    
    while True:
        params = {
            'part': 'snippet',
            'mine': 'true',  # Get channels the user is subscribed TO
            'maxResults': min(limit, 50) if limit else 50
        }
        if page_token:
            params['pageToken'] = page_token
            
        data = youtube_get(access_token, YOUTUBE_SUBSCRIPTIONS_URL, params)
        items = data.get('items') or []
        
        for item in items:
            snippet = item.get('snippet', {})
            subscribers.append({
                'title': snippet.get('title'),
                'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url'),
                'channel_id': snippet.get('resourceId', {}).get('channelId'),
                'subscribed_at': snippet.get('publishedAt'),
            })
            
            if limit and len(subscribers) >= limit:
                return subscribers
            
        page_token = data.get('nextPageToken')
        if not page_token:
            break

    # Enrich with handles (requires additional API calls)
    ids = [s['channel_id'] for s in subscribers if s.get('channel_id')]
    handle_map = fetch_channel_handles(access_token, ids)
    for sub in subscribers:
        sub['handle'] = handle_map.get(sub['channel_id'], '')

    return subscribers


def fetch_my_subscribers(access_token: str, limit: int = None) -> list:
    """Fetch public subscribers for the authenticated user's channel."""
    subscribers = []
    page_token = None

    while True:
        params = {
            'part': 'subscriberSnippet',
            'mySubscribers': 'true',
            'maxResults': min(limit, 50) if limit else 50,
        }
        if page_token:
            params['pageToken'] = page_token

        data = youtube_get(access_token, YOUTUBE_SUBSCRIPTIONS_URL, params)
        items = data.get('items') or []

        for item in items:
            snippet = item.get('subscriberSnippet', {})
            thumbnails = snippet.get('thumbnails') or {}
            subscribers.append({
                'title': snippet.get('title') or 'Untitled channel',
                'description': snippet.get('description') or '',
                'thumbnail': (
                    (thumbnails.get('high') or {}).get('url')
                    or (thumbnails.get('medium') or {}).get('url')
                    or (thumbnails.get('default') or {}).get('url')
                    or ''
                ),
                'channel_id': snippet.get('channelId') or '',
            })

            if limit and len(subscribers) >= limit:
                return subscribers

        page_token = data.get('nextPageToken')
        if not page_token:
            break

    # Enrich with handles (requires additional API calls)
    ids = [s['channel_id'] for s in subscribers if s.get('channel_id')]
    handle_map = fetch_channel_handles(access_token, ids)
    for sub in subscribers:
        sub['handle'] = handle_map.get(sub['channel_id'], '')

    return subscribers


def subscribe_to_channel(access_token: str, channel_id: str) -> dict:
    """Subscribe the authenticated user to the target channel."""
    if not channel_id:
        raise YouTubeOAuthError('Channel ID is required to subscribe.')

    body = {
        'snippet': {
            'resourceId': {
                'kind': 'youtube#channel',
                'channelId': channel_id,
            }
        }
    }
    return youtube_post(
        access_token,
        YOUTUBE_SUBSCRIPTIONS_URL,
        {'part': 'snippet'},
        body,
    )


def fetch_authenticated_channel_summary(access_token: str) -> dict:
    """Fetch the authenticated user's primary YouTube channel summary."""
    data = youtube_get(
        access_token,
        YOUTUBE_CHANNELS_URL,
        {'part': 'snippet,statistics', 'mine': 'true', 'maxResults': 1},
    )
    channel = (data.get('items') or [{}])[0]
    snippet = channel.get('snippet') or {}
    statistics = channel.get('statistics') or {}
    thumbnails = snippet.get('thumbnails') or {}
    # YouTube handles are returned in the 'customUrl' field
    handle = snippet.get('customUrl') or snippet.get('handle') or ''
    
    if handle and not handle.startswith('@'):
        handle = f'@{handle}'

    return {
        'channel_id': channel.get('id') or '',
        'channel_title': snippet.get('title') or '',
        'handle': handle,
        'subscriber_count': _safe_int(statistics.get('subscriberCount')),
        'view_count': _safe_int(statistics.get('viewCount')),
        'video_count': _safe_int(statistics.get('videoCount')),
        'thumbnail': (
            (thumbnails.get('high') or {}).get('url')
            or (thumbnails.get('medium') or {}).get('url')
            or (thumbnails.get('default') or {}).get('url')
            or ''
        ),
    }


def scan_profile(profile: SubscriberProfile) -> SubscriberProfile:
    try:
        access_token = ensure_valid_access_token(profile)
        previous_subscriber_count = profile.channel_subscriber_count or 0
        had_previous_scan = bool(profile.last_scan_at)

        # Use the summary helper to avoid duplicate code
        summary = fetch_authenticated_channel_summary(access_token)
        profile.channel_id = summary['channel_id']
        profile.channel_title = summary['channel_title']
        profile.handle = summary['handle']
        profile.channel_avatar = summary['thumbnail']
        profile.channel_total_view_count = summary['view_count']
        profile.channel_video_count = summary['video_count']
        
        # This is the "Total Count" which is always accurate (people following you)
        total_followers = summary['subscriber_count']
        
        subscriber_change = (
            total_followers - previous_subscriber_count
            if had_previous_scan
            else 0
        )

        target_channel_id = settings.YOUTUBE_TARGET_CHANNEL_ID.strip()
        
        # 1. Get total subscription count efficiently
        sub_data = youtube_get(access_token, YOUTUBE_SUBSCRIPTIONS_URL, 
                               {'part': 'snippet', 'mine': 'true', 'maxResults': 1})
        total_subscriptions = sub_data.get('pageInfo', {}).get('totalResults', 0)

        # 2. Check specific target subscription efficiently using forChannelId
        verified = False
        if target_channel_id:
            v_data = youtube_get(access_token, YOUTUBE_SUBSCRIPTIONS_URL, 
                                 {'part': 'snippet', 'mine': 'true', 'forChannelId': target_channel_id})
            verified = len(v_data.get('items', [])) > 0

        profile.target_subscription_verified = verified
        profile.subscribed_channel_count = total_subscriptions
        profile.channel_subscriber_count = total_followers
        profile.subscriber_change_since_last_scan = subscriber_change
        profile.last_scan_at = timezone.now()
        profile.last_scan_status = SubscriberProfile.SCAN_SUCCESS
        profile.last_scan_error = ''
        profile.save()

        return profile
    except Exception as exc:
        profile.last_scan_at = timezone.now()
        profile.last_scan_status = SubscriberProfile.SCAN_FAILED
        profile.last_scan_error = str(exc)
        profile.save(update_fields=['last_scan_at', 'last_scan_status', 'last_scan_error', 'updated_at'])
        raise


def fetch_channel_videos(access_token: str, channel_id: str, limit: int = 20) -> list:
    """
    Fetch videos from a specific YouTube channel.
    
    Args:
        access_token: Valid YouTube API access token
        channel_id: The channel ID to fetch videos from
        limit: Maximum number of videos to fetch
    
    Returns:
        list: List of video dictionaries with metadata
    """
    videos = []
    page_token = None
    
    # First get the channel's uploads playlist ID
    channel_data = youtube_get(access_token, YOUTUBE_CHANNELS_URL, {
        'part': 'contentDetails',
        'id': channel_id,
        'maxResults': 1
    })
    
    if not channel_data.get('items'):
        return videos
    
    uploads_playlist_id = channel_data['items'][0].get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads')
    if not uploads_playlist_id:
        return videos
    
    # Now fetch videos from the uploads playlist
    while len(videos) < limit:
        params = {
            'part': 'snippet,contentDetails,statistics',
            'playlistId': uploads_playlist_id,
            'maxResults': min(50, limit - len(videos))
        }
        if page_token:
            params['pageToken'] = page_token
            
        data = youtube_get(access_token, 'https://www.googleapis.com/youtube/v3/playlistItems', params)
        items = data.get('items', [])
        
        for item in items:
            snippet = item.get('snippet', {})
            content_details = item.get('contentDetails', {})
            statistics = item.get('statistics', {})
            
            video_id = content_details.get('videoId')
            if not video_id:
                continue
                
            videos.append({
                'video_id': video_id,
                'title': snippet.get('title', ''),
                'description': snippet.get('description', ''),
                'thumbnail': (
                    (snippet.get('thumbnails', {}).get('high') or {}).get('url')
                    or (snippet.get('thumbnails', {}).get('medium') or {}).get('url')
                    or (snippet.get('thumbnails', {}).get('default') or {}).get('url')
                    or ''
                ),
                'published_at': snippet.get('publishedAt', ''),
                'duration': content_details.get('duration', ''),
                'view_count': _safe_int(statistics.get('viewCount')),
                'like_count': _safe_int(statistics.get('likeCount')),
                'url': f'https://www.youtube.com/watch?v={video_id}'
            })
            
            if len(videos) >= limit:
                break
                
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    
    return videos


def auto_assign_videos_if_needed(profile: SubscriberProfile, max_tasks: int = 3) -> int:
    """
    Automatically assign videos to a user based on their score if they have few tasks.
    
    Args:
        profile: The SubscriberProfile to assign videos to
        max_tasks: Maximum number of pending tasks a user should have
    
    Returns:
        int: Number of videos assigned
    """
    if not profile.active_status:
        return 0
    
    # Check current pending tasks
    current_pending = VideoWatchTask.objects.filter(
        profile=profile,
        verified_status=False
    ).count()
    
    if current_pending >= max_tasks:
        return 0  # User already has enough tasks
    
    tasks_needed = max_tasks - current_pending
    if tasks_needed <= 0:
        return 0
    
    # Only assign to users with minimum score
    min_score_required = 0
    total_score = profile.video_score + profile.video_score_reserved
    
    if total_score < min_score_required:
        return 0  # User doesn't meet score requirements
    
    # Get available videos that user doesn't already have
    existing_video_ids = set(
        VideoWatchTask.objects.filter(profile=profile)
        .values_list('video_id', flat=True)
    )
    
    available_videos = Video.objects.exclude(id__in=existing_video_ids).order_by('-created_at')
    
    if not available_videos.exists():
        return 0  # No new videos available
    
    assigned_count = 0
    
    # Assign videos based on user's score level
    for video in available_videos[:tasks_needed]:
        # Calculate watch time based on user's score
        base_watch_time = 60  # 1 minute base
        score_bonus = min(total_score // 10, 5)  # Max 5 minutes bonus for high scorers
        min_watch_time = base_watch_time + (score_bonus * 60)
        
        try:
            VideoWatchTask.objects.create(
                profile=profile,
                video=video,
                min_watch_time_seconds=min_watch_time,
            )
            assigned_count += 1
        except Exception:
            # Skip if task already exists (unique constraint)
            continue
    
    return assigned_count


def _get_or_create_video_profile(user) -> VideoProfile:
    video_profile, _ = VideoProfile.objects.get_or_create(user=user)
    return video_profile


def available_video_score(user) -> int:
    """Return the available video score minutes that can be assigned."""
    video_profile = _get_or_create_video_profile(user)
    video_profile.refresh_from_db(fields=['video_score', 'video_score_reserved'])
    return max(int(video_profile.video_score or 0) - int(video_profile.video_score_reserved or 0), 0)


def assign_video_from_source_profile(
    source_profile: SubscriberProfile,
    source_user,
    video: Video,
    target_profiles: list[SubscriberProfile],
    minutes_per_target: int,
    max_targets: int | None = None,
    min_watch_time_seconds: int | None = None,
) -> int:
    """Assign one video to active target users with score-budget reservation."""
    if minutes_per_target <= 0:
        raise ValueError("minutes_per_target must be a positive integer")

    with transaction.atomic():
        source_video_profile = _get_or_create_video_profile(source_user)
        source_video_profile.refresh_from_db(fields=['video_score', 'video_score_reserved'])
        available_minutes = max(int(source_video_profile.video_score or 0) - int(source_video_profile.video_score_reserved or 0), 0)
        if available_minutes <= 0:
            raise ValueError("Source profile has no available video score to assign")

        video_minutes = max(int(math.ceil((int(video.duration_seconds or 0)) / 60.0)), 1)
        reserve_per_target = max(video_minutes, minutes_per_target)

        target_video_profile_by_user = {
            vp.user_id: vp
            for vp in VideoProfile.objects.filter(user_id__in=[p.user_id for p in target_profiles], active_status_for_video=True)
        }
        candidates = [
            p for p in list(target_profiles)
            if p.id != source_profile.id and p.user_id in target_video_profile_by_user
        ]
        if max_targets is not None:
            candidates = candidates[:max_targets]

        assigned_count = 0
        total_reserved = 0
        remaining_minutes = available_minutes

        for target in candidates:
            if remaining_minutes < reserve_per_target:
                break
            # Do not reassign the same video to the same user once a task exists.
            # This also prevents reassigning already-completed video tasks.
            if VideoWatchTask.objects.filter(profile=target, video=video).exists():
                continue

            assigned_seconds = reserve_per_target * 60
            min_watch_seconds = max(int(video.duration_seconds or 0), 1)

            VideoWatchTask.objects.create(
                profile=target,
                video=video,
                source_profile=source_profile,
                assigned_video_score=reserve_per_target,
                assigned_watch_time_seconds=assigned_seconds,
                min_watch_time_seconds=min_watch_seconds,
                status=VideoWatchTask.STATUS_PENDING,
            )
            assigned_count += 1
            total_reserved += reserve_per_target
            remaining_minutes -= reserve_per_target

        if total_reserved > 0:
            source_video_profile.video_score_reserved += total_reserved
            source_video_profile.save(update_fields=['video_score_reserved', 'updated_at'])

        return assigned_count


def _settle_source_assigned_score(watch_task: VideoWatchTask) -> None:
    """Consume source reserved minutes when a target's task is complete."""
    source_profile = watch_task.source_profile
    if not source_profile or watch_task.assigned_video_score <= 0:
        return

    assigned_minutes = watch_task.assigned_video_score
    if assigned_minutes <= 0:
        return

    source_video_profile = _get_or_create_video_profile(source_profile.user)
    source_video_profile.refresh_from_db(fields=['video_score_reserved'])
    if source_video_profile.video_score_reserved >= assigned_minutes:
        source_video_profile.video_score_reserved -= assigned_minutes
        source_video_profile.save(update_fields=['video_score_reserved', 'updated_at'])


def _completion_threshold_seconds(watch_task: VideoWatchTask) -> int:
    """Complete when watched >= 50% of video length; fallback to task minimum if duration unknown."""
    video_length = int(getattr(watch_task.video, "duration_seconds", 0) or 0)
    if video_length > 0:
        return max(video_length // 2, 1)
    return max(int(watch_task.min_watch_time_seconds or 0), 1)


# ============================================================================
# VIDEO WATCH SYSTEM FUNCTIONS
# ============================================================================

def record_watch_event(
    watch_task: VideoWatchTask,
    watch_duration_seconds: int,
    start_position_seconds: int = 0,
    end_position_seconds: int = 0,
    session_id: str = "",
    event_type: str = "heartbeat",
    is_tab_active: bool = True,
    is_player_playing: bool = False,
    is_muted: bool = False,
    playback_rate: float = 1.0,
    seek_count: int = 0,
    pause_count: int = 0,
    client_timestamp_ms: int = 0,
    ip_address: str | None = None,
    user_agent: str = "",
    is_valid: bool = True,
    invalid_reason: str = "",
) -> WatchEvent:
    """
    Record a watch session for a video task.
    
    Args:
        watch_task: The VideoWatchTask to record watch time for
        watch_duration_seconds: Duration of this watch session in seconds
        start_position_seconds: Where the user started watching in the video
        end_position_seconds: Where the user stopped watching
    
    Returns:
        WatchEvent: The created watch event
    """
    with transaction.atomic():
        now = timezone.now()
        hold_cutoff = watch_task.created_at + timedelta(minutes=30)
        release_cutoff = now - timedelta(minutes=10)
        if (
            not watch_task.verified_status
            and watch_task.last_seen_at
            and watch_task.last_seen_at <= release_cutoff
        ):
            watch_task.status = VideoWatchTask.STATUS_RELEASE
        elif (not watch_task.verified_status) and now >= hold_cutoff:
            watch_task.status = VideoWatchTask.STATUS_HOLD
        elif not watch_task.verified_status:
            watch_task.status = VideoWatchTask.STATUS_ACTIVE

        watch_task.last_seen_at = now
        if watch_task.opened_at is None:
            watch_task.opened_at = now

        watch_event = WatchEvent.objects.create(
            watch_task=watch_task,
            profile=watch_task.profile,
            video=watch_task.video,
            watch_duration_seconds=watch_duration_seconds,
            start_position_seconds=start_position_seconds,
            end_position_seconds=end_position_seconds,
            session_id=session_id,
            event_type=event_type,
            is_tab_active=is_tab_active,
            is_player_playing=is_player_playing,
            is_muted=is_muted,
            playback_rate=playback_rate,
            seek_count=seek_count,
            pause_count=pause_count,
            client_timestamp_ms=client_timestamp_ms,
            ip_address=ip_address,
            user_agent=user_agent,
            is_valid=is_valid,
            invalid_reason=invalid_reason,
            session_started_at=now,
            session_ended_at=now,
            is_completed=is_valid and watch_duration_seconds > 0,
        )
        
        # Update task only with validated watch time.
        if is_valid and watch_duration_seconds > 0 and watch_task.status not in (VideoWatchTask.STATUS_HOLD, VideoWatchTask.STATUS_RELEASE):
            watch_task.watch_time_seconds += watch_duration_seconds
        watch_task.last_attempt_at = now
        
        # Check if task is now complete
        completion_threshold = _completion_threshold_seconds(watch_task)
        if watch_task.watch_time_seconds >= completion_threshold and not watch_task.verified_status:
            watch_task.verified_status = True
            watch_task.verified_at = now
            watch_task.status = VideoWatchTask.STATUS_COMPLETE
            # Award score when task is completed
            _award_video_score(watch_task.profile, watch_task)
            _settle_source_assigned_score(watch_task)
        elif watch_task.status not in (VideoWatchTask.STATUS_HOLD, VideoWatchTask.STATUS_RELEASE):
            watch_task.status = VideoWatchTask.STATUS_ACTIVE

        watch_task.save(update_fields=['watch_time_seconds', 'last_attempt_at', 'verified_status', 'verified_at', 'status', 'opened_at', 'last_seen_at'])
        return watch_event


def _award_video_score(profile: SubscriberProfile, watch_task: VideoWatchTask) -> None:
    """
    Award video score when a watch task is verified.
    Score is calculated based on watch time minutes.
    1 minute = 1 score point
    """
    watch_time_minutes = watch_task.watch_time_seconds // 60
    score_to_award = max(watch_time_minutes, 1)  # Minimum 1 point per completed task
    
    video_profile = _get_or_create_video_profile(profile.user)
    video_profile.video_score_reserved += score_to_award
    video_profile.save(update_fields=['video_score_reserved', 'updated_at'])


def update_video_watch_time(watch_task: VideoWatchTask, watch_time_seconds: int) -> VideoWatchTask:
    """
    Update the watch time for a video watch task.
    
    Args:
        watch_task: The VideoWatchTask to update
        watch_time_seconds: Total watch time in seconds
    
    Returns:
        VideoWatchTask: The updated task
    """
    with transaction.atomic():
        now = timezone.now()
        watch_task.watch_time_seconds = watch_time_seconds
        watch_task.last_attempt_at = now
        watch_task.last_seen_at = now
        if watch_task.opened_at is None:
            watch_task.opened_at = now
        hold_cutoff = watch_task.created_at + timedelta(minutes=30)
        if (not watch_task.verified_status) and now >= hold_cutoff:
            watch_task.status = VideoWatchTask.STATUS_HOLD
        elif not watch_task.verified_status:
            watch_task.status = VideoWatchTask.STATUS_ACTIVE
        
        # Auto-verify if minimum watch time is reached
        completion_threshold = _completion_threshold_seconds(watch_task)
        if watch_task.watch_time_seconds >= completion_threshold and not watch_task.verified_status:
            watch_task.verified_status = True
            watch_task.verified_at = now
            watch_task.status = VideoWatchTask.STATUS_COMPLETE
            _award_video_score(watch_task.profile, watch_task)
            _settle_source_assigned_score(watch_task)
        elif watch_task.status != VideoWatchTask.STATUS_HOLD:
            watch_task.status = VideoWatchTask.STATUS_PENDING

        watch_task.save(update_fields=['watch_time_seconds', 'last_attempt_at', 'verified_status', 'verified_at', 'status', 'opened_at', 'last_seen_at', 'updated_at'])
        return watch_task

def verify_video_watch_task(watch_task: VideoWatchTask) -> bool:
    """
    Check and verify if a video watch task meets the minimum watch time requirement.
    
    Args:
        watch_task: The VideoWatchTask to verify
    
    Returns:
        bool: True if task is verified and valid, False otherwise
    """
    watch_task.refresh_from_db()
    
    if watch_task.verified_status:
        return True
    
    if watch_task.watch_time_seconds >= _completion_threshold_seconds(watch_task):
        watch_task.verified_status = True
        watch_task.verified_at = timezone.now()
        watch_task.status = VideoWatchTask.STATUS_COMPLETE
        watch_task.save(update_fields=['verified_status', 'verified_at', 'status', 'updated_at'])
        _award_video_score(watch_task.profile, watch_task)
        _settle_source_assigned_score(watch_task)
        return True
    
    return False


def get_video_watch_progress(watch_task: VideoWatchTask) -> dict:
    """
    Get progress details for a video watch task.
    
    Returns:
        dict: Progress information including percentage, remaining time, and status
    """
    watch_task.refresh_from_db()
    
    progress_percentage = min(
        (watch_task.watch_time_seconds / watch_task.min_watch_time_seconds * 100) if watch_task.min_watch_time_seconds > 0 else 0,
        100
    )
    remaining_seconds = max(watch_task.min_watch_time_seconds - watch_task.watch_time_seconds, 0)
    
    return {
        'watch_time_seconds': watch_task.watch_time_seconds,
        'min_watch_time_seconds': watch_task.min_watch_time_seconds,
        'remaining_seconds': remaining_seconds,
        'progress_percentage': progress_percentage,
        'is_completed': watch_task.verified_status,
        'status': watch_task.status,
        'watch_time_minutes': watch_task.watch_time_seconds // 60,
        'remaining_minutes': remaining_seconds // 60,
    }


def transfer_video_score_to_available(user, amount: int = None) -> int:
    """
    Transfer video_score_reserved to video_score (make it available for use).
    
    Args:
        profile: The SubscriberProfile to transfer score for
        amount: Amount to transfer (None = transfer all reserved score)
    
    Returns:
        int: Amount transferred
    """
    with transaction.atomic():
        video_profile = _get_or_create_video_profile(user)
        video_profile.refresh_from_db(fields=['video_score', 'video_score_reserved'])
        
        transfer_amount = amount or video_profile.video_score_reserved
        transfer_amount = min(transfer_amount, video_profile.video_score_reserved)
        
        video_profile.video_score += transfer_amount
        video_profile.video_score_reserved -= transfer_amount
        video_profile.save(update_fields=['video_score', 'video_score_reserved', 'updated_at'])
        
        return transfer_amount


def use_video_score(user, amount: int) -> bool:
    """
    Use (deduct) available video score from a profile.
    
    Args:
        profile: The SubscriberProfile to deduct score from
        amount: Amount of score to use
    
    Returns:
        bool: True if score was deducted successfully, False if insufficient score
    """
    with transaction.atomic():
        video_profile = _get_or_create_video_profile(user)
        video_profile.refresh_from_db(fields=['video_score'])
        
        if video_profile.video_score < amount:
            return False
        
        video_profile.video_score -= amount
        video_profile.save(update_fields=['video_score', 'updated_at'])
        return True
