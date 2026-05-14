# Video Playback & Watch System Documentation

## Overview

The video playback system is a gamified video watching platform that tracks user watch time and awards scores based on watch duration. It's similar to the existing subscribe task system but focused on video engagement.

## System Architecture

### Database Models

#### 1. **Video Model**
Stores information about videos that users can watch.

```python
class Video(models.Model):
    youtube_video_id      # Unique YouTube video ID
    title                 # Video title
    channel_id           # YouTube channel ID
    channel_title        # Channel name
    description          # Video description
    thumbnail_url        # Video thumbnail
    duration_seconds     # Total video length in seconds
    view_count           # YouTube view count
    published_at         # Publication date
    video_url            # Direct YouTube URL
    created_at           # When added to system
    updated_at           # Last update
```

**Key Features:**
- Unique constraint on `youtube_video_id`
- Indexed by channel and creation date
- Stores complete video metadata

#### 2. **VideoWatchTask Model**
Represents a watch task assigned to a user for a specific video.

```python
class VideoWatchTask(models.Model):
    profile                    # FK to SubscriberProfile
    video                     # FK to Video
    min_watch_time_seconds    # Minimum watch time required (default: 60s)
    watch_time_seconds        # Accumulated watch time
    verified_status           # Task completion status
    verified_at              # When task was completed
    last_attempt_at          # Last watch session
```

**Key Features:**
- Unique constraint: One task per (user, video) pair
- Auto-verified when minimum watch time is reached
- Tracks accumulated watch time from multiple sessions

#### 3. **WatchEvent Model**
Records individual watch sessions within a task.

```python
class WatchEvent(models.Model):
    watch_task                # FK to VideoWatchTask
    profile                  # FK to SubscriberProfile
    video                    # FK to Video
    watch_duration_seconds   # How long watched in this session
    start_position_seconds   # Video position when started
    end_position_seconds     # Video position when stopped
    session_started_at       # Session start time
    session_ended_at         # Session end time
    is_completed             # Session completion flag
```

**Key Features:**
- Tracks granular watch session data
- Enables resume functionality
- Records viewing progress

#### 4. **SubscriberProfile Updates**
Added two new fields to track video-based scoring:

```python
class SubscriberProfile(models.Model):
    # ... existing fields ...
    video_score            # Available video score (earned & transferred)
    video_score_reserved   # Reserved score (pending verification)
```

## Score System

### Score Calculation

**Formula:** `1 minute watched = 1 score point`

```python
watch_time_minutes = watch_task.watch_time_seconds // 60
score_to_award = max(watch_time_minutes, 1)  # Minimum 1 point
```

**Score Flow:**
1. User watches video → `WatchEvent` recorded
2. Accumulated time ≥ minimum → Task verified
3. Score calculated and added to `video_score_reserved`
4. Admin/system transfers to `video_score` when confirmed
5. User can then use `video_score` for actions

### Score States

| Field | Meaning |
|-------|---------|
| `video_score` | Available score user can spend |
| `video_score_reserved` | Score pending transfer/confirmation |

## API Endpoints

### 1. **List Video Watch Tasks**
```
GET /videos/watch-tasks/
```

Returns all video watch tasks for the authenticated user with progress information.

**Response:**
```json
{
    "watch_tasks": [
        {
            "task": { ... },
            "progress": {
                "watch_time_seconds": 120,
                "min_watch_time_seconds": 60,
                "progress_percentage": 200,
                "is_completed": true,
                "watch_time_minutes": 2,
                "remaining_minutes": 0
            }
        }
    ],
    "total_tasks": 10,
    "completed_tasks": 3,
    "pending_tasks": 7,
    "total_video_score": 250,
    "reserved_video_score": 45
}
```

### 2. **Watch Video**
```
GET /videos/watch/<task_id>/
```

Display video player and watch interface.

**Template:** `subscribers/watch_video.html`

### 3. **Update Watch Time (API)**
```
POST /videos/watch/<task_id>/update-time/

Content-Type: application/json
{
    "watch_duration_seconds": 30,
    "start_position_seconds": 120,
    "end_position_seconds": 150
}
```

**Response:**
```json
{
    "success": true,
    "message": "Watch time recorded successfully",
    "watch_event_id": 123,
    "progress": { ... },
    "is_completed": false
}
```

### 4. **Complete Watch Task**
```
POST /videos/watch/<task_id>/complete/
```

Verify and finalize the watch task. Awards score if minimum time is met.

**Response (Success):**
```json
{
    "success": true,
    "message": "Video watch task completed! Score awarded.",
    "is_completed": true,
    "progress": { ... }
}
```

**Response (Failure - Insufficient Time):**
```json
{
    "success": false,
    "message": "Minimum watch time not met. 300 seconds remaining.",
    "is_completed": false,
    "progress": { ... }
}
```

### 5. **Video Score Details**
```
GET /videos/score-details/
```

Show score summary and watch history.

## Service Functions

### Core Functions

#### `record_watch_event(watch_task, watch_duration_seconds, start_position_seconds=0, end_position_seconds=0)`

Records a watch session and updates accumulated watch time.

```python
from subscribers.services import record_watch_event

watch_event = record_watch_event(
    watch_task=my_task,
    watch_duration_seconds=120,
    start_position_seconds=0,
    end_position_seconds=120
)
```

**Behavior:**
- Creates `WatchEvent` record
- Updates `watch_time_seconds` on task
- Auto-verifies if minimum time reached
- Awards score automatically

#### `verify_video_watch_task(watch_task)`

Manually verify a task if minimum watch time is met.

```python
is_verified = verify_video_watch_task(my_task)
if is_verified:
    print("Task completed and scored!")
```

#### `get_video_watch_progress(watch_task)`

Get progress details for a task.

```python
progress = get_video_watch_progress(my_task)
print(f"Progress: {progress['progress_percentage']}%")
print(f"Remaining: {progress['remaining_seconds']}s")
```

#### `transfer_video_score_to_available(profile, amount=None)`

Transfer reserved score to available score.

```python
from subscribers.services import transfer_video_score_to_available

# Transfer all reserved score
transferred = transfer_video_score_to_available(profile)

# Transfer specific amount
transferred = transfer_video_score_to_available(profile, amount=50)
```

#### `use_video_score(profile, amount)`

Deduct available video score.

```python
from subscribers.services import use_video_score

success = use_video_score(profile, amount=100)
if not success:
    print("Insufficient score")
```

## Admin Interface

### Video Admin
- List videos by title, YouTube ID, channel
- Filter by publish date and creation date
- View video statistics (views, duration)

### VideoWatchTask Admin
- List all tasks with progress
- Filter by verification status
- Search by user, profile, or video title
- View task creation and verification dates

### WatchEvent Admin
- View individual watch sessions
- Filter by completion status
- Track watch position and duration
- See session timestamps

## Usage Examples

### Example 1: Creating a Video Task

```python
from subscribers.models import Video, VideoWatchTask, SubscriberProfile

# Get or create video
video, _ = Video.objects.get_or_create(
    youtube_video_id='dQw4w9WgXcQ',
    defaults={
        'title': 'Never Gonna Give You Up',
        'channel_id': 'UCuAXFkgsw1L7xaCfnd5J JVw',
        'channel_title': 'Rick Astley',
        'duration_seconds': 213,
        'video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    }
)

# Get user profile
profile = SubscriberProfile.objects.get(user__username='john_doe')

# Create watch task (require 60 seconds of watching)
task, created = VideoWatchTask.objects.get_or_create(
    profile=profile,
    video=video,
    defaults={'min_watch_time_seconds': 60}
)
```

### Example 2: Recording Watch Time

```python
from subscribers.services import record_watch_event

# User watches 30 seconds
watch_event = record_watch_event(
    watch_task=task,
    watch_duration_seconds=30,
    start_position_seconds=0,
    end_position_seconds=30
)

# User comes back and watches another 40 seconds
watch_event = record_watch_event(
    watch_task=task,
    watch_duration_seconds=40,
    start_position_seconds=30,
    end_position_seconds=70
)

# Task is now verified (70 seconds total ≥ 60 seconds minimum)
# Score awarded: 1 point (70 seconds = 1 minute)
print(task.verified_status)  # True
print(profile.video_score_reserved)  # 1
```

### Example 3: JavaScript Frontend Integration

```javascript
// Record watch time periodically (e.g., every 5 seconds)
async function recordWatchTime(taskId, watchDurationSeconds) {
    const response = await fetch(`/videos/watch/${taskId}/update-time/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            watch_duration_seconds: watchDurationSeconds,
            start_position_seconds: 0,
            end_position_seconds: watchDurationSeconds
        })
    });
    
    const data = await response.json();
    if (data.success) {
        console.log(`Task progress: ${data.progress.progress_percentage}%`);
        if (data.is_completed) {
            console.log('Task completed and scored!');
        }
    }
}

// Complete the task manually
async function completeTask(taskId) {
    const response = await fetch(`/videos/watch/${taskId}/complete/`, {
        method: 'POST',
        headers: {
            'X-CSRFToken': getCookie('csrftoken')
        }
    });
    
    const data = await response.json();
    if (data.success) {
        console.log('Task verified and scored!');
    }
}
```

## Workflow Summary

1. **Admin creates Video** → stored in database
2. **System assigns VideoWatchTask** → user gets task
3. **User watches video** → records WatchEvents
4. **System tracks watch time** → accumulates in task
5. **Minimum time reached** → auto-verify & award score
6. **Score reserved** → pending admin confirmation
7. **Admin transfers score** → becomes available
8. **User spends score** → redeems for rewards

## Key Differences from Subscribe Task System

| Aspect | Subscribe Task | Watch Task |
|--------|----------------|-----------|
| **Unit** | Per channel subscription | Per video watched |
| **Score** | Earned on verification | Earned per minute watched |
| **Verification** | Manual or auto-check | Auto on time threshold |
| **Reversible** | Yes (can unsubscribe) | No (can't "unwatch") |
| **Fields** | `score`, `reserved_score` | `video_score`, `video_score_reserved` |

## Admin Operations

### Transfer Video Scores

```python
from subscribers.services import transfer_video_score_to_available

# Transfer all reserved scores for a user
profile = SubscriberProfile.objects.get(id=1)
transferred = transfer_video_score_to_available(profile)
print(f"Transferred {transferred} points")
```

### Bulk Verify Tasks

```python
# Mark all completed tasks as verified
for task in VideoWatchTask.objects.filter(verified_status=False):
    if task.watch_time_seconds >= task.min_watch_time_seconds:
        verify_video_watch_task(task)
```

## Notes

- Watch time is cumulative across sessions
- Scores are awarded only once per task
- Video metadata should be synced with YouTube API periodically
- WatchEvents provide audit trail of all viewing activity
- The system supports resume functionality via position tracking
