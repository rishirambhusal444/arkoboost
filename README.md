# newyoutubers

Minimal Django project to:
- let user connect Google account
- scan the user's YouTube subscriptions using YouTube Data API v3
- store scan results in a profile

## Run in VS Code (Windows PowerShell)

1. Open folder:
```powershell
cd C:\Users\SSS\Desktop\running_projects\newyoutubers-main
code .
```

2. Create virtual env:
```powershell
python -m venv .venv --system-site-packages --without-pip
```

3. Run Django commands using `.venv` python:
```powershell
.\.venv\Scripts\python manage.py check
```

4. Set environment variables (replace values):
```powershell

$env:YOUTUBE_REDIRECT_URI="http://127.0.0.1:8000/auth/google/callback/"
$env:YOUTUBE_TARGET_CHANNEL_ID="UC-7jV2U-Y0-z_V-Y5-Y-Y0A" # Replace with your target channel ID
```

5. Run migrations:
```powershell
.\.venv\Scripts\python manage.py makemigrations
.\.venv\Scripts\python manage.py migrate
```

6. Start app:
```powershell
.\.venv\Scripts\python manage.py runserver
```

7. Open:
`http://127.0.0.1:8000/`

## Google Cloud setup notes

- Enable **YouTube Data API v3**.
- Configure OAuth consent screen.
- Add redirect URI:
  `http://127.0.0.1:8000/auth/google/callback/`
- Use scope:
  `https://www.googleapis.com/auth/youtube.readonly`

## Lightweight OCR for Render Free Tier

- Set `OCRSPACE_API_KEY` in Render environment variables (free key from OCR.Space).
- Optional compatibility alias: `OCR_SPACE_API_KEY` (either one works).
- OCR is now API-only via OCR.Space (no local Tesseract fallback).
- Images are preprocessed/compressed, OCR requests retry on timeout, and duplicate images are cached.
# arkoboost
