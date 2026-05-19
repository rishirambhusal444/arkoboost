from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0061_manual_profile_total_verified_remove_sub_reserve"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualFacebookProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("page_name", models.CharField(blank=True, db_index=True, max_length=255)),
                ("profile_url", models.URLField(blank=True)),
                ("follow_score", models.PositiveIntegerField(default=0)),
                ("total_verified", models.PositiveIntegerField(default=0)),
                ("loyal_score", models.PositiveIntegerField(default=0)),
                ("active_status_for_follow", models.BooleanField(db_index=True, default=False)),
                ("last_tasks_entry_at", models.DateTimeField(db_index=True, null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="manual_facebook_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "manual_facebook_profile",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.CreateModel(
            name="ManualFacebookFollowTaskAssign",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "followed_status",
                    models.CharField(
                        choices=[
                            ("assigned", "Assigned"),
                            ("unverified", "Unverified"),
                            ("verified", "Verified"),
                            ("released", "Released"),
                        ],
                        db_index=True,
                        default="assigned",
                        max_length=16,
                    ),
                ),
                ("active_status", models.BooleanField(db_index=True, default=False)),
                ("clicked_follow_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manual_facebook_profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="follow_task_assignments",
                        to="subscribers.manualfacebookprofile",
                    ),
                ),
                (
                    "target_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="target_follow_task_assignments",
                        to="subscribers.manualfacebookprofile",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="manual_facebook_follow_task_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "manual_facebook_follow_task_assign",
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="manualfacebookprofile",
            index=models.Index(
                fields=["active_status_for_follow", "last_tasks_entry_at"],
                name="manfb_active_last_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualfacebookprofile",
            index=models.Index(
                fields=["active_status_for_follow", "follow_score"],
                name="manfb_active_score_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualfacebookfollowtaskassign",
            index=models.Index(
                fields=["user", "target_profile", "followed_status"],
                name="manfb_task_pair_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualfacebookfollowtaskassign",
            index=models.Index(
                fields=["followed_status", "user"],
                name="manfb_task_state_owner_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="manualfacebookfollowtaskassign",
            constraint=models.UniqueConstraint(
                fields=("user", "target_profile"),
                name="unique_manual_facebook_follow_task",
            ),
        ),
    ]
