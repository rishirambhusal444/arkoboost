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
    path("profile", views.profile_page),
    path("profile/facebook/", views.update_facebook_profile, name="update_facebook_profile"),
    path("tasks/", views.user_tasks, name="tasks"),
    path("tasks/youtube/", views.user_tasks, {"task_mode": "youtube"}, name="youtube_tasks"),
    path("tasks/youtube/enter/", views.enter_youtube_tasks, name="enter_youtube_tasks"),
    path("tasks/facebook/", views.user_tasks, {"task_mode": "facebook"}, name="facebook_tasks"),
    path("tasks/subscribe/", views.subscribe_assigned_channel, name="subscribe_assigned_channel"),
    path("tasks/subscribe-top-user/", views.subscribe_top_user_channel, name="subscribe_top_user_channel"),
    path("tasks/facebook-followed/", views.mark_facebook_followed, name="mark_facebook_followed"),
    path("scan/", views.scan_now, name="scan_now"),
    path("my-subscribers/", views.list_subscribers, name="list_subscribers"), # This will now show actual subscribers
    path("my-subscriptions/", views.list_subscriptions, name="list_subscriptions"), # This will now show channels the user follows
    path("auth/google/start/", views.google_connect, name="google_connect"),
    path("auth/google/callback/", views.google_callback, name="google_callback"),
    path("auth/facebook/start/", views.facebook_connect, name="facebook_connect"),
    path("auth/facebook/callback/", views.facebook_callback, name="facebook_callback"),
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
