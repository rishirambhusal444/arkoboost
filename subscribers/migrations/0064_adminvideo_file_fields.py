from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0063_adminvideo"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminvideo",
            name="home_video_file",
            field=models.FileField(blank=True, null=True, upload_to="admin_videos/"),
        ),
        migrations.AddField(
            model_name="adminvideo",
            name="task_video_file_facebook",
            field=models.FileField(blank=True, null=True, upload_to="admin_videos/"),
        ),
        migrations.AddField(
            model_name="adminvideo",
            name="task_video_file_facebook_verify",
            field=models.FileField(blank=True, null=True, upload_to="admin_videos/"),
        ),
        migrations.AddField(
            model_name="adminvideo",
            name="task_video_file_subscribe",
            field=models.FileField(blank=True, null=True, upload_to="admin_videos/"),
        ),
    ]
