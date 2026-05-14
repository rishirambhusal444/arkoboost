from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0016_topusersubscribetask_score_awarded"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriberprofile",
            name="last_tasks_entry_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
