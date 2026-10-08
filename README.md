# Dr. Dev Ads - backend (Flask, Render free)

## 1. Firebase Realtime Database
Create a Realtime Database, set rules to `{ "rules": { ".read": true, ".write": true } }` and copy the URL
(`https://YOUR-PROJECT-default-rtdb.firebaseio.com`). The URL stays on the server only.
Meta tokens are AES-encrypted before they are written, so open rules do not expose them.
Anyone who learns the URL could still create licenses, so keep it private (or lock the rules and set FIREBASE_SECRET later).

## 2. Deploy on Render
Push this folder to GitHub, then New > Web Service:
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn main:app --workers 1 --threads 8 --timeout 180`
- Environment variables: see `.env.example` (FIREBASE_DB_URL, APP_SECRET, ADMIN_PASSWORD are required)
- Never change APP_SECRET after users have saved credentials (they would have to re-enter them).

Free instances sleep after ~15 min. The app shows "Waking up the server" for the first ~50 s.
Ping `/health` every 10 min with UptimeRobot to keep it awake.

## 3. Use it
- Admin panel: `https://YOUR-SERVICE.onrender.com/admin` (user `admin`, your ADMIN_PASSWORD)
- Put the same URL in the Colab notebook as BACKEND_URL.
- Optional: paste the signing SHA-256 printed by the notebook into ALLOWED_APP_SIGS to reject repackaged APKs.

## Testing without a Meta account
Open `/admin`, sign in, go to **Demo data** and create a data account (it comes with a sample campaign and 45 days of numbers).
Then create a license in **Licenses**, pick the data account in the dropdown, add the user's name and profile icon URL.
That license opens the app straight into the data account (no Meta token needed). Edit campaigns, daily numbers, the spending limit and
the opportunity score in the admin site, then reopen the app to see them.
