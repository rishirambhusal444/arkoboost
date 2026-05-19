import sqlite3
import os
from datetime import datetime

DB = os.path.join(os.getcwd(), "db.sqlite3")
if not os.path.exists(DB):
    print("db.sqlite3 not found at:", DB)
    raise SystemExit(1)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()
# Table names from models: varificatio_image, user_table
print("Connected to DB:", DB)
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in cur.fetchall()]
print("Tables found:")
for t in tables:
    print(" -", t)

if "varificatio_image" not in tables:
    print('\nTable "varificatio_image" not present in DB. Nothing to show.')
    con.close()
    raise SystemExit(0)

try:
    cur.execute("SELECT COUNT(1) FROM varificatio_image")
    total = cur.fetchone()[0]
    print(f"varificatio_image rows: {total}")
except Exception as e:
    print("Failed to count varificatio_image:", e)
    con.close()
    raise

if total == 0:
    print("No verification image rows found.")
    con.close()
    raise SystemExit(0)

try:
    cur.execute("SELECT id, user_id, image, scanned_status, scanned_at, extracted_text, created_at FROM varificatio_image ORDER BY created_at DESC LIMIT 20")
    rows = cur.fetchall()
except Exception as e:
    print("Failed to read varificatio_image:", e)
    con.close()
    raise

for r in rows:
    uid = r["user_id"]
    user = None
    try:
        cur.execute("SELECT id, username, handle, email FROM user_table WHERE id=?", (uid,))
        user = cur.fetchone()
    except Exception:
        user = None

    username = (user["handle"] or user["username"]) if user else f"<user {uid}>"
    scanned = r["scanned_status"]
    scanned_at = r["scanned_at"]
    created = r["created_at"]
    extracted = r["extracted_text"] or ""
    print("---")
    print(f"id: {r['id']}  user: {username} (id={uid})")
    print(f"image: {r['image']}")
    print(f"created_at: {created}")
    print(f"scanned_status: {scanned}  scanned_at: {scanned_at}")
    print("extracted_text:")
    if extracted.strip():
        for line in extracted.splitlines()[:20]:
            print("  ", line)
    else:
        print("  <empty>")

con.close()
