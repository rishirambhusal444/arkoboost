from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0066_adminvideo_manual_profile_video_file"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="adminvideo",
            name="home_video_url",
        ),
        migrations.RemoveField(
            model_name="adminvideo",
            name="manual_profile_video_url",
        ),
        migrations.RemoveField(
            model_name="adminvideo",
            name="task_video_url_facebook",
        ),
        migrations.RemoveField(
            model_name="adminvideo",
            name="task_video_url_facebook_verify",
        ),
        migrations.RemoveField(
            model_name="adminvideo",
            name="task_video_url_subscribe",
        ),
        migrations.RemoveField(
            model_name="adminvideo",
            name="task_video_url_subscribe_verify",
        ),
    ]
