from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0038_simplify_video_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="active_status",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
