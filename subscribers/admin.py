from django.contrib import admin
from django import forms
from django.contrib import messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

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
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("YouTube", {"fields": ("handle", "email", "account_mode")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "handle", "account_mode", "password1", "password2"),
            },
        ),
    )
    list_display = ("username", "handle", "email", "account_mode", "is_staff")
    search_fields = ("username", "handle", "email", "account_mode")
    list_filter = ("account_mode", "is_staff", "is_active")
    ordering = ("username",)


@admin.register(SubscriberProfile)
class SubscriberProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user_handle",
        "channel_title",
        "handle",
        "category",
        "channel_subscriber_count",
        "facebook_followers_count",
        "score",
        "reserved_score",
        "video_score",
        "video_score_reserved",
        "active_status_for_video",
        "active_status_for_youtube",
        "last_scan_at",
        "last_scan_status",
    )
    fieldsets = (
        ("Basic Info", {
            "fields": ("user", "channel_title", "handle", "category", "google_email", "facebook_profile_url")
        }),
        ("Channel Stats", {
            "fields": ("channel_subscriber_count", "channel_video_count", "channel_total_view_count", "facebook_followers_count")
        }),
        ("Scores", {
            "fields": ("score", "reserved_score", "video_score", "video_score_reserved")
        }),
        ("Status", {
            "fields": (
                "last_scan_at",
                "last_scan_status",
                "target_subscription_verified",
                "active_status",
                "active_status_for_video",
                "active_status_for_youtube",
                "last_verified_sync_at",
                "last_tasks_entry_at",
            )
        }),
    )
    search_fields = ("user__email", "channel_title", "handle", "google_email", "facebook_profile_url")
    list_filter = ("last_scan_status", "target_subscription_verified", "category")
    readonly_fields = ("last_scan_at", "updated_at")
    actions = ["delete_selected_users_and_profiles"]

    @admin.display(description="User")
    def user_handle(self, obj):
        return obj.handle or obj.user.username

    @admin.action(description="Delete selected profiles and their associated User accounts")
    def delete_selected_users_and_profiles(self, request, queryset):
        user_count = 0
        for profile in queryset:
            if profile.user:
                profile.user.delete()
                user_count += 1

        self.message_user(
            request,
            f"Successfully deleted {user_count} user accounts and their associated profiles.",
            messages.SUCCESS,
        )


@admin.register(TopUserSubscribeTask)
class TopUserSubscribeTaskAdmin(admin.ModelAdmin):
    list_display = (
        "profile_handle",
        "target_profile_handle",
        "verified_status",
        "subscribed_at",
        "facebook_followed_status",
        "facebook_followed_at",
        "last_attempt_at",
        "updated_at",
    )
    list_filter = ("verified_status", "facebook_followed_status", "subscribed_at", "updated_at")
    search_fields = (
        "profile__user__username",
        "profile__user__email",
        "target_profile__user__username",
        "target_profile__user__email",
    )
    readonly_fields = ("created_at", "updated_at", "last_attempt_at")

    @admin.display(description="Profile")
    def profile_handle(self, obj):
        return obj.profile.handle or obj.profile.user.username

    @admin.display(description="Target")
    def target_profile_handle(self, obj):
        return obj.target_profile.handle or obj.target_profile.user.username


@admin.register(FacebookProfile)
class FacebookProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user_handle",
        "name",
        "facebook_email",
        "page_name",
        "page_followers_count",
        "facebook_subject_id",
        "connected_at",
        "updated_at",
    )
    search_fields = ("user__username", "user__email", "name", "facebook_email", "facebook_subject_id", "page_name", "page_id")
    readonly_fields = ("connected_at", "updated_at")

    @admin.display(description="User")
    def user_handle(self, obj):
        return obj.user.handle or obj.user.username


@admin.register(FacebookTaskAssing)
class FacebookTaskAssingAdmin(admin.ModelAdmin):
    list_display = (
        "profile_handle",
        "target_facebook_name",
        "followed_status",
        "followed_at",
        "last_attempt_at",
        "updated_at",
    )
    list_filter = ("followed_status", "updated_at")
    search_fields = (
        "profile__user__username",
        "profile__user__email",
        "target_facebook_profile__name",
        "target_facebook_profile__facebook_email",
        "target_facebook_profile__facebook_subject_id",
    )
    readonly_fields = ("created_at", "updated_at", "last_attempt_at")

    @admin.display(description="Profile")
    def profile_handle(self, obj):
        return obj.profile.handle or obj.profile.user.username

    @admin.display(description="Target Facebook")
    def target_facebook_name(self, obj):
        return obj.target_facebook_profile.name or obj.target_facebook_profile.user.username


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner_user",
        "video_url",
        "duration_seconds",
        "watched_time_seconds",
        "status",
        "created_at",
    )
    search_fields = (
        "video_url",
        "owner_user__username",
        "owner_user__email",
    )
    list_filter = ("status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("owner_user",)


@admin.register(ManualSubscribeProfile)
class ManualSubscribeProfileAdmin(admin.ModelAdmin):
    list_display = ( 
        "profile_handle", 
        "handle", 
        "sub_score", 
        "total_verified",
        "loyal_score", 
        "active_status_for_subscribe", 
        "updated_at", 
    ) 
    list_filter = ("active_status_for_subscribe", "updated_at")
    search_fields = (
        "handle",
        "user__username",
        "user__email",
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Profile")
    def profile_handle(self, obj):
        return obj.handle or obj.user.username


@admin.register(ManualSubscribeTaskAssign)
class ManualSubscribeTaskAssignAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "manual_subscribe_profile",
        "target_profile_handle",
        "subscribed_status",
        "active_status",
        "clicked_subscribe_at",
        "updated_at",
    )
    list_filter = ("subscribed_status", "active_status", "updated_at")
    search_fields = (
        "user__username",
        "user__email",
        "target_profile__user__username",
        "target_profile__handle",
    )
    readonly_fields = ("created_at", "updated_at", "clicked_subscribe_at")
    actions = ("mark_verified", "mark_unverified")

    @admin.display(description="Target")
    def target_profile_handle(self, obj):
        return obj.target_profile.handle or obj.target_profile.user.username

    @admin.action(description="Mark selected as verified")
    def mark_verified(self, request, queryset):
        updated = queryset.update(subscribed_status=ManualSubscribeTaskAssign.STATUS_VERIFIED)
        self.message_user(request, f"{updated} row(s) marked as verified.", messages.SUCCESS)

    @admin.action(description="Mark selected as unverified")
    def mark_unverified(self, request, queryset):
        updated = queryset.update(subscribed_status=ManualSubscribeTaskAssign.STATUS_UNVERIFIED)
        self.message_user(request, f"{updated} row(s) marked as unverified.", messages.WARNING)


@admin.register(ManualFacebookProfile)
class ManualFacebookProfileAdmin(admin.ModelAdmin):
    list_display = (
        "profile_name",
        "profile_url",
        "follow_score",
        "total_verified",
        "loyal_score",
        "active_status_for_follow",
        "updated_at",
    )
    list_filter = ("active_status_for_follow", "updated_at")
    search_fields = ("page_name", "profile_url", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Profile")
    def profile_name(self, obj):
        return obj.page_name or obj.user.username


@admin.register(ManualFacebookFollowTaskAssign)
class ManualFacebookFollowTaskAssignAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "manual_facebook_profile",
        "target_profile",
        "followed_status",
        "active_status",
        "clicked_follow_at",
        "updated_at",
    )
    list_filter = ("followed_status", "active_status", "updated_at")
    search_fields = (
        "user__username",
        "user__email",
        "target_profile__page_name",
        "target_profile__profile_url",
    )
    readonly_fields = ("created_at", "updated_at", "clicked_follow_at")
    actions = ("mark_verified", "mark_unverified")

    @admin.action(description="Mark selected as verified")
    def mark_verified(self, request, queryset):
        updated = queryset.update(followed_status=ManualFacebookFollowTaskAssign.STATUS_VERIFIED)
        self.message_user(request, f"{updated} row(s) marked as verified.", messages.SUCCESS)

    @admin.action(description="Mark selected as unverified")
    def mark_unverified(self, request, queryset):
        updated = queryset.update(followed_status=ManualFacebookFollowTaskAssign.STATUS_UNVERIFIED)
        self.message_user(request, f"{updated} row(s) marked as unverified.", messages.WARNING)


@admin.register(VerificationImage)
class VerificationImageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "image", "scanned_status", "scanned_at", "created_at")
    search_fields = ("user__username", "user__email", "image")
    list_filter = ("scanned_status", "created_at")
    readonly_fields = ("created_at", "updated_at")


class AdminVideoForm(forms.ModelForm):
    class Meta:
        model = AdminVideo
        fields = "__all__"
        labels = {
            "home_video_file": "Home Video File",
            "task_video_file_subscribe": "YouTube Guide Video File",
            "manual_profile_video_file": "Profile Guide Video File",
            "task_video_file_facebook": "Facebook Guide Video File",
            "task_video_file_facebook_verify": "Facebook Verify Guide Video File",
        }


@admin.register(AdminVideo)
class AdminVideoAdmin(admin.ModelAdmin):
    form = AdminVideoForm
    list_display = (
        "id",
        "home_video_file",
        "task_video_file_subscribe",
        "manual_profile_video_file",
        "task_video_file_facebook",
        "task_video_file_facebook_verify",
        "updated_at",
    )
    fields = (
        "home_video_file",
        "task_video_file_subscribe",
        "manual_profile_video_file",
        "task_video_file_facebook",
        "task_video_file_facebook_verify",
        "updated_at",
    )
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not AdminVideo.objects.exists()
