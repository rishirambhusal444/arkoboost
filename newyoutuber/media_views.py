import mimetypes
import os
import re

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils._os import safe_join


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def serve_media(request, path):
    try:
        full_path = safe_join(settings.MEDIA_ROOT, path)
    except ValueError as exc:
        raise Http404("Media file not found") from exc

    if not os.path.isfile(full_path):
        raise Http404("Media file not found")

    file_size = os.path.getsize(full_path)
    content_type, _ = mimetypes.guess_type(full_path)
    content_type = content_type or "application/octet-stream"
    range_header = request.headers.get("Range", "")
    range_match = RANGE_RE.match(range_header)

    if not range_match:
        response = FileResponse(open(full_path, "rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(file_size)
        return response

    first, last = range_match.groups()
    start = int(first) if first else 0
    end = int(last) if last else file_size - 1
    end = min(end, file_size - 1)

    if start > end or start >= file_size:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{file_size}"
        return response

    length = end - start + 1
    file_obj = open(full_path, "rb")
    file_obj.seek(start)
    response = FileResponse(file_obj, status=206, content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(length)
    response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    return response
