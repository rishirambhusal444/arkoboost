from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0030_watch_event_anticheat_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriberprofile",
            name="manual_video_url_1",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="subscriberprofile",
            name="manual_video_url_2",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="subscriberprofile",
            name="manual_video_url_3",
            field=models.URLField(blank=True),
        ),
    ]
