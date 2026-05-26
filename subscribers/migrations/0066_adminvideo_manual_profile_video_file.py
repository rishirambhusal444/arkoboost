from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0065_adminvideo_manual_profile_video_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminvideo",
            name="manual_profile_video_file",
            field=models.FileField(blank=True, null=True, upload_to="admin_videos/"),
        ),
    ]
