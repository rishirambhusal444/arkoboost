from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0033_video_added_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="assignment_completed_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="video",
            name="assignment_pending_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="video",
            name="assignment_total_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="video",
            name="assignment_total_minutes",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
