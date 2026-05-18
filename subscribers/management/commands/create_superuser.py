import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        User = get_user_model()

        username = "superadmin"
        email = "Superadmin@admin.com"
        password = "Superadmin@123"

        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            print("✅ Superuser created")
        else:
            print("ℹ️ Superuser already exists")