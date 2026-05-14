from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0018_subscriberprofile_active_status"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="topusersubscribetask",
            name="top_usr_prof_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="topusersubscribetask",
            name="top_usr_tgt_status_idx",
        ),
        migrations.RemoveField(
            model_name="topusersubscribetask",
            name="status",
        ),
    ]
