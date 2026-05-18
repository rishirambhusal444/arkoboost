from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_manual_profile_user_and_category(apps, schema_editor):
    ManualSubscribeProfile = apps.get_model("subscribers", "ManualSubscribeProfile")
    for row in ManualSubscribeProfile.objects.select_related("profile", "profile__user").all():
        updates = []
        if getattr(row, "user_id", None) is None and getattr(row, "profile_id", None):
            row.user_id = row.profile.user_id
            updates.append("user_id")
        if not getattr(row, "category", ""):
            row.category = getattr(row.profile, "category", "other") or "other"
            updates.append("category")
        if updates:
            row.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0052_manualsubscribeprofile_loyal_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="manualsubscribeprofile",
            name="category",
            field=models.CharField(
                choices=[
                    ("education", "Education"),
                    ("entertainment", "Entertainment"),
                    ("sports", "Sports"),
                    ("technology", "Technology"),
                    ("music", "Music"),
                    ("gaming", "Gaming"),
                    ("lifestyle", "Lifestyle"),
                    ("news", "News"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="other",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="manualsubscribeprofile",
            name="last_tasks_entry_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="manualsubscribeprofile",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="manual_subscribe_profile_user",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_manual_profile_user_and_category, migrations.RunPython.noop),
    ]
