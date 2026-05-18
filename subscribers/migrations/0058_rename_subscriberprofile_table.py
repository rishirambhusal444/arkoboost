from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0057_video_profile"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="subscriberprofile",
            table="google_subscribe_profile",
        ),
    ]

