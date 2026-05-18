from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0051_verificationimage_extracted_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="manualsubscribeprofile",
            name="loyal_score",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
