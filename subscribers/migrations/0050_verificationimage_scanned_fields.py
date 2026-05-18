from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0049_verificationimage"),
    ]

    operations = [
        migrations.AddField(
            model_name="verificationimage",
            name="scanned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="verificationimage",
            name="scanned_status",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
