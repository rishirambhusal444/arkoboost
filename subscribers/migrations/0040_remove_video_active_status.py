from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0039_video_active_status"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="video",
            name="active_status",
        ),
    ]
