from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0036_profile_video_activity_and_task_presence_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="owner_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="owned_videos",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="video",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("hold", "Hold"),
                    ("release", "Release"),
                    ("complete", "Complete"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="video",
            name="watched_time_seconds",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
