"""
Demo engine v2 - lets you test the whole app WITHOUT a Meta account.

A demo account is one JSON document stored in Firebase (demo_docs/<id>). The admin panel edits it freely
(campaigns, ad sets, ads, daily numbers, spending limit, opportunity score...). A license can be linked to a
demo account; the app then talks to this engine instead of Meta, and every number it shows is computed from
the daily rows you entered - so you can test counting, date filters and tracking exactly like the real thing.

All money in the document is in MAJOR units (rupees). Graph-style responses convert to minor units (paise).
"""
import copy
import json
import random
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

IST = timezone(timedelta(hours=5, minutes=30))
SAMPLE_VIDEO = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
ID_START = 1000000000100
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "PYG", "UGX", "RWF", "XAF", "XOF", "XPF"}

OBJECTIVES = ["OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT", "OUTCOME_AWARENESS", "OUTCOME_APP_PROMOTION"]
RESULT_BY_OBJECTIVE = {
    "OUTCOME_LEADS": "subscribe_website", "OUTCOME_SALES": "purchase", "OUTCOME_TRAFFIC": "landing_page_view",
    "OUTCOME_ENGAGEMENT": "post_engagement", "OUTCOME_AWARENESS": "link_click", "OUTCOME_APP_PROMOTION": "mobile_app_install",
}
RESULT_ACTIONS = ["subscribe_website", "lead", "onsite_conversion.lead_grouped", "purchase", "landing_page_view", "link_click", "post_engagement", "mobile_app_install"]
BID = ["LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP", "COST_CAP", "LOWEST_COST_WITH_MIN_ROAS"]
STATUSES = ["ACTIVE", "PAUSED", "ARCHIVED"]
METRICS = ["spend", "reach", "impressions", "clicks", "unique_clicks", "results"]
INTERESTS = [
    "Cricket", "Indian Premier League", "Fitness and wellness", "Dancing", "Online shopping", "Technology",
    "Music", "Travel", "Bollywood", "Entrepreneurship", "Gaming", "Photography", "Fashion", "Food", "Education",
]


def today() -> date:
    return datetime.now(IST).date()


def offset(cur: str) -> int:
    return 1 if cur in ZERO_DECIMAL else 100


# ------------------------------------------------------------------ sanitizing helpers
def _num(v, default=0.0, lo=0.0, hi=1e12) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:
        return default
    return max(lo, min(hi, x))


def _int(v, default=0, lo=0, hi=10 ** 12) -> int:
    return int(round(_num(v, default, lo, hi)))


def _str(v, n=500) -> str:
    return "" if v is None else str(v).strip()[:n]


def _date(v) -> str:
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return ""


def _url(v) -> str:
    s = _str(v, 1000)
    return s if s.startswith(("http://", "https://")) or s == "" else ""


def _list(v, n=30, size=60):
    if not isinstance(v, list):
        return []
    return [_str(x, size) for x in v[:n] if _str(x, size)]


class _Ids:
    def __init__(self, start):
        self.n = max(int(start or ID_START), ID_START)
        self.used = set()

    def get(self, v=None) -> str:
        s = str(v or "")
        if s.isdigit() and len(s) >= 10 and s not in self.used:
            self.used.add(s)
            self.n = max(self.n, int(s) + 1)
            return s
        while str(self.n) in self.used:
            self.n += 1
        s = str(self.n)
        self.n += 1
        self.used.add(s)
        return s


def _placements(p):
    p = p if isinstance(p, dict) else {}
    plat = [x for x in _list(p.get("platforms"), 4, 20) if x in ("facebook", "instagram", "audience_network", "messenger")]
    return {
        "auto": bool(p.get("auto", False)),
        "platforms": plat or ["facebook", "instagram"],
        "facebook": _list(p.get("facebook"), 12, 30),
        "instagram": _list(p.get("instagram"), 12, 30),
    }


def _adset(a, ids):
    a = a if isinstance(a, dict) else {}
    amin = _int(a.get("age_min"), 18, 13, 65)
    amax = _int(a.get("age_max"), 65, 13, 65)
    if amin > amax:
        amin, amax = amax, amin
    bid = _str(a.get("bid_strategy"), 40)
    st = _str(a.get("status"))
    return {
        "id": ids.get(a.get("id")),
        "name": _str(a.get("name"), 120) or "Ad set",
        "status": st if st in STATUSES else "ACTIVE",
        "daily_budget": _num(a.get("daily_budget"), 0.0),
        "start_time": _date(a.get("start_time")) or today().isoformat(),
        "end_time": _date(a.get("end_time")),
        "bid_strategy": bid if bid in BID else BID[0],
        "countries": [c.upper() for c in _list(a.get("countries"), 25, 2) if len(c) == 2] or ["IN"],
        "age_min": amin, "age_max": amax,
        "genders": _int(a.get("genders"), 0, 0, 2),
        "interests": _list(a.get("interests"), 25, 60),
        "placements": _placements(a.get("placements")),
        "expansion": bool(a.get("expansion", False)),
    }


def _ad(a, ids, adset_ids):
    a = a if isinstance(a, dict) else {}
    r = a.get("reactions") if isinstance(a.get("reactions"), dict) else {}
    asid = str(a.get("adset_id") or "")
    st = _str(a.get("status"))
    return {
        "id": ids.get(a.get("id")),
        "adset_id": asid if asid in adset_ids else (adset_ids[0] if adset_ids else ""),
        "name": _str(a.get("name"), 120) or "Ad",
        "status": st if st in STATUSES else "ACTIVE",
        "profile_name": _str(a.get("profile_name"), 80),
        "profile_image": _url(a.get("profile_image")),
        "message": _str(a.get("message"), 2000),
        "headline": _str(a.get("headline"), 200),
        "link_url": _url(a.get("link_url")),
        "link_display": _str(a.get("link_display"), 120),
        "cta": _str(a.get("cta"), 30) or "LEARN_MORE",
        "video_url": _url(a.get("video_url")),
        "thumb_url": _url(a.get("thumb_url")),
        "reactions": {k: _int(r.get(k), 0) for k in ("like", "love", "haha")},
        "comments": _int(a.get("comments"), 0),
        "shares": _int(a.get("shares"), 0),
    }


def _daily(d):
    out = {}
    if not isinstance(d, dict):
        return out
    for k, row in list(d.items())[:900]:
        k2 = _date(k)
        if not k2 or not isinstance(row, dict):
            continue
        out[k2] = {"spend": round(_num(row.get("spend")), 2)}
        for m in METRICS:
            if m != "spend":
                out[k2][m] = _int(row.get(m))
    return out


def _campaign(c, ids):
    c = c if isinstance(c, dict) else {}
    obj = _str(c.get("objective"), 40)
    obj = obj if obj in OBJECTIVES else "OUTCOME_TRAFFIC"
    ra = _str(c.get("result_action"), 60)
    raw_sets = c.get("adsets") if isinstance(c.get("adsets"), list) else []
    adsets = [_adset(x, ids) for x in raw_sets[:10]]
    asids = [a["id"] for a in adsets]
    raw_ads = c.get("ads") if isinstance(c.get("ads"), list) else []
    ads = [_ad(x, ids, asids) for x in raw_ads[:20]]
    cb = c.get("daily_budget")
    st = _str(c.get("status"))
    return {
        "id": ids.get(c.get("id")),
        "name": _str(c.get("name"), 120) or "Campaign",
        "objective": obj,
        "status": st if st in STATUSES else "ACTIVE",
        "daily_budget": _num(cb) if cb not in (None, "", 0, "0") else None,
        "result_action": ra if ra in RESULT_ACTIONS else RESULT_BY_OBJECTIVE[obj],
        "created": _date(c.get("created")) or today().isoformat(),
        "adsets": adsets,
        "ads": ads,
        "daily": _daily(c.get("daily")),
    }


def sanitize(doc) -> dict:
    doc = doc if isinstance(doc, dict) else {}
    ids = _Ids(doc.get("next_id"))
    camps_in = doc.get("campaigns") if isinstance(doc.get("campaigns"), list) else []
    # reserve existing ids first so new items never collide
    for c in camps_in:
        if isinstance(c, dict):
            for src in [c] + list(c.get("adsets") or []) + list(c.get("ads") or []):
                if isinstance(src, dict):
                    s = str(src.get("id") or "")
                    if s.isdigit() and len(s) >= 10:
                        ids.n = max(ids.n, int(s) + 1)
    recs = []
    raw_recs = doc.get("recommendations") if isinstance(doc.get("recommendations"), list) else []
    for i, r in enumerate(raw_recs[:20]):
        if isinstance(r, dict):
            recs.append({
                "id": _str(r.get("id"), 30) or f"rec{i + 1}_{random.randint(100, 999)}",
                "type": _str(r.get("type"), 60) or "RECOMMENDATION",
                "body": _str(r.get("body"), 400), "lift": _str(r.get("lift"), 120),
                "points": _int(r.get("points"), 0, 0, 100),
            })
    videos = doc.get("videos") if isinstance(doc.get("videos"), dict) else {}
    return {
        "name": _str(doc.get("name"), 80) or "Account",
        "currency": (_str(doc.get("currency"), 3).upper() or "INR"),
        "spend_cap": round(_num(doc.get("spend_cap")), 2),
        "opportunity_score": _int(doc.get("opportunity_score"), 99, 0, 100),
        "recommendations": recs,
        "videos": {str(k)[:20]: _url(v) for k, v in list(videos.items())[-60:] if _url(v)},
        "creatives": {},
        "campaigns": [_campaign(c, ids) for c in camps_in[:60]],
        "next_id": ids.n,
    }


# ------------------------------------------------------------------ sample data
def generate_daily(start: str, end: str, totals_in: dict, seed=None) -> dict:
    """Spread totals over a date range with natural-looking ups and downs. Sums match the totals exactly."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if e < s:
        s, e = e, s
    days = min((e - s).days + 1, 800)
    rnd = random.Random(seed)
    weights = [max(0.25, 1 + rnd.uniform(-0.45, 0.45) + (0.18 if (i % 7) in (5, 6) else 0)) for i in range(days)]
    tw = sum(weights)
    keys = [(s + timedelta(days=i)).isoformat() for i in range(days)]
    out = {k: {} for k in keys}
    for m in METRICS:
        total = _num(totals_in.get(m))
        if m == "spend":
            vals = [round(total * w / tw, 2) for w in weights]
            vals[-1] = round(total - sum(vals[:-1]), 2)
        else:
            vals = [int(total * w / tw) for w in weights]
            vals[-1] = int(round(total)) - sum(vals[:-1])
        for k, v in zip(keys, vals):
            out[k][m] = max(v, 0)
    return out


def new_doc(name: str, currency="INR", sample=True) -> dict:
    doc = {"name": name, "currency": currency, "spend_cap": 40000.0 if sample else 0.0, "opportunity_score": 99,
           "recommendations": [], "campaigns": [], "next_id": ID_START}
    if sample:
        t = today()
        daily = generate_daily((t - timedelta(days=44)).isoformat(), t.isoformat(),
                               {"spend": 29845.20, "reach": 219357, "impressions": 284057, "clicks": 2260,
                                "unique_clicks": 1740, "results": 1391}, seed=7)
        doc["recommendations"] = [
            {"type": "CREATIVE_FATIGUE", "body": "Your best ad has been shown many times to the same people. Add a fresh creative.",
             "lift": "Estimated 6% more results", "points": 4},
            {"type": "ADVANTAGE_PLUS_PLACEMENTS", "body": "Let Meta choose the best placements for your ad set.",
             "lift": "Estimated 9% lower cost per result", "points": 3},
        ]
        doc["campaigns"] = [{
            "name": "Sample campaign", "objective": "OUTCOME_LEADS", "status": "ACTIVE",
            "created": (t - timedelta(days=44)).isoformat(),
            "adsets": [{
                "name": "Sample ad set", "status": "ACTIVE", "daily_budget": 1000, "start_time": (t - timedelta(days=44)).isoformat(),
                "countries": ["IN"], "age_min": 18, "age_max": 65, "interests": ["Cricket", "Indian Premier League"],
                "placements": {"auto": False, "platforms": ["facebook", "instagram"],
                               "facebook": ["feed", "story", "video_feeds", "facebook_reels"], "instagram": ["stream", "story", "explore"]},
                "expansion": True,
            }],
            "ads": [{
                "name": "Sample video ad", "status": "ACTIVE",
                "message": "Watch this quick video and join us today. Tap the button below to subscribe.",
                "headline": "JOIN NOW", "link_url": "https://example.com", "link_display": "example.com", "cta": "SUBSCRIBE",
                "video_url": SAMPLE_VIDEO, "reactions": {"like": 98, "love": 15, "haha": 8}, "comments": 32, "shares": 1,
            }],
            "daily": daily,
        }]
    return sanitize(doc)


# ------------------------------------------------------------------ insights
def date_range(args: dict):
    fields = str(args.get("fields", ""))
    preset = args.get("date_preset")
    if not preset:
        m = re.search(r"date_preset\(([a-z_0-9]+)\)", fields)
        preset = m.group(1) if m else None
    tr = args.get("time_range")
    if not preset and not tr:
        m2 = re.search(r"time_range\((\{[^}]*\})\)", fields)
        tr = m2.group(1) if m2 else None
    t = today()
    if preset:
        if preset == "today":
            return t, t
        if preset == "yesterday":
            return t - timedelta(days=1), t - timedelta(days=1)
        if preset == "last_7d":
            return t - timedelta(days=7), t - timedelta(days=1)
        if preset == "last_30d":
            return t - timedelta(days=30), t - timedelta(days=1)
        if preset == "this_week_mon_today":
            return t - timedelta(days=t.weekday()), t
        if preset == "this_month":
            return t.replace(day=1), t
        return None, None
    if tr:
        try:
            j = json.loads(tr) if isinstance(tr, str) else tr
            return date.fromisoformat(j["since"]), date.fromisoformat(j["until"])
        except (ValueError, KeyError, TypeError):
            return None, None
    return None, None


def totals(camp: dict, since=None, until=None) -> dict:
    tot = {"spend": 0.0, "reach": 0, "impressions": 0, "clicks": 0, "unique_clicks": 0, "results": 0}
    for d, row in (camp.get("daily") or {}).items():
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        if (since and dd < since) or (until and dd > until):
            continue
        for k in tot:
            tot[k] += row.get(k, 0) or 0
    tot["spend"] = round(tot["spend"], 2)
    return tot


def lifetime_spend(doc: dict) -> float:
    return round(sum(totals(c)["spend"] for c in doc["campaigns"]), 2)


def insights_row(camp: dict, since, until):
    t = totals(camp, since, until)
    if not any(t.values()):
        return None
    ra = camp.get("result_action") or "link_click"
    freq = (t["impressions"] / t["reach"]) if t["reach"] else 0
    row = {
        "spend": f"{t['spend']:.2f}", "reach": str(t["reach"]), "impressions": str(t["impressions"]),
        "frequency": f"{freq:.2f}", "unique_clicks": str(t["unique_clicks"]),
        "actions": [{"action_type": ra, "value": str(t["results"])}, {"action_type": "link_click", "value": str(t["clicks"])}],
    }
    if ra == "link_click":
        row["actions"] = [{"action_type": "link_click", "value": str(t["results"] or t["clicks"])}]
    if t["results"]:
        row["cost_per_action_type"] = [{"action_type": ra, "value": f"{t['spend'] / t['results']:.2f}"}]
    if t["unique_clicks"]:
        row["cost_per_unique_inline_link_click"] = f"{t['spend'] / t['unique_clicks']:.2f}"
    return row


# ------------------------------------------------------------------ Graph-style JSON
def _minor(v, cur) -> str:
    return str(int(round(float(v) * offset(cur))))


def _major(v, cur) -> float:
    return round(float(v) / offset(cur), 2)


def _eff(status: str) -> str:
    return status if status in ("ACTIVE", "PAUSED", "ARCHIVED") else "PAUSED"


def _profile(ad, ctx):
    return (ad.get("profile_name") or ctx.get("name") or "Page", ad.get("profile_image") or ctx.get("avatar") or "")


def ad_content(ad: dict, ctx: dict) -> dict:
    name, img = _profile(ad, ctx)
    link = ad.get("link_url", "")
    disp = ad.get("link_display") or (urlparse(link).netloc if link else "")
    return {
        "profile_name": name, "profile_image": img, "message": ad.get("message", ""), "headline": ad.get("headline", ""),
        "link_url": link, "link_display": disp, "cta": ad.get("cta", "LEARN_MORE"),
        "video_url": ad.get("video_url", ""), "thumb_url": ad.get("thumb_url", ""),
        "reactions": ad.get("reactions", {}), "comments": ad.get("comments", 0), "shares": ad.get("shares", 0),
    }


def ad_json(ad: dict, camp: dict, ctx: dict) -> dict:
    name, img = _profile(ad, ctx)
    has_video = bool(ad.get("video_url"))
    creative = {"name": ad["name"], "object_type": "VIDEO" if has_video else "SHARE",
                "thumbnail_url": ad.get("thumb_url") or img}
    if has_video:
        creative["video_id"] = "v" + ad["id"]
    return {
        "id": ad["id"], "name": ad["name"], "status": ad["status"], "effective_status": _eff(ad["status"]),
        "creative": creative, "drdev": ad_content(ad, ctx), "campaign_name": camp["name"],
    }


def adset_json(a: dict, cur: str) -> dict:
    p = a["placements"]
    t = {"geo_locations": {"countries": a["countries"]}, "age_min": a["age_min"], "age_max": a["age_max"]}
    if a["genders"] in (1, 2):
        t["genders"] = [a["genders"]]
    if a["interests"]:
        t["flexible_spec"] = [{"interests": [{"id": str(6000 + i), "name": n} for i, n in enumerate(a["interests"])]}]
    if a["expansion"]:
        t["targeting_optimization"] = "expansion_all"
    if not p["auto"]:
        t["publisher_platforms"] = p["platforms"]
        if p["facebook"]:
            t["facebook_positions"] = p["facebook"]
        if p["instagram"]:
            t["instagram_positions"] = p["instagram"]
    out = {"id": a["id"], "name": a["name"], "status": a["status"], "effective_status": _eff(a["status"]),
           "bid_strategy": a["bid_strategy"], "targeting": t, "start_time": a["start_time"] + "T00:00:00+0530"}
    if a["daily_budget"]:
        out["daily_budget"] = _minor(a["daily_budget"], cur)
    if a["end_time"]:
        out["end_time"] = a["end_time"] + "T23:59:59+0530"
    return out


def campaign_json(c: dict, cur: str, since, until, ctx: dict, with_insights=True) -> dict:
    first_ad = c["ads"][0] if c["ads"] else {}
    thumb = (first_ad.get("thumb_url") or _profile(first_ad, ctx)[1]) or None
    out = {"id": c["id"], "name": c["name"], "objective": c["objective"], "status": c["status"],
           "effective_status": _eff(c["status"]),
           "ads": {"data": [{"creative": {"thumbnail_url": thumb}}]}}
    if c.get("daily_budget"):
        out["daily_budget"] = _minor(c["daily_budget"], cur)
    if with_insights:
        row = insights_row(c, since, until)
        if row:
            out["insights"] = {"data": [row]}
    return out


def _find(doc, oid):
    for c in doc["campaigns"]:
        if c["id"] == oid:
            return "campaign", c, c
        for a in c["adsets"]:
            if a["id"] == oid:
                return "adset", a, c
        for a in c["ads"]:
            if a["id"] == oid:
                return "ad", a, c
    return None, None, None


def _next(doc) -> str:
    i = _Ids(doc.get("next_id"))
    s = i.get()
    doc["next_id"] = i.n
    return s


def targeting_to_fields(a: dict, t: dict):
    geo = t.get("geo_locations") or {}
    if geo.get("countries"):
        a["countries"] = [str(x).upper() for x in geo["countries"] if len(str(x)) == 2] or a["countries"]
    a["age_min"] = _int(t.get("age_min"), a["age_min"], 13, 65)
    a["age_max"] = _int(t.get("age_max"), a["age_max"], 13, 65)
    g = t.get("genders")
    a["genders"] = g[0] if isinstance(g, list) and len(g) == 1 and g[0] in (1, 2) else 0
    fs = t.get("flexible_spec")
    if isinstance(fs, list):
        names = []
        for spec in fs:
            for it in ((spec or {}).get("interests") or []):
                if isinstance(it, dict) and it.get("name"):
                    names.append(str(it["name"]))
        a["interests"] = names[:25]
    a["expansion"] = t.get("targeting_optimization") == "expansion_all"
    plats = t.get("publisher_platforms")
    pl = a["placements"]
    if isinstance(plats, list) and plats:
        pl.update(auto=False, platforms=[p for p in plats if p in ("facebook", "instagram", "audience_network", "messenger")],
                  facebook=list(t.get("facebook_positions") or []), instagram=list(t.get("instagram_positions") or []))
    else:
        pl["auto"] = True


# ------------------------------------------------------------------ main entry
def handle(method: str, sub: str, args: dict, payload: dict, doc: dict, ctx: dict, aid: str):
    """Returns (http_status, json_body, changed_doc)."""
    acct = "act_" + aid
    cur = doc["currency"]
    parts = sub.split("/")
    since, until = date_range(args)
    name = ctx.get("name") or doc["name"]

    if method == "GET":
        if sub == "me/adaccounts":
            return 200, {"data": [{"id": acct, "account_id": aid, "name": name, "currency": cur,
                                   "amount_spent": _minor(lifetime_spend(doc), cur),
                                   "spend_cap": _minor(doc["spend_cap"], cur)}]}, False
        if sub == "me/accounts":
            return 200, {"data": [{"id": "100000000000001", "name": name}]}, False
        if sub == acct:
            return 200, {"id": acct, "opportunity_score": doc["opportunity_score"]}, False
        if sub == f"{acct}/insights":
            total = round(sum(totals(c, since, until)["spend"] for c in doc["campaigns"]), 2)
            return 200, {"data": [{"spend": f"{total:.2f}"}] if total else []}, False
        if sub == f"{acct}/campaigns":
            return 200, {"data": [campaign_json(c, cur, since, until, ctx) for c in doc["campaigns"]]}, False
        if sub == f"{acct}/ads":
            return 200, {"data": [ad_json(a, c, ctx) for c in doc["campaigns"] for a in c["ads"]]}, False
        if sub == f"{acct}/activities":
            items = [{"event_time": c["created"] + "T10:00:00+0000", "translated_event_type": "Ad campaign created",
                      "object_name": c["name"]} for c in doc["campaigns"][-8:]]
            items.sort(key=lambda x: x["event_time"], reverse=True)
            return 200, {"data": items}, False
        if sub == f"{acct}/recommendations":
            return 200, {"data": [{
                "opportunity_score": doc["opportunity_score"],
                "recommendations": [{"type": r["type"], "recommendation_signature": r["id"], "opportunity_score_lift": str(r["points"]),
                                     "recommendation_content": {"body": r["body"], "lift_estimate": r["lift"]},
                                     "url": "https://adsmanager.facebook.com/"} for r in doc["recommendations"]],
            }]}, False
        if sub == "search":
            q = (args.get("q") or "").lower()
            return 200, {"data": [{"id": str(7000 + i), "name": n} for i, n in enumerate(INTERESTS) if q in n.lower()][:8]}, False
        if len(parts) == 1 and parts[0].isdigit():
            return 200, {"id": parts[0], "picture": "https://example.com/poster.jpg", "status": {"video_status": "ready"}}, False
        if len(parts) == 2 and parts[0].isdigit():
            kind, obj, camp = _find(doc, parts[0])
            edge = parts[1]
            if kind == "campaign":
                if edge == "insights":
                    row = insights_row(obj, since, until)
                    return 200, {"data": [row] if row else []}, False
                if edge == "adsets":
                    return 200, {"data": [adset_json(a, cur) for a in obj["adsets"]]}, False
                if edge == "ads":
                    return 200, {"data": [ad_json(a, obj, ctx) for a in obj["ads"]]}, False
            if kind == "ad" and edge == "previews":
                return 200, {"data": [{"body": "<div style='font-family:sans-serif;padding:24px'>Preview is shown natively in the app.</div>"}]}, False
        return 200, {"data": []}, False

    # ---------------------------------------------------------------- POST
    p = payload or {}
    if sub == f"{acct}/campaigns":
        obj = p.get("objective") if p.get("objective") in OBJECTIVES else "OUTCOME_TRAFFIC"
        c = {"id": _next(doc), "name": _str(p.get("name"), 120) or "New campaign", "objective": obj,
             "status": p.get("status") if p.get("status") in STATUSES else "PAUSED", "daily_budget": None,
             "result_action": RESULT_BY_OBJECTIVE[obj], "created": today().isoformat(), "adsets": [], "ads": [], "daily": {}}
        doc["campaigns"].append(c)
        return 200, {"id": c["id"]}, True
    if sub == f"{acct}/adsets":
        camp = next((c for c in doc["campaigns"] if c["id"] == str(p.get("campaign_id"))), None)
        if not camp:
            return 400, {"error": {"message": "Campaign not found.", "code": 100}}, False
        a = _adset({"name": p.get("name"), "status": p.get("status"), "bid_strategy": p.get("bid_strategy"),
                    "daily_budget": _major(p.get("daily_budget") or 0, cur), "start_time": _date(p.get("start_time")),
                    "end_time": _date(p.get("end_time"))}, _Ids(doc["next_id"]))
        a["id"] = _next(doc)
        if isinstance(p.get("targeting"), dict):
            targeting_to_fields(a, p["targeting"])
        camp["adsets"].append(a)
        return 200, {"id": a["id"]}, True
    if sub == f"{acct}/advideos":
        url = _url(p.get("file_url"))
        if not url:
            return 400, {"error": {"message": "Enter a video URL.", "code": 100}}, False
        vid = "v" + _next(doc)
        doc["videos"][vid] = url
        return 200, {"id": vid}, True
    if sub == f"{acct}/adcreatives":
        cid = _next(doc)
        spec = p.get("object_story_spec") if isinstance(p.get("object_story_spec"), dict) else {}
        vd = spec.get("video_data") or {}
        ld = spec.get("link_data") or {}
        src = vd or ld
        cta = src.get("call_to_action") or {}
        link = (cta.get("value") or {}).get("link") or src.get("link") or ""
        doc["creatives"][cid] = {
            "message": src.get("message", ""), "headline": src.get("title") or src.get("name") or "", "link_url": link,
            "cta": cta.get("type", "LEARN_MORE"), "video_url": doc["videos"].get(str(vd.get("video_id", "")), ""),
            "thumb_url": vd.get("image_url") if str(vd.get("image_url", "")).startswith("http") and "example.com" not in str(vd.get("image_url")) else "",
        }
        return 200, {"id": cid}, True
    if sub == f"{acct}/ads":
        kind, adset, camp = _find(doc, str(p.get("adset_id")))
        if kind != "adset":
            return 400, {"error": {"message": "Ad set not found.", "code": 100}}, False
        cr = doc["creatives"].pop(str((p.get("creative") or {}).get("creative_id", "")), {})
        ad = _ad({**cr, "name": p.get("name"), "status": p.get("status"), "adset_id": adset["id"],
                  "reactions": {"like": 0, "love": 0, "haha": 0}}, _Ids(doc["next_id"]), [adset["id"]])
        ad["id"] = _next(doc)
        camp["ads"].append(ad)
        return 200, {"id": ad["id"]}, True
    if sub == f"{acct}/recommendations":
        sig = str(p.get("recommendation_signature", ""))
        before = len(doc["recommendations"])
        doc["recommendations"] = [r for r in doc["recommendations"] if r["id"] != sig]
        if len(doc["recommendations"]) < before:
            doc["opportunity_score"] = min(100, doc["opportunity_score"] + 1)
        return 200, {"success": True}, True
    if len(parts) == 2 and parts[0].isdigit() and parts[1] == "copies":
        kind, obj, camp = _find(doc, parts[0])
        if kind != "campaign":
            return 400, {"error": {"message": "Campaign not found.", "code": 100}}, False
        n = copy.deepcopy(obj)
        n.update(id=_next(doc), name=obj["name"] + " - Copy", status="PAUSED", daily={}, created=today().isoformat())
        for a in n["adsets"]:
            old = a["id"]
            a["id"] = _next(doc)
            a["status"] = "PAUSED"
            for ad in n["ads"]:
                if ad["adset_id"] == old:
                    ad["adset_id"] = a["id"]
        for ad in n["ads"]:
            ad["id"] = _next(doc)
            ad["status"] = "PAUSED"
        doc["campaigns"].append(n)
        return 200, {"copied_campaign_id": n["id"]}, True
    if len(parts) == 1 and parts[0].isdigit():
        kind, obj, camp = _find(doc, parts[0])
        if not kind:
            return 400, {"error": {"message": "Object not found.", "code": 100}}, False
        if _str(p.get("name")):
            obj["name"] = _str(p["name"], 120)
        if p.get("status") in ("ACTIVE", "PAUSED", "ARCHIVED"):
            obj["status"] = p["status"]
        if "daily_budget" in p and kind in ("campaign", "adset"):
            obj["daily_budget"] = _major(p["daily_budget"], cur)
        if kind == "adset":
            if "start_time" in p:
                obj["start_time"] = _date(p["start_time"]) or obj["start_time"]
            if "end_time" in p:
                obj["end_time"] = _date(p["end_time"])
            if isinstance(p.get("targeting"), dict):
                targeting_to_fields(obj, p["targeting"])
        return 200, {"success": True}, True
    return 400, {"error": {"message": "This call is not supported here.", "code": 100}}, False
