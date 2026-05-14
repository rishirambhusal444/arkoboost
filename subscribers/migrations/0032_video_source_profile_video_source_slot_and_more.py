from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0031_profile_manual_video_slots"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="source_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="manual_videos",
                to="subscribers.subscriberprofile",
            ),
        ),
        migrations.AddField(
            model_name="video",
            name="source_slot",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="video",
            name="youtube_video_id",
            field=models.CharField(db_index=True, max_length=128),
        ),
        migrations.AddConstraint(
            model_name="video",
            constraint=models.UniqueConstraint(
                fields=("source_profile", "source_slot"),
                name="unique_manual_video_slot_per_profile",
            ),
        ),
    ]
