# Generated migration for video tracking system

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('subscribers', '0027_facebookprofile_page_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscriberprofile',
            name='video_score',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='subscriberprofile',
            name='video_score_reserved',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='Video',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('youtube_video_id', models.CharField(db_index=True, max_length=128, unique=True)),
                ('title', models.CharField(max_length=255)),
                ('channel_id', models.CharField(blank=True, db_index=True, max_length=128)),
                ('channel_title', models.CharField(blank=True, max_length=255)),
                ('description', models.TextField(blank=True)),
                ('thumbnail_url', models.URLField(blank=True)),
                ('duration_seconds', models.PositiveIntegerField(default=0)),
                ('view_count', models.PositiveBigIntegerField(default=0)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('video_url', models.URLField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'video_table',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='VideoWatchTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('min_watch_time_seconds', models.PositiveIntegerField(default=60)),
                ('watch_time_seconds', models.PositiveIntegerField(default=0)),
                ('verified_status', models.BooleanField(db_index=True, default=False)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('last_attempt_at', models.DateTimeField(auto_now=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='video_watch_tasks', to='subscribers.subscriberprofile')),
                ('video', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watch_task_assignments', to='subscribers.video')),
            ],
            options={
                'db_table': 'video_watch_task_table',
                'ordering': ['-updated_at', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WatchEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('watch_duration_seconds', models.PositiveIntegerField(default=0)),
                ('start_position_seconds', models.PositiveIntegerField(default=0)),
                ('end_position_seconds', models.PositiveIntegerField(default=0)),
                ('session_started_at', models.DateTimeField()),
                ('session_ended_at', models.DateTimeField(blank=True, null=True)),
                ('is_completed', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watch_events', to='subscribers.subscriberprofile')),
                ('video', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watch_events', to='subscribers.video')),
                ('watch_task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watch_events', to='subscribers.videowatchtask')),
            ],
            options={
                'db_table': 'watch_event_table',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='videowatchtask',
            constraint=models.UniqueConstraint(
                fields=['profile', 'video'],
                name='unique_video_watch_task',
            ),
        ),
    ]
