from django.core.management.base import BaseCommand
from django.utils import timezone

from subscribers.models import ManualSubscribeProfile, ManualSubscribeTaskAssign, SubscriberProfile
from subscribers.views import ACTIVITY_WINDOW_MINUTES, MANUAL_PENDING_STATUSES


class Command(BaseCommand):
    help = "Print eligibility counts for manual YouTube subscribe tasks."

    def handle(self, *args, **options):
        now = timezone.now()
        window_start = now - timezone.timedelta(minutes=ACTIVITY_WINDOW_MINUTES)

        manual_profiles = ManualSubscribeProfile.objects.select_related("user")
        active_receivers = manual_profiles.filter(
            user__is_active=True,
            active_status_for_subscribe=True,
            last_tasks_entry_at__gte=window_start,
        )
        eligible_targets = manual_profiles.filter(
            user__is_active=True,
            sub_score__gt=0,
            handle__startswith="@",
        )
        google_score_targets = SubscriberProfile.objects.filter(
            user__is_active=True,
            score__gt=0,
            handle__startswith="@",
        )
        pending_tasks = ManualSubscribeTaskAssign.objects.filter(
            subscribed_status__in=MANUAL_PENDING_STATUSES
        )

        self.stdout.write(f"manual_profiles={manual_profiles.count()}")
        self.stdout.write(f"active_receivers_last_{ACTIVITY_WINDOW_MINUTES}m={active_receivers.count()}")
        self.stdout.write(f"eligible_manual_targets_sub_score_gt_0={eligible_targets.count()}")
        self.stdout.write(f"google_profiles_score_gt_0={google_score_targets.count()}")
        self.stdout.write(f"pending_manual_tasks={pending_tasks.count()}")

        self.stdout.write("")
        self.stdout.write("Top manual targets:")
        for row in eligible_targets.order_by("-sub_score", "id")[:10]:
            self.stdout.write(
                f"  user_id={row.user_id} username={row.user.username if row.user else '-'} "
                f"handle={row.handle or '-'} sub_score={row.sub_score} "
                f"active={row.active_status_for_subscribe}"
            )

        self.stdout.write("")
        self.stdout.write("Top google-score profiles:")
        for row in google_score_targets.order_by("-score", "id")[:10]:
            self.stdout.write(
                f"  user_id={row.user_id} username={row.user.username} "
                f"handle={row.handle or '-'} score={row.score}"
            )
