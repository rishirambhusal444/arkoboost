from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0029_add_video_watch_task_source_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="watchevent",
            name="client_timestamp_ms",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="event_type",
            field=models.CharField(db_index=True, default="heartbeat", max_length=24),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="invalid_reason",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="ip_address",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="is_muted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="is_player_playing",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="is_tab_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="is_valid",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="pause_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="playback_rate",
            field=models.FloatField(default=1.0),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="seek_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="session_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="watchevent",
            name="user_agent",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
