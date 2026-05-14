from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0019_remove_topusersubscribetask_status"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="topusersubscribetask",
            name="score_awarded",
        ),
    ]
