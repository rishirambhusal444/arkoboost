"""
Video Watch System Demo Script

This script demonstrates the complete video watch system:
1. Creating sample videos
2. Assigning watch tasks to users
3. Recording watch events
4. Tracking scores and progress

Usage:
    python manage.py shell < demo_video_system.py
"""

from django.contrib.auth import get_user_model
from subscribers.models import (
    SubscriberProfile,
    Video,
    VideoWatchTask,
    WatchEvent,
)
from subscribers.services import (
    record_watch_event,
    verify_video_watch_task,
    get_video_watch_progress,
    transfer_video_score_to_available,
)

User = get_user_model()

print("=" * 80)
print("VIDEO WATCH SYSTEM - DEMO")
print("=" * 80)

# Step 1: Create sample videos
print("\n[1] Creating sample videos...")
videos_data = [
    {
        "youtube_video_id": "dQw4w9WgXcQ",
        "title": "10 Tips for Content Creators",
        "channel_id": "UCuAXFkgsw1L7xaCfnd5J0Gw",
        "channel_title": "YouTube Creators",
        "description": "Learn the best practices for creating engaging content on YouTube.",
        "duration_seconds": 480,  # 8 minutes
        "view_count": 150000,
        "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "thumbnail_url": "https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
    },
    {
        "youtube_video_id": "9bZkp7q19f0",
        "title": "Growing Your YouTube Channel in 2024",
        "channel_id": "UCuAXFkgsw1L7xaCfnd5J0Gw",
        "channel_title": "YouTube Creators",
        "description": "A comprehensive guide to growing your YouTube channel in 2024.",
        "duration_seconds": 600,  # 10 minutes
        "view_count": 250000,
        "video_url": "https://www.youtube.com/watch?v=9bZkp7q19f0",
        "thumbnail_url": "https://img.youtube.com/vi/9bZkp7q19f0/maxresdefault.jpg",
    },
    {
        "youtube_video_id": "1ELlHQrU5OU",
        "title": "SEO Optimization for YouTubers",
        "channel_id": "UCuAXFkgsw1L7xaCfnd5J0Gw",
        "channel_title": "YouTube Creators",
        "description": "Master SEO techniques to get your videos discovered.",
        "duration_seconds": 420,  # 7 minutes
        "view_count": 180000,
        "video_url": "https://www.youtube.com/watch?v=1ELlHQrU5OU",
        "thumbnail_url": "https://img.youtube.com/vi/1ELlHQrU5OU/maxresdefault.jpg",
    },
]

created_videos = []
for video_data in videos_data:
    video, created = Video.objects.get_or_create(
        youtube_video_id=video_data["youtube_video_id"],
        defaults=video_data,
    )
    created_videos.append(video)
    status = "✓ Created" if created else "⊘ Already exists"
    print(f"  {status}: {video.title} ({video.youtube_video_id})")

# Step 2: Get or create a test user and profile
print("\n[2] Setting up test user and profile...")
test_user, user_created = User.objects.get_or_create(
    username="test_video_viewer",
    defaults={
        "email": "viewer@example.com",
        "is_active": True,
    }
)
profile, profile_created = SubscriberProfile.objects.get_or_create(
    user=test_user,
    defaults={
        "handle": "test_viewer",
        "active_status": True,
    }
)
print(f"  User: {test_user.username} ({'created' if user_created else 'existing'})")
print(f"  Profile: {profile.handle} ({'created' if profile_created else 'existing'})")

# Step 3: Assign videos as watch tasks
print("\n[3] Assigning videos as watch tasks...")
for video in created_videos:
    task, created = VideoWatchTask.objects.get_or_create(
        profile=profile,
        video=video,
        defaults={
            "min_watch_time_seconds": 180,  # 3 minutes minimum
        }
    )
    status = "✓ Assigned" if created else "⊘ Already assigned"
    print(f"  {status}: {video.title} (Required: {task.min_watch_time_seconds}s)")

# Step 4: Simulate watch events
print("\n[4] Simulating watch events...")
watch_tasks = profile.video_watch_tasks.all()

for i, task in enumerate(watch_tasks, 1):
    print(f"\n  Video {i}: {task.video.title}")
    
    # Record multiple watch sessions
    watch_sessions = [
        {"duration": 100, "start": 0, "end": 100},
        {"duration": 80, "start": 100, "end": 180},
        {"duration": 50, "start": 180, "end": 230},
    ]
    
    for j, session in enumerate(watch_sessions, 1):
        watch_event = record_watch_event(
            watch_task=task,
            watch_duration_seconds=session["duration"],
            start_position_seconds=session["start"],
            end_position_seconds=session["end"],
        )
        print(f"    Session {j}: {session['duration']}s recorded")
        
        # Check progress
        progress = get_video_watch_progress(task)
        print(f"    Progress: {progress['watch_time_seconds']}s / {progress['min_watch_time_seconds']}s " 
              f"({progress['progress_percentage']:.0f}%)")
    
    # Verify task if completed
    if task.verified_status:
        print(f"    ✓ Task completed! Score awarded.")
    else:
        print(f"    ⚠ Task not yet complete")

# Step 5: Show final scores and progress
print("\n[5] Final Status Report")
print("-" * 80)

profile.refresh_from_db()
print(f"User: {profile.handle}")
print(f"  Available Score: {profile.video_score}")
print(f"  Reserved Score: {profile.video_score_reserved}")
print(f"  Total Score: {profile.video_score + profile.video_score_reserved}")

print(f"\nWatch Tasks:")
for task in profile.video_watch_tasks.all():
    progress = get_video_watch_progress(task)
    status = "✓ COMPLETED" if task.verified_status else "⚠ PENDING"
    print(f"  {status} | {task.video.title}")
    print(f"    Watch Time: {progress['watch_time_seconds']}s / {progress['min_watch_time_seconds']}s")
    print(f"    Progress: {progress['progress_percentage']:.0f}%")

print(f"\nWatch Events: {WatchEvent.objects.filter(profile=profile).count()} recorded")
total_watch_time = sum(
    event.watch_duration_seconds 
    for event in WatchEvent.objects.filter(profile=profile)
)
print(f"Total Watch Time: {total_watch_time}s ({total_watch_time // 60}m {total_watch_time % 60}s)")

# Step 6: Demonstrate score transfer
print("\n[6] Demonstrating score transfer...")
transfer_amount = transfer_video_score_to_available(profile, amount=2)
print(f"  Transferred {transfer_amount} score from reserved to available")
profile.refresh_from_db()
print(f"  Available Score: {profile.video_score}")
print(f"  Reserved Score: {profile.video_score_reserved}")

print("\n" + "=" * 80)
print("✓ DEMO COMPLETE")
print("=" * 80)
print("\nNext steps:")
print("1. Visit the dashboard to see the new video watch system")
print("2. Go to: http://localhost:8000/videos/watch-tasks/")
print("3. Click on a video to watch and track progress")
print("4. Complete watch tasks to earn video score")
