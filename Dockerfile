FROM python:3.11-slim

# Install system dependencies including Tesseract OCR
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libtesseract-dev tesseract-ocr tesseract-ocr-eng \
       libleptonica-dev \
       pkg-config \
       git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application
COPY . /app

# Collect static (optional; may require settings adjustments)
RUN python manage.py collectstatic --noinput || true

ENV PORT=8000
EXPOSE 8000

CMD ["gunicorn", "newyoutuber.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
