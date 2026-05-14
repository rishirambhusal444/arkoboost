from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0034_video_assignment_management_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="video",
            name="assignment_completed_count",
        ),
        migrations.RemoveField(
            model_name="video",
            name="assignment_pending_count",
        ),
        migrations.RemoveField(
            model_name="video",
            name="assignment_total_count",
        ),
        migrations.RemoveField(
            model_name="video",
            name="assignment_total_minutes",
        ),
        migrations.AddField(
            model_name="videowatchtask",
            name="status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("hold", "Hold"), ("complete", "Complete")],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
    ]
