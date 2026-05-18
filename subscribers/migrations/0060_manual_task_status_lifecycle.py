from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0059_rebalance_performance_indexes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="manualsubscribetaskassign",
            name="subscribed_status",
            field=models.CharField(
                choices=[
                    ("assigned", "Assigned"),
                    ("unverified", "Unverified"),
                    ("verified", "Verified"),
                    ("released", "Released"),
                ],
                db_index=True,
                default="assigned",
                max_length=16,
            ),
        ),
    ]
