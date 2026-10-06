"""
Demo mode - lets you test the whole app WITHOUT a Meta account.

In the app's "Configure App" screen enter the access token:  DEMO
The backend then answers every Meta call with fake (but realistic) data and remembers your
edits (pause/resume, rename, budget, duplicate, create campaign...) in memory until the server restarts.
Turn it off in production with the environment variable ENABLE_DEMO=0.
"""
import copy
import itertools
import re
import threading

ACCOUNT = "act_1234567890"
_ids = itertools.count(1000000000100)
_lock = threading.Lock()
_STATE: dict = {}

FACTORS = {
    "maximum": 1.0, "last_30d": 0.9375, "last_7d": 0.30, "today": 0.03,
    "yesterday": 0.04, "this_week_mon_today": 0.07, "this_month": 0.82,
}
FALLBACK_FACTOR = 0.4  # custom time_range


def _new_id() -> str:
    with _lock:
        return str(next(_ids))


def _default_state() -> dict:
    return {
        "campaigns": {
            "1000000000001": {
                "id": "1000000000001", "name": "The mask man", "objective": "OUTCOME_LEADS",
                "status": "ACTIVE", "daily_budget": None, "base": 1.0,
            }
        },
        "adsets": {
            "2000000000001": {
                "id": "2000000000001", "campaign_id": "1000000000001", "name": "The mask man",
                "status": "ACTIVE", "daily_budget": "100000", "start_time": "2026-08-26T00:00:00+0530",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "targeting": {
                    "geo_locations": {"countries": ["IN"]}, "age_min": 18, "age_max": 65,
                    "flexible_spec": [{"interests": [
                        {"id": "6003", "name": "Hardik Pandya"}, {"id": "6004", "name": "cricket team"},
                        {"id": "6005", "name": "Big Bash League"}, {"id": "6006", "name": "Rohit sharma"},
                    ]}],
                    "targeting_optimization": "expansion_all",
                    "publisher_platforms": ["facebook", "instagram"],
                    "facebook_positions": ["feed", "instream_video", "marketplace", "story", "video_feeds", "facebook_reels"],
                    "instagram_positions": ["explore", "story", "stream"],
                },
            }
        },
        "ads": {
            "3000000000001": {
                "id": "3000000000001", "adset_id": "2000000000001", "campaign_id": "1000000000001",
                "name": "New Leads Ad", "status": "ACTIVE", "creative_name": "join now telegram", "video": True,
            }
        },
        "recs": [
            {"type": "CREATIVE_FATIGUE", "sig": "demo-sig-1", "body": "Your top ad has been shown many times to the same people. Add a fresh creative.",
             "lift": "Estimated 6% more results", "points": "4"},
            {"type": "ADVANTAGE_PLUS_PLACEMENTS", "sig": "demo-sig-2", "body": "Let Meta choose the best placements for your ad set.",
             "lift": "Estimated 9% lower cost per result", "points": "3"},
        ],
    }


def _state(key: str) -> dict:
    with _lock:
        if key not in _STATE:
            _STATE[key] = _default_state()
        return _STATE[key]


def _factor(params: dict) -> float:
    blob = " ".join(str(v) for v in params.values())
    m = re.search(r"date_preset\(?=?([a-z_0-9]+)", blob)
    preset = params.get("date_preset") or (m.group(1) if m else None)
    if preset:
        return FACTORS.get(preset, 0.5)
    if "time_range" in blob:
        return FALLBACK_FACTOR
    return 1.0


def _insights(base: float, factor: float):
    f = base * factor
    if f <= 0:
        return None
    spend = 29845.20 * f
    subs = max(int(1391 * f), 0)
    return {
        "spend": f"{spend:.2f}",
        "reach": str(int(219357 * f)),
        "impressions": str(int(284057 * f)),
        "frequency": "1.10",
        "actions": [
            {"action_type": "subscribe_website", "value": str(subs)},
            {"action_type": "link_click", "value": str(int(1740 * f))},
        ],
        "cost_per_action_type": [{"action_type": "subscribe_website", "value": f"{(spend / subs) if subs else 0:.2f}"}],
        "unique_clicks": str(int(1980 * f)),
        "cost_per_unique_inline_link_click": "17.15",
    }


def _eff(status: str) -> str:
    return "ACTIVE" if status == "ACTIVE" else "PAUSED"


def _campaign_json(c: dict, st: dict, factor: float, with_insights: bool) -> dict:
    out = {
        "id": c["id"], "name": c["name"], "objective": c["objective"],
        "status": c["status"], "effective_status": _eff(c["status"]),
        "ads": {"data": [{"creative": {}}]},
    }
    if c.get("daily_budget"):
        out["daily_budget"] = str(c["daily_budget"])
    if with_insights:
        ins = _insights(c.get("base", 0.0), factor)
        if ins:
            out["insights"] = {"data": [ins]}
    return out


def _adset_json(a: dict) -> dict:
    out = {k: a[k] for k in ("id", "name", "status", "bid_strategy", "targeting") if k in a}
    out["effective_status"] = _eff(a["status"])
    for k in ("daily_budget", "start_time", "end_time"):
        if a.get(k):
            out[k] = str(a[k])
    return out


def _ad_json(a: dict) -> dict:
    return {
        "id": a["id"], "name": a["name"], "status": a["status"], "effective_status": _eff(a["status"]),
        "creative": {"name": a.get("creative_name", a["name"]), "object_type": "VIDEO" if a.get("video") else "SHARE",
                     **({"video_id": "999"} if a.get("video") else {})},
    }


def _preview(fmt: str) -> str:
    return (
        "<div style=\"font-family:sans-serif;max-width:430px;margin:12px auto;border:1px solid #ddd;border-radius:10px;overflow:hidden;background:#fff\">"
        "<div style=\"padding:12px\"><b>The mask man</b><br><small style=\"color:#65676b\">Sponsored</small></div>"
        "<div style=\"padding:0 12px 12px;font-size:15px\">Demo ad text. This is a fake preview shown in demo mode.</div>"
        "<div style=\"background:#1c1e21;color:#fff;height:320px;display:flex;align-items:center;justify-content:center;font-size:20px\">"
        f"Demo video - {fmt}</div>"
        "<div style=\"padding:12px;background:#f0f2f5\"><b>JOIN NOW TELEGRAM</b><br><small>example.com</small></div></div>"
    )


def _find(st: dict, oid: str):
    for kind in ("campaigns", "adsets", "ads"):
        if oid in st[kind]:
            return kind, st[kind][oid]
    return None, None


def upload(kind: str) -> dict:
    if kind == "video":
        return {"id": "999"}
    return {"images": {"image.jpg": {"hash": "demohash123"}}}


def handle(method: str, sub: str, args: dict, payload: dict, key: str):
    """Returns (http_status, json_body)."""
    st = _state(key)
    parts = sub.split("/")

    # ---------------- GET
    if method == "GET":
        if sub == "me/adaccounts":
            return 200, {"data": [{
                "id": ACCOUNT, "account_id": ACCOUNT[4:], "name": "Demo Ads Account", "currency": "INR",
                "amount_spent": "2984520", "spend_cap": "4000000",
            }]}
        if sub == "me/accounts":
            return 200, {"data": [{"id": "111222333444", "name": "Demo Page"}]}
        if sub == ACCOUNT:
            return 200, {"id": ACCOUNT, "opportunity_score": 99}
        if sub == f"{ACCOUNT}/insights":
            f = _factor(args)
            total = 0.0
            for c in st["campaigns"].values():
                ins = _insights(c.get("base", 0), f)
                if ins:
                    total += float(ins["spend"])
            return 200, {"data": [{"spend": f"{total:.2f}"}] if total else []}
        if sub == f"{ACCOUNT}/campaigns":
            f = _factor(args)
            return 200, {"data": [_campaign_json(c, st, f, True) for c in st["campaigns"].values()]}
        if sub == f"{ACCOUNT}/activities":
            return 200, {"data": [
                {"event_time": "2026-10-05T14:20:11+0000", "translated_event_type": "Ad campaign status updated", "object_name": "The mask man"},
                {"event_time": "2026-10-04T09:02:45+0000", "translated_event_type": "Daily budget updated", "object_name": "The mask man"},
                {"event_time": "2026-08-26T10:15:00+0000", "translated_event_type": "Ad campaign created", "object_name": "The mask man"},
            ]}
        if sub == f"{ACCOUNT}/recommendations":
            return 200, {"data": [{
                "opportunity_score": 99,
                "recommendations": [{
                    "type": r["type"], "recommendation_signature": r["sig"], "opportunity_score_lift": r["points"],
                    "recommendation_content": {"body": r["body"], "lift_estimate": r["lift"]},
                    "url": "https://adsmanager.facebook.com/",
                } for r in st["recs"]],
            }]}
        if sub == "search":
            q = (args.get("q") or "").lower()
            pool = ["Cricket", "Indian Premier League", "Fitness and wellness", "Dancing", "Online shopping", "Technology", "Music", "Travel"]
            return 200, {"data": [{"id": str(7000 + i), "name": n} for i, n in enumerate(pool) if q in n.lower()][:8]}
        if len(parts) == 1 and parts[0].isdigit():
            return 200, {"id": parts[0], "picture": "https://example.com/demo.jpg", "status": {"video_status": "ready"}}
        if len(parts) == 2 and parts[0].isdigit():
            cid, edge = parts
            if edge == "insights":
                kind, obj = _find(st, cid)
                base = obj.get("base", 1.0) if kind == "campaigns" else 1.0
                ins = _insights(base, _factor(args))
                return 200, {"data": [ins] if ins else []}
            if edge == "adsets":
                return 200, {"data": [_adset_json(a) for a in st["adsets"].values() if a["campaign_id"] == cid]}
            if edge == "ads":
                return 200, {"data": [_ad_json(a) for a in st["ads"].values() if a["campaign_id"] == cid]}
            if edge == "previews":
                return 200, {"data": [{"body": _preview(args.get("ad_format", "MOBILE_FEED_STANDARD"))}]}
        return 200, {"data": []}

    # ---------------- POST
    p = payload or {}
    if sub == f"{ACCOUNT}/campaigns":
        cid = _new_id()
        st["campaigns"][cid] = {
            "id": cid, "name": p.get("name", "New campaign"), "objective": p.get("objective", "OUTCOME_TRAFFIC"),
            "status": p.get("status", "PAUSED"), "daily_budget": None, "base": 0.0,
        }
        return 200, {"id": cid}
    if sub == f"{ACCOUNT}/adsets":
        aid = _new_id()
        st["adsets"][aid] = {
            "id": aid, "campaign_id": str(p.get("campaign_id")), "name": p.get("name", "Ad set"),
            "status": p.get("status", "PAUSED"), "daily_budget": p.get("daily_budget"), "start_time": p.get("start_time"),
            "end_time": p.get("end_time"), "bid_strategy": p.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
            "targeting": p.get("targeting") or {},
        }
        return 200, {"id": aid}
    if sub == f"{ACCOUNT}/adcreatives":
        return 200, {"id": _new_id()}
    if sub == f"{ACCOUNT}/ads":
        adset = st["adsets"].get(str(p.get("adset_id")))
        if not adset:
            return 400, {"error": {"message": "Demo: unknown ad set.", "code": 100}}
        aid = _new_id()
        st["ads"][aid] = {
            "id": aid, "adset_id": adset["id"], "campaign_id": adset["campaign_id"],
            "name": p.get("name", "Ad"), "status": p.get("status", "PAUSED"), "creative_name": p.get("name", "Ad"), "video": True,
        }
        return 200, {"id": aid}
    if sub == f"{ACCOUNT}/recommendations":
        sig = str(p.get("recommendation_signature", ""))
        st["recs"] = [r for r in st["recs"] if r["sig"] != sig]
        return 200, {"success": True}
    if len(parts) == 2 and parts[0].isdigit() and parts[1] == "copies":
        src = st["campaigns"].get(parts[0])
        if not src:
            return 400, {"error": {"message": "Demo: campaign not found.", "code": 100}}
        new_c = copy.deepcopy(src)
        new_c.update(id=_new_id(), name=src["name"] + " - Copy", status="PAUSED", base=0.0)
        st["campaigns"][new_c["id"]] = new_c
        for a in [x for x in st["adsets"].values() if x["campaign_id"] == src["id"]]:
            na = copy.deepcopy(a); na.update(id=_new_id(), campaign_id=new_c["id"], status="PAUSED")
            st["adsets"][na["id"]] = na
            for ad in [x for x in st["ads"].values() if x["adset_id"] == a["id"]]:
                nad = copy.deepcopy(ad); nad.update(id=_new_id(), adset_id=na["id"], campaign_id=new_c["id"], status="PAUSED")
                st["ads"][nad["id"]] = nad
        return 200, {"copied_campaign_id": new_c["id"]}
    if len(parts) == 1 and parts[0].isdigit():
        kind, obj = _find(st, parts[0])
        if not obj:
            return 400, {"error": {"message": "Demo: object not found.", "code": 100}}
        for k in ("name", "status", "daily_budget", "start_time", "end_time", "targeting"):
            if k in p:
                obj[k] = p[k]
        return 200, {"success": True}
    return 400, {"error": {"message": "Demo: this call is not supported in demo mode.", "code": 100}}
