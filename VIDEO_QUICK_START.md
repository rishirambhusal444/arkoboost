# Video Playback System - Quick Start Guide

## System Overview

You now have a complete **Video Playback & Watch-Time Tracking System** integrated into your newyoutubers project. This system allows you to:

- Track which videos users are watching
- Record watch time and viewing progress
- Auto-award scores based on watch duration
- Manage video tasks similar to subscribe tasks

## Database Structure

### 4 New Models Created:

1. **Video** - Stores video information
2. **VideoWatchTask** - A user's watch assignment for a video
3. **WatchEvent** - Records individual watch sessions
4. **SubscriberProfile** (Updated) - Added `video_score` and `video_score_reserved` fields

## Score System (Important!)

**Formula:** `1 minute watched = 1 score point`

- **video_score**: Available score user can spend
- **video_score_reserved**: Score waiting to be transferred/confirmed

**Flow:**
1. User watches video
2. System accumulates watch time
3. When minimum time is reached → Auto-verify
4. Score awarded to `video_score_reserved`
5. Admin transfers to `video_score` (or auto-transfer)
6. User can now spend the score

## Key API Endpoints

### 1. List User's Video Tasks
```
GET /videos/watch-tasks/
```
Shows all video watch tasks with progress

### 2. Watch a Video
```
GET /videos/watch/<task_id>/
```
Display video player page

### 3. Record Watch Time (from Video Player)
```
POST /videos/watch/<task_id>/update-time/

JSON Body:
{
    "watch_duration_seconds": 30,
    "start_position_seconds": 0,
    "end_position_seconds": 30
}
```
Response shows progress and completion status

### 4. Complete a Task
```
POST /videos/watch/<task_id>/complete/
```
Finalize task and verify completion

### 5. View Score Details
```
GET /videos/score-details/
```
Show user's video score summary

## Quick Testing

### In Django Shell:

```python
from subscribers.models import Video, VideoWatchTask, SubscriberProfile
from subscribers.services import record_watch_event, get_video_watch_progress

# Get a user
profile = SubscriberProfile.objects.first()

# Create a test video
video, _ = Video.objects.get_or_create(
    youtube_video_id='test_video_123',
    defaults={
        'title': 'Test Video',
        'channel_id': 'test_channel',
        'channel_title': 'Test Channel',
        'duration_seconds': 600,
    }
)

# Create a watch task (60 second minimum)
task, _ = VideoWatchTask.objects.get_or_create(
    profile=profile,
    video=video,
    defaults={'min_watch_time_seconds': 60}
)

# Record a 70-second watch session
watch_event = record_watch_event(
    watch_task=task,
    watch_duration_seconds=70
)

# Check task status
print(f"Task verified: {task.verified_status}")  # Should be True
print(f"Video score reserved: {profile.video_score_reserved}")  # Should be 1

# Check progress
progress = get_video_watch_progress(task)
print(f"Progress: {progress['progress_percentage']}%")
```

## Admin Panel Access

Go to `/admin/` and look for:
- **Videos** - Manage video library
- **Video Watch Tasks** - Track user assignments
- **Watch Events** - Audit trail of watch sessions

## Admin Operations

### Transfer Reserved Scores

```python
from subscribers.services import transfer_video_score_to_available

profile = SubscriberProfile.objects.get(id=1)

# Transfer all reserved score to available
transferred = transfer_video_score_to_available(profile)
print(f"Transferred {transferred} points")

# Or transfer specific amount
transferred = transfer_video_score_to_available(profile, amount=50)
```

### Check User Stats

```python
profile = SubscriberProfile.objects.get(id=1)

print(f"Available video score: {profile.video_score}")
print(f"Reserved video score: {profile.video_score_reserved}")
print(f"Total: {profile.video_score + profile.video_score_reserved}")

# See all their watch tasks
tasks = profile.video_watch_tasks.all()
for task in tasks:
    print(f"{task.video.title}: {task.watch_time_seconds}s watched, verified={task.verified_status}")
```

## Next Steps

1. **Create Templates** - Design HTML templates for:
   - Video watch list (`subscribers/video_watch_tasks_list.html`)
   - Video player (`subscribers/watch_video.html`)
   - Score details (`subscribers/video_score_details.html`)

2. **Frontend Integration** - Add JavaScript to:
   - Track watch time automatically
   - Update progress bar
   - Report back to server periodically

3. **Assign Tasks** - Create tasks through Django shell or admin:
   ```python
   # Bulk create tasks for users
   from subscribers.models import Video, VideoWatchTask, SubscriberProfile
   
   video = Video.objects.first()
   users = SubscriberProfile.objects.filter(active_status=True)
   
   for user_profile in users:
       VideoWatchTask.objects.get_or_create(
           profile=user_profile,
           video=video,
           defaults={'min_watch_time_seconds': 120}
       )
   ```

4. **YouTube Integration** (Optional) - Sync videos from YouTube API:
   ```python
   # Pseudo-code for YouTube sync
   from youtube_data_api import get_video_info
   
   for video_id in video_ids:
       info = get_video_info(video_id)
       Video.objects.update_or_create(
           youtube_video_id=video_id,
           defaults={
               'title': info['title'],
               'channel_id': info['channel_id'],
               # ... other fields
           }
       )
   ```

5. **Automation** - Setup periodic tasks to:
   - Auto-transfer reserved scores
   - Sync video metadata
   - Generate reports

## Important Notes

- ✅ System is fully integrated and tested
- ✅ Database migrations applied successfully
- ✅ Admin interface ready to use
- ✅ API endpoints ready for frontend integration
- ⏳ Templates need to be created (if you want web UI)
- ⏳ Video data needs to be populated (via admin or script)
- ⏳ Tasks need to be assigned to users

## Architecture Diagram

```
User
  ↓
VideoWatchTask (assignment)
  ├─→ Video (what to watch)
  └─→ WatchEvent (individual sessions)
       └─→ Records watch time
           └─→ Auto-verifies at min_watch_time
               └─→ Awards video_score_reserved
                   └─→ Transfers to video_score
```

## File Locations

- **Models**: `subscribers/models.py` (lines 207-310)
- **Services**: `subscribers/services.py` (lines 495-637)
- **Views**: `subscribers/views.py` (lines 1190-1357)
- **URLs**: `subscribers/urls.py` (lines 26-33)
- **Admin**: `subscribers/admin.py` (lines 120-198)
- **Migration**: `subscribers/migrations/0028_add_video_system.py`
- **Docs**: `VIDEO_SYSTEM_DOCUMENTATION.md`

## Troubleshooting

### "Task not found" error
- Make sure task belongs to logged-in user
- Check task ID is correct

### Score not awarded
- Verify `watch_time_seconds >= min_watch_time_seconds`
- Check that watch events are being recorded
- Confirm auto-verify happened

### Database errors
- Run `python manage.py migrate` if you get migration errors
- Run `python manage.py check` to validate setup

## Support Files

Full documentation available in:
- `VIDEO_SYSTEM_DOCUMENTATION.md` - Complete technical docs
- This file - Quick start guide

---

**System Status**: ✅ Ready to use
**Last Updated**: 2026-05-01
**Version**: 1.0
