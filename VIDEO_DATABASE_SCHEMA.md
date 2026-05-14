# Video Playback System - Database Schema

## Entity Relationship Diagram

```
┌─────────────────────┐
│   auth.User         │
├─────────────────────┤
│ id (PK)             │
│ username            │
│ email               │
│ is_active           │
│ date_joined         │
└─────────────────────┘
        │ 1
        │ OneToOne
        │
        ↓ *
┌─────────────────────┐
│ SubscriberProfile   │ (UPDATED)
├─────────────────────┤
│ id (PK)             │
│ user (FK) [unique]  │
│ channel_id          │
│ channel_title       │
│ handle              │
│ score               │
│ reserved_score      │
├─────────────────────┤
│ video_score         │ ← NEW
│ video_score_reserved│ ← NEW
└─────────────────────┘
        │ *
        │ FK
        ├───────────────────────────────┐
        │                               │
        ↓                               ↓
┌──────────────────────┐      ┌──────────────────────┐
│ VideoWatchTask       │      │ WatchEvent           │
├──────────────────────┤      ├──────────────────────┤
│ id (PK)              │      │ id (PK)              │
│ profile (FK)         │←─────┤ profile (FK)         │
│ video (FK)           │      │ video (FK)           │
│ min_watch_time_sec   │      │ watch_task (FK)      │
│ watch_time_sec       │←─────┤ watch_duration_sec   │
│ verified_status      │      │ start_position_sec   │
│ verified_at          │      │ end_position_sec     │
│ last_attempt_at      │      │ session_started_at   │
│ created_at           │      │ session_ended_at     │
│ updated_at           │      │ is_completed         │
├──────────────────────┤      │ created_at           │
│ Unique:              │      │ updated_at           │
│ (profile, video)     │      └──────────────────────┘
└──────────────────────┘
        │ *
        │ FK
        │
        ↓
    ┌────────────────────┐
    │ Video              │
    ├────────────────────┤
    │ id (PK)            │
    │ youtube_video_id   │ [UNIQUE, INDEXED]
    │ title              │
    │ channel_id         │ [INDEXED]
    │ channel_title      │
    │ description        │
    │ thumbnail_url      │
    │ duration_seconds   │
    │ view_count         │
    │ published_at       │
    │ video_url          │
    │ created_at         │ [INDEXED]
    │ updated_at         │
    └────────────────────┘
```

## Table Details

### Video Table (`video_table`)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BigAutoField | PRIMARY KEY | Auto-incrementing ID |
| youtube_video_id | CharField(128) | UNIQUE, INDEX | YouTube video ID |
| title | CharField(255) | | Video title |
| channel_id | CharField(128) | INDEX | YouTube channel ID |
| channel_title | CharField(255) | | YouTube channel name |
| description | TextField | BLANK | Video description |
| thumbnail_url | URLField | BLANK | Thumbnail URL |
| duration_seconds | PositiveIntegerField | DEFAULT=0 | Video length in seconds |
| view_count | PositiveBigIntegerField | DEFAULT=0 | YouTube view count |
| published_at | DateTimeField | NULL, BLANK | Publication date |
| video_url | URLField | BLANK | YouTube video URL |
| created_at | DateTimeField | AUTO_NOW_ADD | Creation timestamp |
| updated_at | DateTimeField | AUTO_NOW | Last update timestamp |

**Indexes:**
- PRIMARY: id
- UNIQUE: youtube_video_id
- INDEX: channel_id, created_at, updated_at

**Ordering:** `-updated_at`

---

### VideoWatchTask Table (`video_watch_task_table`)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BigAutoField | PRIMARY KEY | Auto-incrementing ID |
| profile_id | BigInteger | FOREIGN KEY | Reference to SubscriberProfile |
| video_id | BigInteger | FOREIGN KEY | Reference to Video |
| min_watch_time_seconds | PositiveIntegerField | DEFAULT=60 | Minimum required watch time |
| watch_time_seconds | PositiveIntegerField | DEFAULT=0 | Accumulated watch time |
| verified_status | BooleanField | DEFAULT=False, INDEX | Task completion flag |
| verified_at | DateTimeField | NULL, BLANK | When task was completed |
| last_attempt_at | DateTimeField | AUTO_NOW | Last activity timestamp |
| error_message | TextField | BLANK | Error details if any |
| created_at | DateTimeField | AUTO_NOW_ADD | Task creation time |
| updated_at | DateTimeField | AUTO_NOW | Last update time |

**Constraints:**
- UNIQUE: (profile_id, video_id)
  - One task per user per video

**Indexes:**
- PRIMARY: id
- FOREIGN: profile_id, video_id
- INDEX: verified_status

**Ordering:** `-updated_at, -created_at`

---

### WatchEvent Table (`watch_event_table`)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BigAutoField | PRIMARY KEY | Auto-incrementing ID |
| watch_task_id | BigInteger | FOREIGN KEY | Reference to VideoWatchTask |
| profile_id | BigInteger | FOREIGN KEY | Reference to SubscriberProfile |
| video_id | BigInteger | FOREIGN KEY | Reference to Video |
| watch_duration_seconds | PositiveIntegerField | DEFAULT=0 | Duration of this session |
| start_position_seconds | PositiveIntegerField | DEFAULT=0 | Video position at start |
| end_position_seconds | PositiveIntegerField | DEFAULT=0 | Video position at end |
| session_started_at | DateTimeField | | Session start time |
| session_ended_at | DateTimeField | NULL, BLANK | Session end time |
| is_completed | BooleanField | DEFAULT=False, INDEX | Session completion flag |
| created_at | DateTimeField | AUTO_NOW_ADD | Event creation time |
| updated_at | DateTimeField | AUTO_NOW | Last update time |

**Indexes:**
- PRIMARY: id
- FOREIGN: watch_task_id, profile_id, video_id
- INDEX: is_completed, session_started_at, created_at

**Ordering:** `-created_at`

---

### SubscriberProfile Updates

Added two new columns to the existing `profile_table`:

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| video_score | PositiveIntegerField | DEFAULT=0 | Available video score |
| video_score_reserved | PositiveIntegerField | DEFAULT=0 | Reserved/pending score |

---

## Data Flow Diagram

```
User Watches Video
       │
       ├─→ HTTP Request to watch_video()
       │   └─→ GET /videos/watch/<task_id>/
       │
       └─→ JavaScript tracks video time
           │
           └─→ Every N seconds:
               POST /videos/watch/<task_id>/update-time/
               {
                   "watch_duration_seconds": 30,
                   "start_position_seconds": 0,
                   "end_position_seconds": 30
               }
               │
               └─→ Backend calls record_watch_event()
                   │
                   ├─→ Create WatchEvent record
                   │
                   ├─→ Update VideoWatchTask.watch_time_seconds
                   │   (add watch_duration_seconds)
                   │
                   ├─→ Check: watch_time >= min_watch_time?
                   │   │
                   │   ├─ YES:
                   │   │   ├─→ Set verified_status = True
                   │   │   ├─→ Set verified_at = now
                   │   │   └─→ Call _award_video_score()
                   │   │       │
                   │   │       ├─→ Calculate: points = watch_time // 60
                   │   │       └─→ Add to video_score_reserved
                   │   │
                   │   └─ NO: Continue accumulating
                   │
                   └─→ Return progress to frontend
                       {
                           "progress_percentage": 125,
                           "remaining_seconds": 0,
                           "is_completed": true
                       }
```

## Query Examples

### Get User's Watch Tasks with Progress
```sql
SELECT 
    vwt.id,
    v.title,
    vwt.watch_time_seconds,
    vwt.min_watch_time_seconds,
    vwt.verified_status,
    (100 * vwt.watch_time_seconds / vwt.min_watch_time_seconds) as progress_pct
FROM video_watch_task_table vwt
JOIN video_table v ON vwt.video_id = v.id
WHERE vwt.profile_id = ?
ORDER BY vwt.updated_at DESC;
```

### Get Completed Tasks for User
```sql
SELECT COUNT(*)
FROM video_watch_task_table
WHERE profile_id = ? AND verified_status = TRUE;
```

### Get Total Watch Time for User
```sql
SELECT SUM(watch_duration_seconds) as total_watch_time
FROM watch_event_table
WHERE profile_id = ?;
```

### Get Video Performance Stats
```sql
SELECT 
    v.id,
    v.title,
    COUNT(vwt.id) as total_assignments,
    SUM(CASE WHEN vwt.verified_status THEN 1 ELSE 0 END) as completed,
    AVG(vwt.watch_time_seconds) as avg_watch_time
FROM video_table v
LEFT JOIN video_watch_task_table vwt ON v.id = vwt.video_id
GROUP BY v.id
ORDER BY completed DESC;
```

## Migration Details

**Migration File:** `0028_add_video_system.py`

**Operations:**
1. Add `video_score` field to SubscriberProfile
2. Add `video_score_reserved` field to SubscriberProfile
3. Create Video model
4. Create VideoWatchTask model
5. Create WatchEvent model
6. Add unique constraint on VideoWatchTask(profile, video)

**Status:** ✅ Applied

---

## Performance Considerations

### Indexes Placed On:
- `Video.youtube_video_id` - Fast video lookup
- `Video.channel_id` - Filter videos by channel
- `VideoWatchTask.verified_status` - Quick filter for completed/pending tasks
- `WatchEvent.is_completed` - Find active watch sessions
- `WatchEvent.session_started_at` - Timeline queries

### Suggested Indexes (Optional):
```sql
-- If doing heavy filtering on profile
CREATE INDEX idx_videowatchtask_profile_status 
ON video_watch_task_table(profile_id, verified_status);

-- If querying recent watch events
CREATE INDEX idx_watchevent_created_at 
ON watch_event_table(created_at DESC);

-- If doing user analytics
CREATE INDEX idx_subscriberprofile_video_score 
ON profile_table(video_score);
```

---

## Backup & Maintenance

### Important Data:
- All video metadata should be backed up regularly
- WatchEvent records are audit trail - keep indefinitely
- VideoWatchTask state is important for tracking progress

### Cleanup Strategy:
- Archive old WatchEvent records to separate table after 90 days
- Keep VideoWatchTask records indefinitely for historical tracking
- Cache video metadata periodically from YouTube API

---

**Schema Version:** 1.0  
**Created:** 2026-05-01  
**Database:** SQLite (development) / PostgreSQL (production ready)
