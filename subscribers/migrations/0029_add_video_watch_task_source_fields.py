from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('subscribers', '0028_add_video_system'),
    ]

    operations = [
        migrations.AddField(
            model_name='videowatchtask',
            name='source_profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='assigned_video_tasks',
                to='subscribers.subscriberprofile',
            ),
        ),
        migrations.AddField(
            model_name='videowatchtask',
            name='assigned_watch_time_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='videowatchtask',
            name='assigned_video_score',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
