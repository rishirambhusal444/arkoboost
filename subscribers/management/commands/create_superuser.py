import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the configured superuser if it does not already exist."

    def handle(self, *args, **kwargs):
        User = get_user_model()

        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "superadmin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD") or "Superadmin@123"

        if not password:
            self.stdout.write(
                self.style.WARNING(
                    "DJANGO_SUPERUSER_PASSWORD is not set; skipping superuser creation."
                )
            )
            return

        user = User.objects.filter(username=username).first()

        if user is None:
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password,
            )
            self.stdout.write(self.style.SUCCESS("Superuser created"))
        else:
            user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            user.set_password(password)
            user.save(update_fields=["email", "is_staff", "is_superuser", "is_active", "password"])
            self.stdout.write(self.style.SUCCESS("Superuser updated"))
