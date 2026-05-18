from django.db import migrations, models


def backfill_user_account_mode(apps, schema_editor):
    User = apps.get_model("subscribers", "User")
    SubscriberProfile = apps.get_model("subscribers", "SubscriberProfile")

    google_user_ids = set(
        SubscriberProfile.objects.filter(google_subject_id__isnull=False)
        .exclude(google_subject_id="")
        .values_list("user_id", flat=True)
    )
    if google_user_ids:
        User.objects.filter(id__in=google_user_ids).update(account_mode="google")
    User.objects.exclude(id__in=google_user_ids).update(account_mode="manual")


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0055_remove_manualsubscribeprofile_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="account_mode",
            field=models.CharField(
                choices=[("manual", "Manual"), ("google", "Google")],
                db_index=True,
                default="manual",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_user_account_mode, migrations.RunPython.noop),
    ]

