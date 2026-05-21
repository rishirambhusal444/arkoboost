from django.urls import path

from . import views

app_name = "subscribers"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_page, name="login"),
    path("signup/", views.signup, name="signup"),
    path("logout/", views.sign_out, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("profile/", views.profile_page, name="profile"),
    path("profile/google/", views.profile_page, {"profile_mode": "google"}, name="profile_google"),
    path("profile/manual/", views.profile_page, {"profile_mode": "manual"}, name="profile_manual"),
    path("profile/update-youtube-handle/", views.update_youtube_handle, name="update_youtube_handle"),
    path("profile/validate-youtube-handle/", views.validate_youtube_handle, name="validate_youtube_handle"),
    path("profile", views.profile_page),
    path("profile/facebook/", views.update_facebook_profile, name="update_facebook_profile"),
    path("profile/admin-videos/", views.update_admin_videos, name="update_admin_videos"),
    path("tasks/", views.user_tasks, name="tasks"),
    path("tasks/youtube/", views.user_tasks, {"task_mode": "youtube"}, name="youtube_tasks"),
    path("tasks/youtube/manual/", views.manual_youtube_tasks, name="youtube_tasks_manual"),
    path("tasks/youtube/enter/", views.enter_youtube_tasks, name="enter_youtube_tasks"),
    path("tasks/youtube/manual/enter/", views.enter_youtube_tasks_manual, name="enter_youtube_tasks_manual"),
    # Cleaner subscribe-task aliases
    path("tasks/subscribe/", views.user_tasks, {"task_mode": "youtube"}, name="subscribe_tasks"),
    path("tasks/subscribe/manual/", views.manual_youtube_tasks, name="subscribe_tasks_manual"),
    path("tasks/subscribe/handle-insert/", views.enter_youtube_tasks, name="handle_insert_page"),
    path("tasks/subscribe/manual/handle-insert/", views.enter_youtube_tasks_manual, name="handle_insert_page_manual"),
    path("tasks/subscribe/enter/", views.enter_youtube_tasks, name="subscribe_tasks_enter"),
    path("tasks/subscribe/manual/enter/", views.enter_youtube_tasks_manual, name="subscribe_tasks_manual_enter"),
    path("tasks/facebook/", views.user_tasks, {"task_mode": "facebook"}, name="facebook_tasks"),
    path("tasks/facebook/manual/", views.manual_facebook_tasks, name="facebook_tasks_manual"),
    path("tasks/facebook/manual/enter/", views.enter_facebook_tasks_manual, name="facebook_tasks_manual_enter"),
    path("tasks/facebook/manual/follow/", views.manual_facebook_follow_task_assign, name="manual_facebook_follow_task_assign"),
    path("tasks/facebook/manual/make-verify/", views.make_facebook_verify_from_image, name="make_facebook_verify_from_image"),
    path("tasks/subscribe/assigned/", views.subscribe_assigned_channel, name="subscribe_assigned_channel"),
    path("tasks/subscribe-top-user/", views.subscribe_top_user_channel, name="subscribe_top_user_channel"),
    path("tasks/youtube/manual/make-verify/", views.make_verify_from_images, name="make_verify_from_images"),
    path("tasks/youtube/manual/make-verify-api/", views.make_verify_from_images, name="make_verify_from_images_api"),
    path("tasks/manual-subscribe-task-assign/", views.manual_subscribe_task_assign, name="manual_subscribe_task_assign"),
    path("tasks/facebook-followed/", views.mark_facebook_followed, name="mark_facebook_followed"),
    path("scan/", views.scan_now, name="scan_now"),
    path("my-subscribers/", views.list_subscribers, name="list_subscribers"), # This will now show actual subscribers
    path("my-subscriptions/", views.list_subscriptions, name="list_subscriptions"), # This will now show channels the user follows
    path("auth/google/start/", views.google_connect, name="google_connect"),
    path("auth/google/callback/", views.google_callback, name="google_callback"),
    path("auth/facebook/start/", views.facebook_connect, name="facebook_connect"),
    path("auth/facebook/callback/", views.facebook_callback, name="facebook_callback"),
    path("logfile/", views.logfile_page, name="logfile"),
    # Video watch system URLs
    path("videos/watch/", views.watch_video_root, name="watch_video_root"),
    path("videos/watch/enter/", views.enter_watch_tasks, name="enter_watch_tasks"),
    path("videos/watch/manage-link/", views.save_watch_video_link, name="save_watch_video_link"),
    path("videos/watch/<int:task_id>/", views.watch_video, name="watch_video"),
    path("videos/save-watch-time/", views.save_watch_time, name="save_watch_time"),
    path("videos/watch/<int:task_id>/start-session/", views.start_watch_session, name="start_watch_session"),
    path("videos/watch/<int:task_id>/update-time/", views.update_watch_time, name="update_watch_time"),
    path("videos/watch/<int:task_id>/complete/", views.complete_watch_task, name="complete_watch_task"),
]
