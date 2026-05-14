from django.core.management.base import BaseCommand
from subscribers.models import User, SubscriberProfile, Video, VideoWatchTask
from django.utils import timezone
import random

class Command(BaseCommand):
    help = 'Create test data for video watch tasks'

    def handle(self, *args, **options):
        self.stdout.write('Creating test data...')

        # Create test users with profiles
        users_data = [
            {'username': 'testuser1', 'email': 'test1@example.com', 'video_score': 150},
            {'username': 'testuser2', 'email': 'test2@example.com', 'video_score': 200},
            {'username': 'testuser3', 'email': 'test3@example.com', 'video_score': 100},
            {'username': 'testuser4', 'email': 'test4@example.com', 'video_score': 300},
            {'username': 'testuser5', 'email': 'test5@example.com', 'video_score': 50},
        ]

        profiles = []
        for user_data in users_data:
            user, created = User.objects.get_or_create(
                username=user_data['username'],
                defaults={'email': user_data['email']}
            )
            profile, created = SubscriberProfile.objects.get_or_create(
                user=user,
                defaults={
                    'video_score': user_data['video_score'],
                    'video_score_reserved': 0,
                    'handle': f'@{user_data["username"]}',
                    'channel_total_view_count': random.randint(1000, 10000),
                    'subscribed_list': 'UC1234567890,UC0987654321'
                }
            )
            profiles.append(profile)
            self.stdout.write(f'Created profile for {user.username} with video_score {profile.video_score}')

        # Create test videos
        videos_data = [
            {
                'youtube_video_id': 'dQw4w9WgXcQ',
                'title': 'Rick Astley - Never Gonna Give You Up',
                'channel_title': 'Rick Astley',
                'description': 'The classic hit song',
                'thumbnail_url': 'https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg',
                'view_count': 1500000000,
                'duration_seconds': 213,
                'published_at': timezone.now() - timezone.timedelta(days=365*10),
                'video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
            },
            {
                'youtube_video_id': '9bZkp7q19f0',
                'title': 'PSY - GANGNAM STYLE',
                'channel_title': 'officialpsy',
                'description': 'The most viewed video on YouTube',
                'thumbnail_url': 'https://img.youtube.com/vi/9bZkp7q19f0/maxresdefault.jpg',
                'view_count': 4800000000,
                'duration_seconds': 252,
                'published_at': timezone.now() - timezone.timedelta(days=365*8),
                'video_url': 'https://www.youtube.com/watch?v=9bZkp7q19f0'
            },
            {
                'youtube_video_id': 'kJQP7kiw5Fk',
                'title': 'Despacito - Luis Fonsi ft. Daddy Yankee',
                'channel_title': 'LuisFonsiVEVO',
                'description': 'The second most viewed video',
                'thumbnail_url': 'https://img.youtube.com/vi/kJQP7kiw5Fk/maxresdefault.jpg',
                'view_count': 8200000000,
                'duration_seconds': 279,
                'published_at': timezone.now() - timezone.timedelta(days=365*6),
                'video_url': 'https://www.youtube.com/watch?v=kJQP7kiw5Fk'
            },
            {
                'youtube_video_id': 'hTWKbfoikeg',
                'title': 'Nirvana - Smells Like Teen Spirit',
                'channel_title': 'Nirvana',
                'description': 'Grunge classic',
                'thumbnail_url': 'https://img.youtube.com/vi/hTWKbfoikeg/maxresdefault.jpg',
                'view_count': 1200000000,
                'duration_seconds': 301,
                'published_at': timezone.now() - timezone.timedelta(days=365*25),
                'video_url': 'https://www.youtube.com/watch?v=hTWKbfoikeg'
            },
            {
                'youtube_video_id': 'OPf0YbXqDm0',
                'title': 'Mark Ronson - Uptown Funk ft. Bruno Mars',
                'channel_title': 'MarkRonsonVEVO',
                'description': 'Funky hit song',
                'thumbnail_url': 'https://img.youtube.com/vi/OPf0YbXqDm0/maxresdefault.jpg',
                'view_count': 4800000000,
                'duration_seconds': 270,
                'published_at': timezone.now() - timezone.timedelta(days=365*8),
                'video_url': 'https://www.youtube.com/watch?v=OPf0YbXqDm0'
            }
        ]

        videos = []
        for video_data in videos_data:
            video, created = Video.objects.get_or_create(
                youtube_video_id=video_data['youtube_video_id'],
                defaults=video_data
            )
            videos.append(video)
            self.stdout.write(f'Created video: {video.title}')

        # Create some video watch tasks for testing
        for i, profile in enumerate(profiles[:3]):  # First 3 profiles get tasks
            for j, video in enumerate(videos[:2]):  # First 2 videos
                task, created = VideoWatchTask.objects.get_or_create(
                    profile=profile,
                    video=video,
                    defaults={
                        'source_profile': profiles[(i+1) % len(profiles)],  # Different source
                        'assigned_video_score': random.randint(5, 20),
                        'assigned_watch_time_seconds': random.randint(60, 300),
                        'min_watch_time_seconds': random.randint(60, 300),
                        'watch_time_seconds': 0,
                        'verified_status': False,
                        'created_at': timezone.now(),
                        'updated_at': timezone.now()
                    }
                )
                if created:
                    self.stdout.write(f'Created task for {profile.user.username} to watch {video.title}')

        self.stdout.write(self.style.SUCCESS('Test data created successfully!'))
        self.stdout.write(f'Created {len(profiles)} profiles, {len(videos)} videos, and several tasks')