import os

from django.contrib.auth import get_user_model
from django.db import OperationalError, ProgrammingError


def ensure_startup_superuser():
    username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "superadmin")
    email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
    password = os.environ.get("DJANGO_SUPERUSER_PASSWORD") or "Superadmin@123"

    if not password:
        return "DJANGO_SUPERUSER_PASSWORD is not set; skipping superuser creation."

    User = get_user_model()

    try:
        user = User.objects.filter(username=username).first()
        if user is None:
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password,
            )
            return "Superuser created"

        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save(update_fields=["email", "is_staff", "is_superuser", "is_active", "password"])
        return "Superuser updated"
    except (OperationalError, ProgrammingError) as exc:
        return f"Superuser setup skipped until database is ready: {exc}"
