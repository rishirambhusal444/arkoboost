from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0064_adminvideo_file_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminvideo",
            name="manual_profile_video_url",
            field=models.URLField(blank=True),
        ),
    ]
