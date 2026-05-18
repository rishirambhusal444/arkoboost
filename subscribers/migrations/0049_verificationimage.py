from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("subscribers", "0048_manualsubscribetaskassign_active_status_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="VerificationImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.FileField(upload_to="verification_images/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verification_images", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "db_table": "varificatio_image",
                "ordering": ["-created_at"],
            },
        ),
    ]
