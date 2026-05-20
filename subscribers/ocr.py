import io
import os
import base64
import json
import logging
import hashlib
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.cache import cache
from PIL import Image, ImageEnhance, ImageOps

logger = logging.getLogger(__name__)


def _preprocess_image_bytes(data: bytes, max_size: int = 1280) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        logger.exception("Failed opening image for OCR preprocessing")
        return None

    try:
        # Convert to RGB (some screenshots may be RGBA) and limit size
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Resize while keeping aspect ratio to limit memory use
        img.thumbnail((max_size, max_size), Image.LANCZOS)

        # Convert to grayscale to simplify OCR unless color is needed
        gray = ImageOps.grayscale(img)

        # Improve contrast moderately
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(1.3)

        # Apply a light binary threshold to reduce background noise
        try:
            gray = gray.point(lambda x: 0 if x < 120 else 255, mode="1")
        except Exception:
            # If thresholding fails, fallback to the enhanced grayscale
            gray = gray.convert("L")

        return gray
    except Exception:
        logger.exception("Image preprocessing failed")
        return None


def get_ocr_text(uploaded_file) -> str:
    """Run lightweight OCR for screenshots via OCR.Space only."""
    try:
        data = uploaded_file.read()
    except Exception:
        # Some file-like objects expose .file
        try:
            data = uploaded_file.file.read()
        except Exception:
            data = b""

    # Restore seek if possible so Django can re-use the file later
    try:
        uploaded_file.seek(0)
    except Exception:
        try:
            uploaded_file.file.seek(0)
        except Exception:
            pass

    ocrspace_key = (os.environ.get("OCRSPACE_API_KEY") or os.environ.get("OCR_SPACE_API_KEY") or "").strip()
    if not ocrspace_key:
        logger.warning("OCR API key missing. Set OCRSPACE_API_KEY (or OCR_SPACE_API_KEY).")
        return ""

    img = _preprocess_image_bytes(data)
    if img is None:
        return ""

    try:
        out = io.BytesIO()
        img.convert("L").save(out, format="JPEG", optimize=True, quality=72)
        payload_bytes = out.getvalue()
        cache_key = f"ocrspace:v1:{hashlib.sha256(payload_bytes).hexdigest()}"
        cached_text = cache.get(cache_key)
        if cached_text is not None:
            return (cached_text or "")[:5000]

        payload = {
            "apikey": ocrspace_key,
            "language": "eng",
            "isOverlayRequired": "false",
            "OCREngine": "2",
            "isTable": "false",
            "scale": "true",
            "base64Image": f"data:image/jpeg;base64,{base64.b64encode(payload_bytes).decode('ascii')}",
        }
        body = urlencode(payload).encode("utf-8")
        last_err = None
        for timeout in (15, 25):
            try:
                req = Request(
                    "https://api.ocr.space/parse/image",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urlopen(req, timeout=timeout) as resp:
                    parsed = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
                if parsed.get("IsErroredOnProcessing"):
                    err_msg = "; ".join(parsed.get("ErrorMessage") or []) or "unknown OCR error"
                    last_err = RuntimeError(err_msg)
                    continue
                results = parsed.get("ParsedResults") or []
                text = "\n".join((row.get("ParsedText") or "").strip() for row in results if row.get("ParsedText"))
                text = (text or "")[:5000]
                cache.set(cache_key, text, timeout=60 * 60 * 24)
                return text
            except Exception as ex:
                last_err = ex
                continue
        if last_err:
            raise last_err
        return ""
    except Exception:
        logger.exception("OCR.Space OCR failed")
        return ""
