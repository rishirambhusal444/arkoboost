from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import sys
import traceback
from datetime import datetime

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from subscribers.models import VerificationImage
from subscribers.ocr import get_ocr_text


def process_row(row_id):
    try:
        row = VerificationImage.objects.get(id=row_id)
        # read file from storage path
        path = row.image.path
        with open(path, "rb") as f:
            # get_ocr_text expects a Django UploadedFile-like object; pass raw bytes wrapped
            class B:
                def __init__(self, data, path):
                    self._data = data
                    self.name = os.path.basename(path)
                    self.size = len(data)
                    self.content_type = "image/unknown"
                def read(self):
                    return self._data
                def seek(self, pos):
                    return
            uploaded = B(f.read(), path)
        text = get_ocr_text(uploaded)
        row.extracted_text = text or ""
        row.scanned_status = True
        row.scanned_at = timezone.now()
        row.save(update_fields=["extracted_text", "scanned_status", "scanned_at", "updated_at"])
        return (row_id, True, None)
    except Exception as exc:
        return (row_id, False, str(exc) + "\n" + traceback.format_exc())


class Command(BaseCommand):
    help = "Batch OCR verification images and save extracted text to VerificationImage rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workers",
            type=int,
            default=4,
            help="Number of worker threads to use for OCR (default: 4)",
        )
        parser.add_argument(
            "--from-files",
            action="store_true",
            help="Create VerificationImage rows for files in MEDIA_ROOT/verification_images not already in DB. Requires --user-id or existing superuser.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="User id to associate when creating rows from files (required if --from-files and no superuser found).",
        )

    def handle(self, *args, **options):
        workers = max(1, options.get("workers") or 1)
        from_files = options.get("from_files")
        user_id = options.get("user_id")

        # If requested, scan media folder and create missing DB rows
        media_dir = os.path.join(getattr(settings, "MEDIA_ROOT", ""), "verification_images")
        if from_files:
            if not os.path.isdir(media_dir):
                self.stdout.write(self.style.ERROR(f"Media verification_images folder not found: {media_dir}"))
                return
            existing = set(VerificationImage.objects.values_list("image", flat=True))
            created = 0
            # attempt to find a superuser if user_id not provided
            if not user_id:
                from django.contrib.auth import get_user_model

                User = get_user_model()
                su = User.objects.filter(is_superuser=True).first()
                if su:
                    user_id = su.id
            if not user_id:
                self.stdout.write(self.style.ERROR("No user id available for creating VerificationImage rows. Provide --user-id or create a superuser."))
                return

            for fname in os.listdir(media_dir):
                rel = os.path.join("verification_images", fname)
                if rel in existing:
                    continue
                VerificationImage.objects.create(user_id=user_id, image=rel)
                created += 1
            self.stdout.write(self.style.SUCCESS(f"Created {created} VerificationImage rows from files in {media_dir}"))

        qs = VerificationImage.objects.filter(scanned_status=False).order_by("created_at")
        total = qs.count()
        if total == 0:
            self.stdout.write("No unscanned VerificationImage rows found.")
            return

        self.stdout.write(f"Processing {total} images with {workers} workers...")

        ids = list(qs.values_list("id", flat=True))
        successes = 0
        failures = 0

        if workers == 1:
            for rid in ids:
                rid, ok, err = process_row(rid)
                if ok:
                    successes += 1
                    self.stdout.write(self.style.SUCCESS(f"{rid} ok"))
                else:
                    failures += 1
                    self.stdout.write(self.style.ERROR(f"{rid} failed: {err}"))
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(process_row, rid): rid for rid in ids}
                for fut in as_completed(futures):
                    rid = futures[fut]
                    try:
                        rid, ok, err = fut.result()
                        if ok:
                            successes += 1
                            self.stdout.write(self.style.SUCCESS(f"{rid} ok"))
                        else:
                            failures += 1
                            self.stdout.write(self.style.ERROR(f"{rid} failed: {err}"))
                    except Exception as e:
                        failures += 1
                        self.stdout.write(self.style.ERROR(f"{rid} exception: {e}\n{traceback.format_exc()}"))

        self.stdout.write(self.style.SUCCESS(f"Done. successes={successes}, failures={failures}"))
