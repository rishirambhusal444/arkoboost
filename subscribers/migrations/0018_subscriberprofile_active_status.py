from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0017_subscriberprofile_last_tasks_entry_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriberprofile",
            name="active_status",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
