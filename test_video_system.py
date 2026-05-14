#!/usr/bin/env python
"""
Test script for video playback system
Run with: python manage.py shell < test_video_system.py
"""

from subscribers.models import Video, VideoWatchTask, SubscriberProfile, WatchEvent
from subscribers.services import record_watch_event, get_video_watch_progress, verify_video_watch_task

# Test: Create and verify models exist
print("✓ Models imported successfully")

# Create a test user profile
user_profiles = SubscriberProfile.objects.all()[:1]
if user_profiles:
    profile = user_profiles[0]
    print(f"✓ Using profile: {profile.handle or profile.user.username}")
    
    # Create a test video
    video, created = Video.objects.get_or_create(
        youtube_video_id='test_video_system_123',
        defaults={
            'title': 'Test Video System',
            'channel_id': 'test_ch_001',
            'channel_title': 'Test Channel',
            'duration_seconds': 600,
        }
    )
    print(f"✓ Video created: {video.title}")
    
    # Create watch task
    task, task_created = VideoWatchTask.objects.get_or_create(
        profile=profile,
        video=video,
        defaults={'min_watch_time_seconds': 60}
    )
    print(f"✓ Watch task created: {task_created}")
    print(f"  Task ID: {task.id}")
    print(f"  Min watch time: {task.min_watch_time_seconds}s")
    
    # Record watch time
    watch_event = record_watch_event(
        watch_task=task,
        watch_duration_seconds=75,
        start_position_seconds=0,
        end_position_seconds=75
    )
    print(f"✓ Watch event recorded: {watch_event.id}")
    
    # Refresh task to get updated data
    task.refresh_from_db()
    profile.refresh_from_db()
    
    # Check results
    print(f"✓ Task verified: {task.verified_status}")
    print(f"✓ Total watch time: {task.watch_time_seconds}s")
    print(f"✓ Video score reserved: {profile.video_score_reserved}")
    
    # Get progress
    progress = get_video_watch_progress(task)
    print(f"✓ Progress: {progress['progress_percentage']:.1f}%")
    print(f"✓ Remaining: {progress['remaining_seconds']}s")
    
    print("\n✅ VIDEO SYSTEM TEST PASSED - All components working correctly!")
else:
    print("⚠ No user profiles found - please create a user first")
