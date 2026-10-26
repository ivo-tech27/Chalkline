#!/usr/bin/env python3
"""Refresh data.json for the Chalkline app from ESPN's public college football feeds.

Uses the CFP committee rankings once they exist, otherwise the AP Top 25.
Adds recent final scores involving tracked teams. Leaves the file untouched
on any fetch or parse problem, so a bad day never breaks the app.
"""
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"
PAGE = ROOT / "index.html"
API = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"

# ESPN location names that differ from the app's team names.
ALIASES = {
    "Pittsburgh": "pitt", "California": "cal", "Miami (FL)": "miami", "Miami Hurricanes": "miami",
    "Ole Miss": "olemiss", "Mississippi": "olemiss", "North Carolina State": "ncst", "Appalachian State": "appst",
    "Southern California": "usc", "Brigham Young": "byu", "Central Florida": "ucf", "Louisiana State": "lsu",
    "Southern Methodist": "smu", "Texas Christian": "tcu", "Texas-San Antonio": "utsa", "UT San Antonio": "utsa",
    "Connecticut": "uconn", "Hawai'i": "hawaii",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "chalkline-data/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def known_teams():
    """name -> id and id set, read from the TEAMS array in the app page."""
    src = PAGE.read_text(encoding="utf-8")
    by_name, ids = {}, set()
    for m in re.finditer(r"T\('([^']+)','([^']+)','([^']+)','([^']+)','([A-Z0-9]+)'", src):
        tid, name = m.group(1), m.group(2)
        ids.add(tid)
        by_name[name.lower()] = tid
    return by_name, ids


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())[:16] or "team"


class Resolver:
    def __init__(self, extra):
        self.by_name, self.ids = known_teams()
        self.extra = {t["id"]: t for t in extra}
        for t in extra:
            self.by_name.setdefault(t["name"].lower(), t["id"])

    def __call__(self, team):
        for key in (team.get("location"), team.get("shortDisplayName"), team.get("displayName")):
            if not key:
                continue
            if key in ALIASES:
                return ALIASES[key]
            tid = self.by_name.get(key.lower())
            if tid:
                return tid
        name = team.get("location") or team.get("shortDisplayName") or "Team"
        tid = slug(name)
        if tid not in self.ids and tid not in self.extra:
            self.extra[tid] = {
                "id": tid, "name": name, "abbr": (team.get("abbreviation") or name[:4]).upper(),
                "mascot": team.get("name") or "", "conf": "IND",
                "c1": "#" + (team.get("color") or "555555"), "c2": "#" + (team.get("alternateColor") or "dddddd"),
            }
        self.by_name[name.lower()] = tid
        return tid


def record(entry):
    s = entry.get("recordSummary") or (entry.get("record") or {}).get("summary") or ""
    m = re.match(r"(\d+)-(\d+)", s)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def pick_poll(rankings):
    cfp = next((r for r in rankings if (r.get("type") == "cfp" or "playoff" in r.get("name", "").lower()) and r.get("ranks")), None)
    ap = next((r for r in rankings if (r.get("type") == "ap" or r.get("name", "").startswith("AP")) and r.get("ranks")), None)
    return cfp, ap


def main():
    old = json.loads(DATA.read_text(encoding="utf-8"))
    resolve = Resolver(old.get("extraTeams", []))
    now = dt.datetime.now(dt.timezone.utc)

    rankings = get(f"{API}/rankings").get("rankings", [])
    cfp, ap = pick_poll(rankings)
    main_poll = cfp or ap
    if not main_poll or len(main_poll["ranks"]) < 25:
        print("No usable poll in the feed; leaving data.json unchanged.")
        return 0

    top25, seen = [], set()
    for e in main_poll["ranks"][:25]:
        tid = resolve(e["team"])
        w, l = record(e)
        top25.append({"id": tid, "rank": int(e.get("current") or len(top25) + 1), "w": w, "l": l})
        seen.add(tid)

    others = []
    if cfp and ap:
        for e in ap["ranks"]:
            tid = resolve(e["team"])
            if tid not in seen:
                w, l = record(e)
                others.append({"id": tid, "pts": int(e.get("points") or 0), "w": w, "l": l})
                seen.add(tid)
    for e in (ap or main_poll).get("others", []):
        tid = resolve(e["team"])
        if tid not in seen:
            w, l = record(e)
            others.append({"id": tid, "pts": int(e.get("points") or 0), "w": w, "l": l})
            seen.add(tid)

    # Final scores from the last 10 days involving a tracked team.
    start = (now - dt.timedelta(days=10)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    games = {(g["week"], g["a"], g["b"]): g for g in old.get("games", [])}
    try:
        board = get(f"{API}/scoreboard?groups=80&limit=400&dates={start}-{end}")
        for ev in board.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            if not ((comp.get("status") or ev.get("status") or {}).get("type") or {}).get("completed"):
                continue
            sides = comp.get("competitors") or []
            if len(sides) != 2:
                continue
            ids = [resolve(c["team"]) for c in sides]
            if not any(i in seen for i in ids):
                continue
            pts = [int(float(c.get("score") or 0)) for c in sides]
            wi = 0 if pts[0] > pts[1] else 1
            home = next((i for i, c in enumerate(sides) if c.get("homeAway") == "home"), None)
            note = ""
            if home is not None and not comp.get("neutralSite"):
                note = f"At {sides[home]['team'].get('location', '')}"
            period = ((comp.get("status") or {}).get("period")) or 4
            week = int((ev.get("week") or {}).get("number") or main_poll.get("occurrence", {}).get("number") or 0)
            g = {"week": week, "a": ids[wi], "as": pts[wi], "b": ids[1 - wi], "bs": pts[1 - wi], "ot": period > 4, "note": note}
            games[(week, g["a"], g["b"])] = g
    except Exception as exc:  # scores are a bonus; keep the poll update
        print(f"Scoreboard skipped: {exc}")

    game_list = sorted(games.values(), key=lambda g: g["week"])
    if game_list:
        last = game_list[-1]["week"]
        game_list = [g for g in game_list if g["week"] > last - 3]

    src = "CFP" if cfp else "AP"
    released = (main_poll.get("date") or now.isoformat())[:10]
    week = int((main_poll.get("occurrence") or {}).get("number") or old.get("poll", {}).get("week") or 0)
    ties = {}
    for r in top25:
        ties.setdefault(r["rank"], []).append(r["id"])
    notes = []
    for rank, tids in ties.items():
        if len(tids) > 1:
            notes.append(f"{len(tids)} teams tied at No. {rank}.")

    new = {
        "version": f"{released}-{src.lower()}{week}",
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "poll": {"source": src, "name": "CFP Rankings" if cfp else "AP Top 25", "released": released, "week": week},
        "top25": top25,
        "others": others,
        "records": old.get("records", {}),
        "games": game_list,
        "extraTeams": list(resolve.extra.values()),
        "notes": notes,
        "sources": [{"title": "ESPN rankings", "url": "https://www.espn.com/college-football/rankings"},
                    {"title": "ESPN scoreboard", "url": "https://www.espn.com/college-football/scoreboard"}],
    }

    compare = lambda d: {k: v for k, v in d.items() if k != "updated"}
    if compare(new) == compare(old):
        print("No changes.")
        return 0
    DATA.write_text(json.dumps(new, indent=1) + "\n", encoding="utf-8")
    print(f"Updated: {new['poll']['name']} {released}, {len(game_list)} games.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Refresh failed, data.json unchanged: {exc}")
        sys.exit(0)
