from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, UserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.utils import timezone


class User(AbstractBaseUser, PermissionsMixin):
    username_validator = UnicodeUsernameValidator()
    ACCOUNT_MODE_MANUAL = "manual"
    ACCOUNT_MODE_GOOGLE = "google"
    ACCOUNT_MODE_CHOICES = [
        (ACCOUNT_MODE_MANUAL, "Manual"),
        (ACCOUNT_MODE_GOOGLE, "Google"),
    ]

    username = models.CharField(
        max_length=150,
        unique=True,
        help_text="Required. 150 characters or fewer.",
        validators=[username_validator],
        error_messages={"unique": "A user with that username already exists."},
    )
    email = models.EmailField(blank=True)
    handle = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    account_mode = models.CharField(
        max_length=16,
        choices=ACCOUNT_MODE_CHOICES,
        default=ACCOUNT_MODE_MANUAL,
        db_index=True,
    )
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "user_table"

    def clean(self):
        super().clean()
        self.email = type(self).objects.normalize_email(self.email)

    def get_full_name(self):
        return self.handle or self.username

    def get_short_name(self):
        return self.handle or self.username

    def __str__(self):
        return self.handle or self.username


class SubscriberProfile(models.Model):
    CATEGORY_EDUCATION = "education"
    CATEGORY_ENTERTAINMENT = "entertainment"
    CATEGORY_SPORTS = "sports"
    CATEGORY_TECHNOLOGY = "technology"
    CATEGORY_MUSIC = "music"
    CATEGORY_GAMING = "gaming"
    CATEGORY_LIFESTYLE = "lifestyle"
    CATEGORY_NEWS = "news"
    CATEGORY_OTHER = "other"
    CATEGORY_CHOICES = [
        (CATEGORY_EDUCATION, "Education"),
        (CATEGORY_ENTERTAINMENT, "Entertainment"),
        (CATEGORY_SPORTS, "Sports"),
        (CATEGORY_TECHNOLOGY, "Technology"),
        (CATEGORY_MUSIC, "Music"),
        (CATEGORY_GAMING, "Gaming"),
        (CATEGORY_LIFESTYLE, "Lifestyle"),
        (CATEGORY_NEWS, "News"),
        (CATEGORY_OTHER, "Other"),
    ]

    SCAN_PENDING = "pending"
    SCAN_SUCCESS = "success"
    SCAN_FAILED = "failed"
    SCAN_STATUS_CHOICES = [
        (SCAN_PENDING, "Pending"),
        (SCAN_SUCCESS, "Success"),
        (SCAN_FAILED, "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriber_profiles",
    )
    google_subject_id = models.CharField(max_length=128, unique=True, blank=True, null=True)
    google_email = models.EmailField(blank=True)
    channel_id = models.CharField(max_length=128, blank=True)
    channel_title = models.CharField(max_length=255, blank=True)
    handle = models.CharField(max_length=255, blank=True, db_index=True)
    channel_avatar = models.URLField(blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    target_subscription_verified = models.BooleanField(default=False)
    subscribed_channel_count = models.PositiveIntegerField(default=0)
    channel_subscriber_count = models.PositiveIntegerField(default=0)
    channel_total_view_count = models.PositiveBigIntegerField(default=0)
    channel_video_count = models.PositiveIntegerField(default=0)
    facebook_profile_url = models.URLField(blank=True)
    facebook_followers_count = models.PositiveIntegerField(default=0)
    category = models.CharField(
        max_length=32,
        choices=CATEGORY_CHOICES,
        default=CATEGORY_OTHER,
        db_index=True,
    )
    subscriber_change_since_last_scan = models.IntegerField(default=0)
    required_channel_total = models.PositiveIntegerField(default=0)
    required_channel_verified_count = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=0)
    reserved_score = models.PositiveIntegerField(default=0)
    video_score = models.PositiveIntegerField(default=0)
    video_score_reserved = models.PositiveIntegerField(default=0)
    manual_video_url_1 = models.URLField(blank=True)
    manual_video_url_2 = models.URLField(blank=True)
    manual_video_url_3 = models.URLField(blank=True)
    active_status = models.BooleanField(default=False, db_index=True)
    active_status_for_video = models.BooleanField(default=False, db_index=True)
    active_status_for_youtube = models.BooleanField(default=False, db_index=True)
    last_verified_sync_at = models.DateTimeField(null=True, blank=True)
    last_tasks_entry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    subscribed_list = models.JSONField(default=list, blank=True)
    last_scan_at = models.DateTimeField(null=True, blank=True)
    last_scan_status = models.CharField(
        max_length=16,
        choices=SCAN_STATUS_CHOICES,
        default=SCAN_PENDING,
    )
    last_scan_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta: 
        db_table = "google_subscribe_profile" 
        ordering = ["-updated_at"] 
        indexes = [
            models.Index(
                fields=["active_status", "last_tasks_entry_at"],
                name="subprof_active_last_idx",
            ),
            models.Index(
                fields=["active_status", "score"],
                name="subprof_active_score_idx",
            ),
        ]

    def __str__(self):
        return f'{self.handle or self.user.username} ({self.channel_title or "No channel"})'


class FacebookProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="facebook_profile",
    )
    facebook_subject_id = models.CharField(max_length=128, unique=True, blank=True, null=True)
    facebook_email = models.EmailField(blank=True)
    name = models.CharField(max_length=255, blank=True)
    profile_url = models.URLField(blank=True)
    profile_picture_url = models.URLField(blank=True)
    access_token = models.TextField(blank=True)
    page_id = models.CharField(max_length=128, blank=True, db_index=True)
    page_name = models.CharField(max_length=255, blank=True)
    page_url = models.URLField(blank=True)
    page_access_token = models.TextField(blank=True)
    page_followers_count = models.PositiveIntegerField(default=0)
    token_expiry = models.DateTimeField(null=True, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "facebook_profile_table"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name or self.facebook_email or self.user.username


class FacebookTaskAssing(models.Model):
    profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        related_name="facebook_task_assignments",
    )
    target_facebook_profile = models.ForeignKey(
        FacebookProfile,
        on_delete=models.CASCADE,
        related_name="assigned_facebook_tasks",
    )
    followed_status = models.BooleanField(default=False, db_index=True)
    followed_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "facebook_task_assing"
        ordering = ["-updated_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "target_facebook_profile"],
                name="unique_facebook_task_assing_pair",
            )
        ]

    def __str__(self):
        state = "followed" if self.followed_status else "pending"
        owner = self.profile.handle or self.profile.user.username
        target = self.target_facebook_profile.name or self.target_facebook_profile.user.username
        return f"{owner} -> {target} ({state})"


class TopUserSubscribeTask(models.Model):
    profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        related_name="top_user_subscribe_tasks",
    )
    target_profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        related_name="top_user_subscribe_targets",
    )
    verified_status = models.BooleanField(default=False, db_index=True)
    subscribed_at = models.DateTimeField(null=True, blank=True)
    facebook_followed_status = models.BooleanField(default=False, db_index=True)
    facebook_followed_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(auto_now=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta: 
        ordering = ["-updated_at", "-created_at"] 
        indexes = [
            models.Index(
                fields=["profile", "target_profile", "verified_status"],
                name="top_task_pair_ver_idx",
            ),
            models.Index(
                fields=["verified_status", "profile"],
                name="top_task_state_owner_idx",
            ),
        ]
        constraints = [ 
            models.UniqueConstraint( 
                fields=["profile", "target_profile"], 
                name="unique_top_user_subscribe_task", 
            ) 
        ]

    def __str__(self):
        state = "verified" if self.verified_status else "not_verified"
        profile_name = self.profile.handle or self.profile.user.username
        target_name = self.target_profile.handle or self.target_profile.user.username
        return f"{profile_name} -> {target_name} ({state})"


class ManualSubscribeProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="manual_subscribe_profile_user",
        null=True,
        blank=True,
    )
    handle = models.CharField(max_length=255, blank=True, db_index=True)
    category = models.CharField(
        max_length=32,
        choices=SubscriberProfile.CATEGORY_CHOICES,
        default=SubscriberProfile.CATEGORY_OTHER,
        db_index=True,
    ) 
    last_tasks_entry_at = models.DateTimeField(null=True, blank=True, db_index=True) 
    sub_score = models.PositiveIntegerField(default=0) 
    total_verified = models.PositiveIntegerField(default=0)
    loyal_score = models.PositiveIntegerField(default=0) 
    active_status_for_subscribe = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta: 
        db_table = "manual_subscribe_profile" 
        ordering = ["-updated_at"] 
        indexes = [
            models.Index(
                fields=["active_status_for_subscribe", "last_tasks_entry_at"],
                name="manprof_active_last_idx",
            ),
            models.Index(
                fields=["active_status_for_subscribe", "sub_score"],
                name="manprof_active_score_idx",
            ),
        ]

    def __str__(self):
        if self.handle:
            return self.handle
        if self.user:
            return self.user.username
        return "ManualSubscribeProfile"

class ManualSubscribeTaskAssign(models.Model): 
    STATUS_ASSIGNED = "assigned"
    STATUS_UNVERIFIED = "unverified" 
    STATUS_VERIFIED = "verified" 
    STATUS_RELEASED = "released"
    STATUS_CHOICES = [ 
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_UNVERIFIED, "Unverified"), 
        (STATUS_VERIFIED, "Verified"), 
        (STATUS_RELEASED, "Released"),
    ] 

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="manual_subscribe_task_assignments",
    )
    manual_subscribe_profile = models.ForeignKey(
        ManualSubscribeProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_assignments",
    )
    target_profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        related_name="manual_subscribe_task_targets",
    )
    subscribed_status = models.CharField( 
        max_length=16, 
        choices=STATUS_CHOICES, 
        default=STATUS_ASSIGNED,
        db_index=True, 
    ) 
    active_status = models.BooleanField(default=False, db_index=True)
    clicked_subscribe_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta: 
        db_table = "manual_subscribe_task_assign" 
        ordering = ["-updated_at", "-created_at"] 
        indexes = [
            models.Index(
                fields=["user", "target_profile", "subscribed_status"],
                name="man_task_pair_state_idx",
            ),
            models.Index(
                fields=["subscribed_status", "user"],
                name="man_task_state_owner_idx",
            ),
        ]
        constraints = [ 
            models.UniqueConstraint( 
                fields=["user", "target_profile"], 
                name="unique_manual_subscribe_task_assign", 
            ) 
        ]

    def __str__(self):
        target = self.target_profile.handle or self.target_profile.user.username
        return f"{self.user.username} -> {target} ({self.subscribed_status})"


class ManualFacebookProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="manual_facebook_profile",
    )
    page_name = models.CharField(max_length=255, blank=True, db_index=True)
    profile_url = models.URLField(blank=True)
    follow_score = models.PositiveIntegerField(default=0)
    total_verified = models.PositiveIntegerField(default=0)
    loyal_score = models.PositiveIntegerField(default=0)
    active_status_for_follow = models.BooleanField(default=False, db_index=True)
    last_tasks_entry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "manual_facebook_profile"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["active_status_for_follow", "last_tasks_entry_at"],
                name="manfb_active_last_idx",
            ),
            models.Index(
                fields=["active_status_for_follow", "follow_score"],
                name="manfb_active_score_idx",
            ),
        ]

    def __str__(self):
        return self.page_name or self.profile_url or self.user.username


class ManualFacebookFollowTaskAssign(models.Model):
    STATUS_ASSIGNED = "assigned"
    STATUS_UNVERIFIED = "unverified"
    STATUS_VERIFIED = "verified"
    STATUS_RELEASED = "released"
    STATUS_CHOICES = [
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_UNVERIFIED, "Unverified"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_RELEASED, "Released"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="manual_facebook_follow_task_assignments",
    )
    manual_facebook_profile = models.ForeignKey(
        ManualFacebookProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="follow_task_assignments",
    )
    target_profile = models.ForeignKey(
        ManualFacebookProfile,
        on_delete=models.CASCADE,
        related_name="target_follow_task_assignments",
    )
    followed_status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ASSIGNED,
        db_index=True,
    )
    active_status = models.BooleanField(default=False, db_index=True)
    clicked_follow_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "manual_facebook_follow_task_assign"
        ordering = ["-updated_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["user", "target_profile", "followed_status"],
                name="manfb_task_pair_state_idx",
            ),
            models.Index(
                fields=["followed_status", "user"],
                name="manfb_task_state_owner_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "target_profile"],
                name="unique_manual_facebook_follow_task",
            )
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.target_profile} ({self.followed_status})"


class VerificationImage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="verification_images",
    )
    image = models.FileField(upload_to="verification_images/")
    scanned_status = models.BooleanField(default=False, db_index=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    extracted_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "varificatio_image"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} verification image {self.id}"


class VideoProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_profile",
    )
    video_score = models.PositiveIntegerField(default=0)
    video_score_reserved = models.PositiveIntegerField(default=0)
    active_status_for_video = models.BooleanField(default=False, db_index=True)
    active_status_for_youtube = models.BooleanField(default=False, db_index=True)
    last_video_entry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "video_profile_table"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.user.username


class Video(models.Model):
    """Minimal video record for watch tracking."""
    STATUS_PENDING = "pending"
    STATUS_HOLD = "hold"
    STATUS_RELEASE = "release"
    STATUS_COMPLETE = "complete"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_HOLD, "Hold"),
        (STATUS_RELEASE, "Release"),
        (STATUS_COMPLETE, "Complete"),
    ]

    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_videos",
    )
    duration_seconds = models.PositiveIntegerField(default=0)
    watched_time_seconds = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    video_url = models.URLField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "video_table"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.video_url


class VideoWatchTask(models.Model):
    """Similar to TopUserSubscribeTask but for video watching"""
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_HOLD = "hold"
    STATUS_RELEASE = "release"
    STATUS_COMPLETE = "complete"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_HOLD, "Hold"),
        (STATUS_RELEASE, "Release"),
        (STATUS_COMPLETE, "Complete"),
    ]

    profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        related_name="video_watch_tasks",
    )
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="watch_task_assignments",
    )
    source_profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assigned_video_tasks",
    )
    assigned_video_score = models.PositiveIntegerField(default=0)
    assigned_watch_time_seconds = models.PositiveIntegerField(default=0)
    min_watch_time_seconds = models.PositiveIntegerField(default=60)  # Minimum watch time to complete task
    watch_time_seconds = models.PositiveIntegerField(default=0)  # Total watch time in seconds
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    verified_status = models.BooleanField(default=False, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(auto_now=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "video_watch_task_table"
        ordering = ["-updated_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "video"],
                name="unique_video_watch_task",
            )
        ]


    def __str__(self):
        profile_name = self.profile.handle or self.profile.user.username
        return f"{profile_name} watched {self.video.video_url} for {self.watch_duration_seconds}s"
  
    @property
    def assigned_minutes(self):
        return self.assigned_video_score


class WatchEvent(models.Model):
    """Track individual watch sessions and accumulate watch time"""
    watch_task = models.ForeignKey(
        VideoWatchTask,
        on_delete=models.CASCADE,
        related_name="watch_events",
    )
    profile = models.ForeignKey(
        SubscriberProfile,
        on_delete=models.CASCADE,
        related_name="watch_events",
    )
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="watch_events",
    )
    watch_duration_seconds = models.PositiveIntegerField(default=0)  # Duration of this session
    start_position_seconds = models.PositiveIntegerField(default=0)  # Where user started watching
    end_position_seconds = models.PositiveIntegerField(default=0)  # Where user stopped
    session_id = models.CharField(max_length=64, blank=True, db_index=True)
    event_type = models.CharField(max_length=24, default="heartbeat", db_index=True)
    is_tab_active = models.BooleanField(default=True)
    is_player_playing = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)
    playback_rate = models.FloatField(default=1.0)
    seek_count = models.PositiveIntegerField(default=0)
    pause_count = models.PositiveIntegerField(default=0)
    client_timestamp_ms = models.BigIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    is_valid = models.BooleanField(default=True, db_index=True)
    invalid_reason = models.CharField(max_length=128, blank=True)
    session_started_at = models.DateTimeField()
    session_ended_at = models.DateTimeField(null=True, blank=True)
    is_completed = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "watch_event_table"
        ordering = ["-created_at"]

    def __str__(self):
        profile_name = self.profile.handle or self.profile.user.username
        return f"{profile_name} watched {self.video.title} for {self.watch_duration_seconds}s"
    ACCOUNT_MODE_MANUAL = "manual"
    ACCOUNT_MODE_GOOGLE = "google"
    ACCOUNT_MODE_CHOICES = [
        (ACCOUNT_MODE_MANUAL, "Manual"),
        (ACCOUNT_MODE_GOOGLE, "Google"),
    ]
