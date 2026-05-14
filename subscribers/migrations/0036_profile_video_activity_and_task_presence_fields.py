from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0035_video_task_status_and_remove_video_assignment_counts"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriberprofile",
            name="active_status_for_video",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="subscriberprofile",
            name="active_status_for_youtube",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="videowatchtask",
            name="last_seen_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="videowatchtask",
            name="opened_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="videowatchtask",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("active", "Active"),
                    ("hold", "Hold"),
                    ("release", "Release"),
                    ("complete", "Complete"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
    ]