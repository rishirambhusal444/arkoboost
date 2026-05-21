from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0062_manual_facebook_follow_system"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminVideo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("home_video_url", models.URLField(blank=True)),
                ("task_video_url_subscribe", models.URLField(blank=True)),
                ("task_video_url_subscribe_verify", models.URLField(blank=True)),
                ("task_video_url_facebook", models.URLField(blank=True)),
                ("task_video_url_facebook_verify", models.URLField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Admin Videos",
                "verbose_name_plural": "Admin Videos",
                "db_table": "admin_videos",
            },
        ),
    ]

