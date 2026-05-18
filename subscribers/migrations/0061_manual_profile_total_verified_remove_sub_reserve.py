from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0060_manual_task_status_lifecycle"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="manualsubscribeprofile",
            name="sub_reserve_score",
        ),
        migrations.AddField(
            model_name="manualsubscribeprofile",
            name="total_verified",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
