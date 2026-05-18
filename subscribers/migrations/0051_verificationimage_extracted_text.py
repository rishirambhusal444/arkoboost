from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0050_verificationimage_scanned_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="verificationimage",
            name="extracted_text",
            field=models.TextField(blank=True),
        ),
    ]
