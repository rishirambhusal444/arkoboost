from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_video_profile(apps, schema_editor):
    SubscriberProfile = apps.get_model("subscribers", "SubscriberProfile")
    VideoProfile = apps.get_model("subscribers", "VideoProfile")
    for profile in SubscriberProfile.objects.select_related("user").all():
        VideoProfile.objects.update_or_create(
            user_id=profile.user_id,
            defaults={
                "video_score": int(profile.video_score or 0),
                "video_score_reserved": int(profile.video_score_reserved or 0),
                "active_status_for_video": bool(profile.active_status_for_video),
                "active_status_for_youtube": bool(profile.active_status_for_youtube),
                "last_video_entry_at": profile.last_tasks_entry_at,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0056_user_account_mode"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("video_score", models.PositiveIntegerField(default=0)),
                ("video_score_reserved", models.PositiveIntegerField(default=0)),
                ("active_status_for_video", models.BooleanField(db_index=True, default=False)),
                ("active_status_for_youtube", models.BooleanField(db_index=True, default=False)),
                ("last_video_entry_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="video_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "video_profile_table",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.RunPython(backfill_video_profile, migrations.RunPython.noop),
    ]

