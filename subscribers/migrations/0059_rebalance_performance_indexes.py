from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0058_rename_subscriberprofile_table"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="subscriberprofile",
            index=models.Index(
                fields=["active_status", "last_tasks_entry_at"],
                name="subprof_active_last_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="subscriberprofile",
            index=models.Index(
                fields=["active_status", "score"],
                name="subprof_active_score_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="topusersubscribetask",
            index=models.Index(
                fields=["profile", "target_profile", "verified_status"],
                name="top_task_pair_ver_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="topusersubscribetask",
            index=models.Index(
                fields=["verified_status", "profile"],
                name="top_task_state_owner_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualsubscribeprofile",
            index=models.Index(
                fields=["active_status_for_subscribe", "last_tasks_entry_at"],
                name="manprof_active_last_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualsubscribeprofile",
            index=models.Index(
                fields=["active_status_for_subscribe", "sub_score"],
                name="manprof_active_score_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualsubscribetaskassign",
            index=models.Index(
                fields=["user", "target_profile", "subscribed_status"],
                name="man_task_pair_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manualsubscribetaskassign",
            index=models.Index(
                fields=["subscribed_status", "user"],
                name="man_task_state_owner_idx",
            ),
        ),
    ]
