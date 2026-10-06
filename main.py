"""
Dr. Dev Ads Manager - backend API
Flask app for Render (free web service). Data is stored in Firebase Realtime Database
through its REST interface (/path.json). The database URL lives ONLY in this server's
environment variables - the Android app never sees it.

Security model
- The app never talks to Meta directly. Every Meta call goes through /api/meta/... and needs a
  signed session token that is only issued after a valid license check. If someone mods the APK
  and deletes the license screen, there is no token, so nothing works.
- Meta credentials are encrypted (Fernet/AES) before being written to Firebase, so even with
  open database rules the stored tokens are unreadable without APP_SECRET.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import requests
import demo
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

# ----------------------------------------------------------------------------- config
DB_URL = os.environ.get("FIREBASE_DB_URL", "").rstrip("/")
DB_SECRET = os.environ.get("FIREBASE_SECRET", "")          # optional (only if you lock DB rules)
APP_SECRET = os.environ.get("APP_SECRET", "")               # long random string, REQUIRED
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")       # REQUIRED for /admin
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v25.0")
ALLOWED_SIGS = {
    s.strip().upper().replace(":", "")
    for s in os.environ.get("ALLOWED_APP_SIGS", "").split(",")
    if s.strip()
}                                                           # optional APK signing-cert SHA-256s

DEMO_ENABLED = os.environ.get("ENABLE_DEMO", "1") != "0"   # token "DEMO" = fake Meta data for testing
MAX_FAILS = 3
BLOCK_SECONDS = 24 * 3600
SESSION_TTL = 12 * 3600
IST = timezone(timedelta(hours=5, minutes=30))              # license "valid until" = end of that day (IST)
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_RE = re.compile(r"^[A-Z0-9]{18}$")
DEFAULT_MAINTENANCE_MSG = "The app is under maintenance. Please try again later."
PROXY_PATH_RE = re.compile(r"^(me/adaccounts|me/accounts|search|act_\d+(/[A-Za-z0-9_]+)*|\d+(/[A-Za-z0-9_]+)*)$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
_http = requests.Session()


# ----------------------------------------------------------------------------- helpers
def now() -> int:
    return int(time.time())


def graph(path: str) -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}/{path}"


def dev_hash(device_id: str) -> str:
    return hashlib.sha256(("dev:" + device_id).encode()).hexdigest()[:32]


def gen_key() -> str:
    return "".join(secrets.choice(KEY_ALPHABET) for _ in range(18))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign_key() -> bytes:
    return hashlib.sha256(("sig:" + APP_SECRET).encode()).digest()


_fernet = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        k = base64.urlsafe_b64encode(hashlib.sha256(("enc:" + APP_SECRET).encode()).digest())
        _fernet = Fernet(k)
    return _fernet


# ----------------------------------------------------------------------------- Firebase RTDB (REST)
def _db_url(path: str) -> str:
    return f"{DB_URL}/{path.strip('/')}.json"


def _db_params(extra=None) -> dict:
    p = dict(extra or {})
    if DB_SECRET:
        p["auth"] = DB_SECRET
    return p


def db_get(path: str):
    r = _http.get(_db_url(path), params=_db_params(), timeout=15)
    r.raise_for_status()
    return r.json()


def db_put(path: str, data):
    r = _http.put(_db_url(path), params=_db_params(), data=json.dumps(data), timeout=15)
    r.raise_for_status()
    return r.json()


def db_patch(path: str, data: dict):
    r = _http.patch(_db_url(path), params=_db_params(), data=json.dumps(data), timeout=15)
    r.raise_for_status()
    return r.json()


def db_delete(path: str):
    r = _http.delete(_db_url(path), params=_db_params(), timeout=15)
    r.raise_for_status()


# ----------------------------------------------------------------------------- tiny TTL cache
_cache: dict = {}
_cache_lock = threading.Lock()


def cache_get(key: str, ttl: int):
    with _cache_lock:
        item = _cache.get(key)
    if item and time.time() - item[0] < ttl:
        return item[1]
    return None


def cache_set(key: str, value):
    with _cache_lock:
        _cache[key] = (time.time(), value)


def cache_drop(key: str):
    with _cache_lock:
        _cache.pop(key, None)


def get_config() -> dict:
    c = cache_get("config", 8)
    if c is None:
        c = db_get("config") or {}
        cache_set("config", c)
    return {
        "maintenance": bool(c.get("maintenance")),
        "message": (c.get("message") or DEFAULT_MAINTENANCE_MSG),
    }


def get_license(key: str):
    ck = "lic:" + key
    lic = cache_get(ck, 15)
    if lic is None:
        lic = db_get(f"licenses/{key}") or {}
        cache_set(ck, lic)
    return lic or None


def drop_license_cache(key: str):
    cache_drop("lic:" + key)


def maintenance_response(cfg: dict):
    return jsonify(error="maintenance", maintenance=True, message=cfg["message"]), 503


def license_problem(lic, device_hash: str):
    """Return a human message if this license cannot be used by this device, else None."""
    if not lic:
        return "Invalid license key."
    if lic.get("revoked"):
        return "This license has been revoked. Contact @drdevsupport."
    if lic.get("device") and lic["device"] != device_hash:
        return "This license key is already used on another device."
    if int(lic.get("expires_at", 0)) <= now():
        return "Your license has expired. Contact @drdevsupport to renew."
    return None


# ----------------------------------------------------------------------------- session tokens
def make_token(key: str, device_hash: str) -> str:
    payload = _b64(json.dumps({"k": key, "d": device_hash, "e": now() + SESSION_TTL}).encode())
    sig = _b64(hmac.new(_sign_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_token(token: str):
    try:
        payload, sig = token.split(".", 1)
        good = _b64(hmac.new(_sign_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(payload))
        if int(data["e"]) < now():
            return None
        return data
    except Exception:
        return None


def session_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        cfg = get_config()
        if cfg["maintenance"]:
            return maintenance_response(cfg)
        auth = request.headers.get("Authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else ""
        data = read_token(tok)
        if not data:
            return jsonify(error="auth", message="Session expired. Please sign in again."), 401
        lic = get_license(data["k"])
        problem = license_problem(lic, data["d"])
        if problem:
            return jsonify(error="license", message=problem), 403
        g.lic, g.key, g.dev = lic, data["k"], data["d"]
        return fn(*args, **kwargs)

    return wrapper


def load_creds():
    blob = g.lic.get("cred")
    if not blob:
        return None
    try:
        return json.loads(fernet().decrypt(blob.encode()).decode())
    except (InvalidToken, ValueError):
        return None


# ----------------------------------------------------------------------------- device failure tracking
def register_fail(h: str):
    d = db_get(f"devices/{h}") or {}
    fails = int(d.get("fails", 0)) + 1
    if fails >= MAX_FAILS:
        until = now() + BLOCK_SECONDS
        db_put(f"devices/{h}", {"fails": 0, "blocked_until": until, "last_fail": now()})
        return 0, until
    db_put(f"devices/{h}", {"fails": fails, "last_fail": now()})
    return MAX_FAILS - fails, 0


# ----------------------------------------------------------------------------- global guards
@app.before_request
def guard():
    p = request.path
    if p in ("/", "/health") or p.startswith("/admin"):
        return None
    if not DB_URL or not APP_SECRET:
        return jsonify(error="server", message="Server is not configured."), 500
    if p.startswith("/api/") and p != "/api/status" and ALLOWED_SIGS:
        sig = request.headers.get("X-App-Sig", "").upper().replace(":", "")
        if sig not in ALLOWED_SIGS:
            return jsonify(error="tampered", message="Unofficial app build. Install the official app."), 403
    return None


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):
        return jsonify(error="http", message=e.description), e.code
    if isinstance(e, requests.RequestException):
        app.logger.warning("upstream error: %s", e)
        return jsonify(error="upstream", message="Service temporarily unavailable. Try again."), 502
    app.logger.exception(e)
    return jsonify(error="server", message="Server error. Try again."), 500


@app.get("/")
def index():
    return "Dr. Dev Ads API is running."


@app.get("/health")
def health():
    return jsonify(ok=True)


# ----------------------------------------------------------------------------- public API
@app.get("/api/status")
def api_status():
    cfg = get_config()
    out = {"maintenance": cfg["maintenance"], "message": cfg["message"], "server_time": now(), "blocked_until": 0}
    device = request.args.get("device", "").strip()
    if len(device) >= 8:
        d = db_get(f"devices/{dev_hash(device)}") or {}
        if int(d.get("blocked_until", 0)) > now():
            out["blocked_until"] = int(d["blocked_until"])
    return jsonify(out)


@app.post("/api/license/verify")
def license_verify():
    body = request.get_json(silent=True) or {}
    key = str(body.get("key", "")).strip().upper()
    device_id = str(body.get("device_id", "")).strip()
    auto = bool(body.get("auto"))
    if len(device_id) < 8:
        return jsonify(error="bad_request", message="Invalid device."), 400

    cfg = get_config()
    if cfg["maintenance"]:
        return maintenance_response(cfg)

    h = dev_hash(device_id)
    dev = db_get(f"devices/{h}") or {}
    if int(dev.get("blocked_until", 0)) > now():
        return jsonify(
            error="blocked",
            message="Too many wrong attempts. This device is blocked for 24 hours.",
            blocked_until=int(dev["blocked_until"]),
        ), 423

    lic = db_get(f"licenses/{key}") if KEY_RE.match(key) else None
    problem = license_problem(lic, h)
    if problem:
        # A silent re-check of this device's own (now expired/revoked) license is not a wrong guess.
        counts = not (auto and lic and lic.get("device") == h)
        if counts:
            left, until = register_fail(h)
            if until:
                return jsonify(
                    error="blocked",
                    message="Too many wrong attempts. This device is blocked for 24 hours.",
                    blocked_until=until,
                ), 423
            return jsonify(error="invalid", message=problem, attempts_left=left), 401
        return jsonify(error="invalid", message=problem, attempts_left=MAX_FAILS), 401

    patch = {"last_seen": now()}
    if not lic.get("device"):
        patch.update(device=h, activated_at=now())
    db_patch(f"licenses/{key}", patch)
    if dev.get("fails"):
        db_put(f"devices/{h}", {"fails": 0})
    drop_license_cache(key)
    return jsonify(
        ok=True,
        token=make_token(key, h),
        expires_at=int(lic["expires_at"]),
        configured=bool(lic.get("cred")),
    )


@app.get("/api/license/info")
@session_required
def license_info():
    return jsonify(ok=True, expires_at=int(g.lic["expires_at"]), configured=bool(g.lic.get("cred")))


@app.get("/api/config/status")
@session_required
def config_status():
    creds = load_creds()
    return jsonify(
        configured=bool(creds),
        page_id=(creds or {}).get("page_id", ""),
        ad_account_id=(creds or {}).get("ad_account_id", ""),
    )


@app.post("/api/config/save")
@session_required
def config_save():
    b = request.get_json(silent=True) or {}
    token = str(b.get("access_token", "")).strip()
    page_id = re.sub(r"\D", "", str(b.get("page_id", "")))
    acct = re.sub(r"\D", "", str(b.get("ad_account_id", "")))
    is_demo = token == "DEMO"
    if is_demo and not DEMO_ENABLED:
        return jsonify(error="bad_request", message="Demo mode is disabled on this server."), 400
    if not is_demo and len(token) < 20:
        return jsonify(error="bad_request", message="Enter a valid access token."), 400
    if is_demo:
        j = {"data": [{"id": demo.ACCOUNT}]}
    else:
        r = _http.get(
            graph("me/adaccounts"),
            params={"access_token": token, "fields": "id,name", "limit": 5},
            timeout=30,
        )
        j = r.json()
        if "error" in j:
            msg = j["error"].get("message", "Meta rejected this token.")
            return jsonify(error="meta", message=msg.replace(token, "***")), 400
        if not j.get("data"):
            return jsonify(error="meta", message="Token is valid but has no ad accounts. Grant ads_read / ads_management."), 400
    blob = fernet().encrypt(
        json.dumps({"access_token": token, "page_id": page_id, "ad_account_id": acct}).encode()
    ).decode()
    db_patch(f"licenses/{g.key}", {"cred": blob, "cred_at": now()})
    drop_license_cache(g.key)
    return jsonify(ok=True, accounts=len(j["data"]))


@app.post("/api/config/clear")
@session_required
def config_clear():
    db_patch(f"licenses/{g.key}", {"cred": None, "cred_at": None})
    drop_license_cache(g.key)
    return jsonify(ok=True)


# ----------------------------------------------------------------------------- Meta Graph proxy
def _clean(text: str, token: str) -> str:
    """Never let the stored Meta token leak back to the app (e.g. inside paging URLs)."""
    text = re.sub(r"access_token=[^&\"\\\s]*&?", "", text)
    return text.replace(token, "")


def _form(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


@app.route("/api/meta/<path:sub>", methods=["GET", "POST", "DELETE"])
@session_required
def meta_proxy(sub):
    if not PROXY_PATH_RE.match(sub):
        return jsonify(error="bad_request", message="Path not allowed."), 400
    creds = load_creds()
    if not creds:
        return jsonify(error="not_configured", message="App is not configured yet."), 409
    token = creds["access_token"]
    if token == "DEMO":
        if not DEMO_ENABLED:
            return jsonify(error="not_configured", message="Demo mode is disabled."), 409
        args = {k: v for k, v in request.args.items() if k != "access_token"}
        payload = request.get_json(silent=True) or {}
        code, body = demo.handle(request.method, sub, args, payload if isinstance(payload, dict) else {}, g.key)
        return jsonify(body), code
    params = {k: v for k, v in request.args.items() if k != "access_token"}
    params["access_token"] = token
    url = graph(sub)
    if request.method == "GET":
        r = _http.get(url, params=params, timeout=60)
    elif request.method == "DELETE":
        r = _http.delete(url, params=params, timeout=60)
    else:
        payload = request.get_json(silent=True)
        data = _form(payload) if isinstance(payload, dict) else _form(request.form.to_dict())
        r = _http.post(url, params={"access_token": token}, data=data, timeout=120)
    return Response(_clean(r.text, token), status=r.status_code, mimetype="application/json")


@app.post("/api/meta-upload/<kind>")
@session_required
def meta_upload(kind):
    if kind not in ("video", "image"):
        return jsonify(error="bad_request", message="Unknown upload type."), 400
    creds = load_creds()
    if not creds:
        return jsonify(error="not_configured", message="App is not configured yet."), 409
    acct = request.form.get("ad_account_id", "")
    f = request.files.get("file")
    if not re.match(r"^act_\d+$", acct) or f is None:
        return jsonify(error="bad_request", message="Missing account or file."), 400
    token = creds["access_token"]
    if token == "DEMO" and DEMO_ENABLED:
        return jsonify(demo.upload(kind))
    if kind == "video":
        endpoint, field, name = "advideos", "source", f.filename or "video.mp4"
    else:
        endpoint, field, name = "adimages", "filename", "image.jpg"
    r = _http.post(
        graph(f"{acct}/{endpoint}"),
        params={"access_token": token},
        files={field: (name, f.stream, f.mimetype or "application/octet-stream")},
        timeout=900,
    )
    return Response(_clean(r.text, token), status=r.status_code, mimetype="application/json")


# ----------------------------------------------------------------------------- admin panel
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        a = request.authorization
        ok = bool(
            a
            and ADMIN_PASSWORD
            and DB_URL
            and APP_SECRET
            and hmac.compare_digest((a.username or "").encode(), ADMIN_USER.encode())
            and hmac.compare_digest((a.password or "").encode(), ADMIN_PASSWORD.encode())
        )
        if not ok:
            return Response("Admin login required", 401, {"WWW-Authenticate": 'Basic realm="Dr. Dev Admin"'})
        return fn(*args, **kwargs)

    return wrapper


@app.get("/admin")
@admin_required
def admin_page():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html"))


def _parse_valid_until(s: str):
    try:
        d = datetime.strptime(str(s), "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=IST)
    except ValueError:
        return None
    ts = int(d.timestamp())
    return ts if ts > now() else None


@app.get("/admin/api/overview")
@admin_required
def admin_overview():
    lics = db_get("licenses") or {}
    devices = db_get("devices") or {}
    t = now()
    rows = []
    for key, v in lics.items():
        exp = int(v.get("expires_at", 0))
        rows.append(
            {
                "key": key,
                "note": v.get("note", ""),
                "created_at": v.get("created_at", 0),
                "expires_at": exp,
                "activated_at": v.get("activated_at", 0),
                "last_seen": v.get("last_seen", 0),
                "bound": bool(v.get("device")),
                "device": (v.get("device") or "")[:8],
                "configured": bool(v.get("cred")),
                "revoked": bool(v.get("revoked")),
                "status": "revoked" if v.get("revoked") else ("expired" if exp <= t else "live"),
            }
        )
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    cfg = get_config()
    stats = {
        "total_users": sum(1 for r in rows if r["bound"]),
        "total_keys": len(rows),
        "live": sum(1 for r in rows if r["status"] == "live"),
        "expired": sum(1 for r in rows if r["status"] == "expired"),
        "revoked": sum(1 for r in rows if r["status"] == "revoked"),
        "unused": sum(1 for r in rows if not r["bound"]),
        "blocked_devices": sum(1 for d in devices.values() if int((d or {}).get("blocked_until", 0)) > t),
    }
    return jsonify(stats=stats, licenses=rows, maintenance=cfg, server_time=t)


@app.post("/admin/api/licenses")
@admin_required
def admin_create():
    b = request.get_json(silent=True) or {}
    exp = _parse_valid_until(b.get("valid_until", ""))
    if not exp:
        return jsonify(error="bad_request", message="Pick a valid-until date in the future."), 400
    count = max(1, min(50, int(b.get("count") or 1)))
    note = str(b.get("note", ""))[:60]
    new = {}
    while len(new) < count:
        k = gen_key()
        new[k] = {"created_at": now(), "expires_at": exp, "note": note}
    db_patch("licenses", new)
    return jsonify(ok=True, keys=list(new.keys()))


@app.post("/admin/api/licenses/<key>/<action>")
@admin_required
def admin_action(key, action):
    key = key.upper()
    if not KEY_RE.match(key) or not db_get(f"licenses/{key}"):
        return jsonify(error="not_found", message="Key not found."), 404
    b = request.get_json(silent=True) or {}
    if action == "reset-device":
        db_patch(f"licenses/{key}", {"device": None, "activated_at": None})
    elif action == "revoke":
        db_patch(f"licenses/{key}", {"revoked": bool(b.get("revoked", True)) or None})
    elif action == "extend":
        exp = _parse_valid_until(b.get("valid_until", ""))
        if not exp:
            return jsonify(error="bad_request", message="Pick a valid-until date in the future."), 400
        db_patch(f"licenses/{key}", {"expires_at": exp})
    else:
        return jsonify(error="bad_request", message="Unknown action."), 400
    drop_license_cache(key)
    return jsonify(ok=True)


@app.delete("/admin/api/licenses/<key>")
@admin_required
def admin_delete(key):
    key = key.upper()
    if KEY_RE.match(key):
        db_delete(f"licenses/{key}")
        drop_license_cache(key)
    return jsonify(ok=True)


@app.post("/admin/api/maintenance")
@admin_required
def admin_maintenance():
    b = request.get_json(silent=True) or {}
    msg = str(b.get("message", "")).strip()[:300] or DEFAULT_MAINTENANCE_MSG
    db_put("config", {"maintenance": bool(b.get("enabled")), "message": msg})
    cache_drop("config")
    return jsonify(ok=True)


@app.post("/admin/api/unblock-all")
@admin_required
def admin_unblock_all():
    devices = db_get("devices") or {}
    n = 0
    for h, d in devices.items():
        if int((d or {}).get("blocked_until", 0)) > now():
            db_delete(f"devices/{h}")
            n += 1
    return jsonify(ok=True, unblocked=n)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
