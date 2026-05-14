from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscribers', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscriberprofile',
            name='google_subject_id',
            field=models.CharField(blank=True, max_length=128, null=True, unique=True),
        ),
    ]
