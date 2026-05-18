"""
Management command to assign video watch tasks to users.

Usage:
    # Assign a video to all active users
    python manage.py assign_watch_task --video-id <video_id>
    
    # Assign multiple videos to a specific user
    python manage.py assign_watch_task --user-id <user_id> --video-id <video_id>
    
    # Assign videos with custom minimum watch time
    python manage.py assign_watch_task --video-id <video_id> --min-watch-time 120
    
    # Smart assignment based on user scores
    python manage.py assign_watch_task --smart-assign --max-per-user 3
    
    # List available videos
    python manage.py assign_watch_task --list-videos
    
    # List users to assign to
    python manage.py assign_watch_task --list-users
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from subscribers.models import SubscriberProfile, Video, VideoWatchTask
from subscribers.services import assign_video_from_source_profile


class Command(BaseCommand):
    help = "Assign video watch tasks to users"

    def add_arguments(self, parser):
        parser.add_argument(
            "--video-id",
            type=int,
            help="ID of the video to assign",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="ID of the user to assign to (if not provided, assigns to all active users)",
        )
        parser.add_argument(
            "--min-watch-time",
            type=int,
            default=60,
            help="Minimum watch time in seconds (default: 60)",
        )
        parser.add_argument(
            "--smart-assign",
            action="store_true",
            help="Smart assignment based on user video scores and capacity",
        )
        parser.add_argument(
            "--max-per-user",
            type=int,
            default=3,
            help="Maximum videos to assign per user in smart mode (default: 3)",
        )
        parser.add_argument(
            "--min-score-threshold",
            type=int,
            default=0,
            help="Minimum video score required for assignment (default: 0)",
        )
        parser.add_argument(
            "--source-user-id",
            type=int,
            help="ID of the user who is assigning video watch budget",
        )
        parser.add_argument(
            "--assign-minutes",
            type=int,
            default=0,
            help="Minutes to assign to each target task when source-user-id is used",
        )
        parser.add_argument(
            "--max-targets",
            type=int,
            default=0,
            help="Maximum number of target users to assign in source assignment mode",
        )
        parser.add_argument(
            "--target-min-score",
            type=int,
            default=0,
            help="Minimum video_score required for target users in source assignment mode",
        )
        parser.add_argument(
            "--list-videos",
            action="store_true",
            help="List all available videos",
        )
        parser.add_argument(
            "--list-users",
            action="store_true",
            help="List all users available for assignment",
        )
        parser.add_argument(
            "--bulk-file",
            type=str,
            help="Path to a JSON file with bulk assignments: [{'video_id': 1, 'user_id': 1, 'min_watch_time': 60}, ...]",
        )

    def handle(self, *args, **options):
        if options["list_videos"]:
            self.list_videos()
            return

        if options["list_users"]:
            self.list_users()
            return

        if options["bulk_file"]:
            self.handle_bulk_file(options["bulk_file"])
            return

        if options["source_user_id"]:
            if options["assign_minutes"] <= 0:
                raise CommandError("--assign-minutes must be greater than 0 when using --source-user-id")

            try:
                source_profile = SubscriberProfile.objects.get(user_id=options["source_user_id"])
            except SubscriberProfile.DoesNotExist:
                raise CommandError(f"Source user with ID {options['source_user_id']} not found")

            assigned_count = self.assign_from_source(
                source_profile=source_profile,
                video=Video.objects.get(id=options["video_id"]),
                target_profile_id=options["user_id"],
                assign_minutes=options["assign_minutes"],
                max_targets=options["max_targets"],
                target_min_score=options["target_min_score"],
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Source assignment complete: {assigned_count} tasks created"
                )
            )
            return

        if options["smart_assign"]:
            self.smart_assign_videos(
                max_per_user=options["max_per_user"],
                min_score_threshold=options["min_score_threshold"]
            )
            return

        # Validate required arguments
        if not options["video_id"]:
            raise CommandError("--video-id is required (or use --list-videos to see available videos)")

        # Get the video
        try:
            video = Video.objects.get(id=options["video_id"])
        except Video.DoesNotExist:
            raise CommandError(f"Video with ID {options['video_id']} not found")

        min_watch_time = options["min_watch_time"]

        # Determine which users to assign to
        if options["user_id"]:
            # Assign to specific user
            try:
                profile = SubscriberProfile.objects.get(user_id=options["user_id"])
                profiles = [profile]
            except SubscriberProfile.DoesNotExist:
                raise CommandError(f"User with ID {options['user_id']} not found")
        else:
            # Assign to all active users
            profiles = SubscriberProfile.objects.filter(active_status=True)

        if not profiles.exists():
            self.stdout.write(
                self.style.WARNING("No active users found to assign tasks to")
            )
            return

        # Assign tasks
        assigned_count = 0
        skipped_count = 0

        with transaction.atomic():
            for profile in profiles:
                # Check if task already exists
                existing_task = VideoWatchTask.objects.filter(
                    profile=profile, video=video
                ).first()

                if existing_task:
                    skipped_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Task already exists for {profile.handle} -> {video.title}"
                        )
                    )
                    continue

                # Create new task
                task = VideoWatchTask.objects.create(
                    profile=profile,
                    video=video,
                    min_watch_time_seconds=min_watch_time,
                )

                assigned_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Assigned {video.title} to {profile.handle}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Assignment complete: {assigned_count} tasks created, {skipped_count} skipped"
            )
        )

    def smart_assign_videos(self, max_per_user=3, min_score_threshold=0):
        """Smart assignment based on user video scores and capacity"""
        self.stdout.write("🎯 Starting smart video assignment...")

        # Get all available videos
        videos = list(Video.objects.all().order_by("-created_at"))
        if not videos:
            self.stdout.write(self.style.WARNING("No videos available for assignment"))
            return

        # Get eligible users based on score criteria
        eligible_profiles = SubscriberProfile.objects.filter(
            active_status=True,
            video_score__gte=min_score_threshold
        ).order_by("-video_score", "-video_score_reserved")  # Higher scores first

        if not eligible_profiles.exists():
            self.stdout.write(
                self.style.WARNING(f"No eligible users found (min score: {min_score_threshold})")
            )
            return

        self.stdout.write(f"📊 Found {len(videos)} videos and {eligible_profiles.count()} eligible users")

        assigned_count = 0
        skipped_count = 0

        with transaction.atomic():
            for profile in eligible_profiles:
                # Check how many tasks this user already has
                current_tasks_count = VideoWatchTask.objects.filter(
                    profile=profile,
                    verified_status=False  # Only count uncompleted tasks
                ).count()

                if current_tasks_count >= max_per_user:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ⏭️  Skipping {profile.handle} - already has {current_tasks_count} pending tasks"
                        )
                    )
                    continue

                # Calculate how many more tasks this user can take
                tasks_to_assign = max_per_user - current_tasks_count
                if tasks_to_assign <= 0:
                    continue

                # Get videos this user doesn't already have tasks for
                existing_video_ids = set(
                    VideoWatchTask.objects.filter(profile=profile)
                    .values_list('video_id', flat=True)
                )

                available_videos = [v for v in videos if v.id not in existing_video_ids]

                if not available_videos:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ⏭️  Skipping {profile.handle} - no new videos available"
                        )
                    )
                    continue

                # Assign videos to this user
                assigned_to_user = 0
                for video in available_videos[:tasks_to_assign]:
                    # Create task with score-based watch time
                    # Higher scoring users get longer watch requirements
                    base_watch_time = 60  # 1 minute base
                    score_bonus = min(profile.video_score // 10, 5)  # Max 5 minutes bonus
                    min_watch_time = base_watch_time + (score_bonus * 60)

                    task = VideoWatchTask.objects.create(
                        profile=profile,
                        video=video,
                        min_watch_time_seconds=min_watch_time,
                    )

                    assigned_to_user += 1
                    assigned_count += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Assigned '{video.title}' to {profile.handle} "
                            f"(Score: {profile.video_score}, Watch: {min_watch_time}s)"
                        )
                    )

                if assigned_to_user == 0:
                    skipped_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n🎯 Smart assignment complete: {assigned_count} tasks created, {skipped_count} users skipped"
            )
        )
        self.stdout.write(
            f"📈 Assignment prioritized users by video score (min: {min_score_threshold})"
        )

    def assign_from_source(self, source_profile, video, target_profile_id, assign_minutes, max_targets, target_min_score):
        target_qs = SubscriberProfile.objects.filter(active_status=True).exclude(id=source_profile.id)
        if target_profile_id:
            target_qs = target_qs.filter(user_id=target_profile_id)

        if not target_qs.exists():
            self.stdout.write(self.style.WARNING("No eligible target users found for source assignment."))
            return 0

        max_targets = max_targets or None
        assigned_count = assign_video_from_source_profile(
            source_profile=source_profile,
            source_user=source_profile.user,
            video=video,
            target_profiles=list(target_qs.order_by('-updated_at')[:max_targets] if max_targets else target_qs.order_by('-updated_at')),
            minutes_per_target=assign_minutes,
            max_targets=max_targets,
        )

        return assigned_count

    def handle(self, *args, **options):
        if options["list_videos"]:
            self.list_videos()
            return

        if options["list_users"]:
            self.list_users()
            return

        if options["bulk_file"]:
            self.handle_bulk_file(options["bulk_file"])
            return

        # Validate required arguments
        if not options["video_id"]:
            raise CommandError("--video-id is required (or use --list-videos to see available videos)")

        # Get the video
        try:
            video = Video.objects.get(id=options["video_id"])
        except Video.DoesNotExist:
            raise CommandError(f"Video with ID {options['video_id']} not found")

        min_watch_time = options["min_watch_time"]

        # Determine which users to assign to
        if options["user_id"]:
            # Assign to specific user
            try:
                profile = SubscriberProfile.objects.get(user_id=options["user_id"])
                profiles = [profile]
            except SubscriberProfile.DoesNotExist:
                raise CommandError(f"User with ID {options['user_id']} not found")
        else:
            # Assign to all active users
            profiles = SubscriberProfile.objects.filter(active_status=True)

        if not profiles.exists():
            self.stdout.write(
                self.style.WARNING("No active users found to assign tasks to")
            )
            return

        # Assign tasks
        assigned_count = 0
        skipped_count = 0

        with transaction.atomic():
            for profile in profiles:
                # Check if task already exists
                existing_task = VideoWatchTask.objects.filter(
                    profile=profile, video=video
                ).first()

                if existing_task:
                    skipped_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Task already exists for {profile.handle} -> {video.title}"
                        )
                    )
                    continue

                # Create new task
                task = VideoWatchTask.objects.create(
                    profile=profile,
                    video=video,
                    min_watch_time_seconds=min_watch_time,
                )

                assigned_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Assigned {video.title} to {profile.handle}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Assignment complete: {assigned_count} tasks created, {skipped_count} skipped"
            )
        )

    def list_videos(self):
        """List all available videos"""
        videos = Video.objects.all().order_by("-created_at")

        if not videos.exists():
            self.stdout.write(self.style.WARNING("No videos found in the database"))
            return

        self.stdout.write(self.style.SUCCESS("\n=== Available Videos ===\n"))

        for video in videos:
            self.stdout.write(
                f"ID: {video.id:4} | {video.title[:50]:50} | {video.channel_title[:30]:30}"
            )
            self.stdout.write(
                f"       Video ID: {video.youtube_video_id} | Duration: {video.duration_seconds}s | Views: {video.view_count}\n"
            )

    def list_users(self):
        """List all users available for assignment"""
        profiles = SubscriberProfile.objects.select_related("user").all().order_by("-updated_at")

        if not profiles.exists():
            self.stdout.write(self.style.WARNING("No user profiles found"))
            return

        self.stdout.write(self.style.SUCCESS("\n=== Available Users ===\n"))

        for profile in profiles:
            status = "ACTIVE" if profile.active_status else "INACTIVE"
            task_count = profile.video_watch_tasks.count()
            completed_count = profile.video_watch_tasks.filter(verified_status=True).count()

            self.stdout.write(
                f"User ID: {profile.user_id:4} | Handle: {profile.handle or 'N/A':20} | Status: {status:8} | "
                f"Tasks: {task_count} (Completed: {completed_count})"
            )

    def handle_bulk_file(self, file_path):
        """Handle bulk assignments from JSON file"""
        import json

        try:
            with open(file_path, "r") as f:
                assignments = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"File not found: {file_path}")
        except json.JSONDecodeError:
            raise CommandError(f"Invalid JSON in file: {file_path}")

        if not isinstance(assignments, list):
            raise CommandError("JSON file must contain a list of assignments")

        total_assigned = 0
        total_skipped = 0

        self.stdout.write(self.style.SUCCESS(f"\nProcessing {len(assignments)} assignments...\n"))

        with transaction.atomic():
            for assignment in assignments:
                video_id = assignment.get("video_id")
                user_id = assignment.get("user_id")
                min_watch_time = assignment.get("min_watch_time", 60)

                if not video_id or not user_id:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  Skipping invalid assignment: {assignment} (missing video_id or user_id)"
                        )
                    )
                    continue

                try:
                    video = Video.objects.get(id=video_id)
                    profile = SubscriberProfile.objects.get(user_id=user_id)
                except Video.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(f"  Video with ID {video_id} not found")
                    )
                    continue
                except SubscriberProfile.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(f"  User with ID {user_id} not found")
                    )
                    continue

                # Check if task already exists
                existing_task = VideoWatchTask.objects.filter(
                    profile=profile, video=video
                ).first()

                if existing_task:
                    total_skipped += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Task already exists for User {user_id} -> Video {video_id}"
                        )
                    )
                    continue

                # Create new task
                VideoWatchTask.objects.create(
                    profile=profile,
                    video=video,
                    min_watch_time_seconds=min_watch_time,
                )

                total_assigned += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Assigned {video.title} to {profile.handle}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Bulk assignment complete: {total_assigned} tasks created, {total_skipped} skipped"
            )
        )
