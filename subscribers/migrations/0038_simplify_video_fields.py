from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0037_video_core_fields_for_assignment_flow"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="video",
            name="unique_manual_video_slot_per_profile",
        ),
        migrations.RemoveField(
            model_name="video",
            name="added_by",
        ),
        migrations.RemoveField(
            model_name="video",
            name="channel_id",
        ),
        migrations.RemoveField(
            model_name="video",
            name="channel_title",
        ),
        migrations.RemoveField(
            model_name="video",
            name="description",
        ),
        migrations.RemoveField(
            model_name="video",
            name="published_at",
        ),
        migrations.RemoveField(
            model_name="video",
            name="source_profile",
        ),
        migrations.RemoveField(
            model_name="video",
            name="source_slot",
        ),
        migrations.RemoveField(
            model_name="video",
            name="thumbnail_url",
        ),
        migrations.RemoveField(
            model_name="video",
            name="title",
        ),
        migrations.RemoveField(
            model_name="video",
            name="view_count",
        ),
        migrations.RemoveField(
            model_name="video",
            name="youtube_video_id",
        ),
        migrations.AlterField(
            model_name="video",
            name="video_url",
            field=models.URLField(unique=True),
        ),
    ]
