from django.core.management.base import BaseCommand

from subscribers.superuser import ensure_startup_superuser


class Command(BaseCommand):
    help = "Create the configured superuser if it does not already exist."

    def handle(self, *args, **kwargs):
        message = ensure_startup_superuser()
        if "skipped" in message.lower():
            self.stdout.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS(message))
