#!/usr/bin/env python3
"""Refresh data.json for the Chalkline app from ESPN's public college football feeds.

Rankings come only from the College Football Playoff selection committee. When the
feed has no committee ranking, the current ranking is kept and only scores, records
and the coming week's games are refreshed. Any fetch or parse problem leaves the
file untouched, so a bad day never breaks the app.
"""
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
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
    "Connecticut": "uconn", "Hawai'i": "hawaii", "San José State": "sjsu", "San Jose State": "sjsu",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "chalkline-data/1.1"})
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


def parse_record(s):
    m = re.match(r"(\d+)-(\d+)", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def entry_record(entry):
    return parse_record(entry.get("recordSummary") or (entry.get("record") or {}).get("summary"))


def committee_poll(rankings):
    for r in rankings:
        name = (r.get("name") or "").lower()
        if (r.get("type") == "cfp" or "playoff" in name or "committee" in name) and len(r.get("ranks") or []) >= 25:
            return r
    return None


def scoreboard_day(day):
    """One day of FBS games. ESPN rejects some parameter mixes, so fall back to simpler URLs."""
    for q in (f"groups=80&limit=300&dates={day}", f"groups=80&dates={day}", f"dates={day}"):
        try:
            return get(f"{API}/scoreboard?{q}").get("events", [])
        except urllib.error.HTTPError as exc:
            last = exc
    print(f"Scoreboard {day} unavailable: {last}")
    return []


def main():
    old = json.loads(DATA.read_text(encoding="utf-8"))
    resolve = Resolver(old.get("extraTeams", []))
    now = dt.datetime.now(dt.timezone.utc)

    try:
        poll = committee_poll(get(f"{API}/rankings").get("rankings", []))
    except Exception as exc:
        print(f"Rankings feed unavailable: {exc}")
        poll = None

    if poll:
        top25 = []
        for e in poll["ranks"][:25]:
            w, l = entry_record(e)
            top25.append({"id": resolve(e["team"]), "rank": int(e.get("current") or len(top25) + 1),
                          "prev": int(e.get("previous") or 0), "w": w, "l": l})
        released = (poll.get("date") or now.isoformat())[:10]
        week = int((poll.get("occurrence") or {}).get("number") or old.get("poll", {}).get("week") or 0)
        others = []
        in_poll = {r["id"] for r in top25}
        for e in poll.get("droppedOut") or []:
            tid = resolve(e["team"])
            if tid not in in_poll:
                w, l = entry_record(e)
                others.append({"id": tid, "pts": 0, "prev": int(e.get("previous") or 0), "w": w, "l": l, "dropped": True})
        poll_info = {"source": "CFP", "name": "CFP Rankings", "released": released, "week": week}
        version = f"{released}-cfp{week}"
    else:
        print("No committee ranking in the feed; keeping the current ranking.")
        top25, others = old["top25"], old.get("others", [])
        poll_info, version = old["poll"], old["version"]

    tracked = {r["id"] for r in top25} | {o["id"] for o in others}
    records = dict(old.get("records", {}))
    games = {(g["week"], g["a"], g["b"]): g for g in old.get("games", [])}
    upcoming = []

    for offset in range(-9, 9):
        day = (now + dt.timedelta(days=offset)).strftime("%Y%m%d")
        for ev in scoreboard_day(day):
            try:
                comp = (ev.get("competitions") or [{}])[0]
                sides = comp.get("competitors") or []
                if len(sides) != 2:
                    continue
                ids = [resolve(c["team"]) for c in sides]
                for tid, c in zip(ids, sides):
                    w, l = parse_record(((c.get("records") or [{}])[0]).get("summary"))
                    if w is not None:
                        records[tid] = {"w": w, "l": l}
                if not any(i in tracked for i in ids):
                    continue
                state = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
                home = next((i for i, c in enumerate(sides) if c.get("homeAway") == "home"), 1)
                away = 1 - home
                neutral = bool(comp.get("neutralSite"))
                week = int((ev.get("week") or {}).get("number") or 0)
                if state.get("completed"):
                    pts = [int(float(c.get("score") or 0)) for c in sides]
                    wi = 0 if pts[0] > pts[1] else 1
                    note = "" if neutral else f"At {sides[home]['team'].get('location', '')}"
                    period = (comp.get("status") or {}).get("period") or 4
                    g = {"week": week, "a": ids[wi], "as": pts[wi], "b": ids[1 - wi], "bs": pts[1 - wi], "ot": period > 4, "note": note}
                    games[(week, g["a"], g["b"])] = g
                elif state.get("state") == "pre" and offset >= 0:
                    tv = ""
                    for b in comp.get("broadcasts") or []:
                        if b.get("names"):
                            tv = b["names"][0]
                            break
                    upcoming.append({"week": week, "date": ev.get("date") or "", "away": ids[away], "home": ids[home],
                                     "neutral": neutral, "tv": tv})
            except Exception as exc:  # one odd event shouldn't sink the rest
                print(f"Skipped an event: {exc}")

    # Records from the scoreboard are newer than the ranking's for teams that have played since.
    for r in top25 + others:
        rec = records.get(r["id"])
        if rec:
            r["w"], r["l"] = rec["w"], rec["l"]

    game_list = sorted(games.values(), key=lambda g: g["week"])
    if game_list:
        last = game_list[-1]["week"]
        game_list = [g for g in game_list if g["week"] > last - 3]
    upcoming.sort(key=lambda g: g["date"])
    if upcoming:
        first_week = upcoming[0]["week"]
        upcoming = [g for g in upcoming if g["week"] == first_week]

    notes = []
    ties = {}
    for r in top25:
        ties.setdefault(r["rank"], []).append(r["id"])
    for rank, tids in ties.items():
        if len(tids) > 1:
            notes.append(f"{len(tids)} teams tied at No. {rank}.")

    new = {
        "version": version,
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "poll": poll_info,
        "top25": top25,
        "others": others,
        "records": records,
        "games": game_list,
        "upcoming": upcoming,
        "extraTeams": list(resolve.extra.values()),
        "notes": notes,
        "sources": [{"title": "CFP rankings", "url": "https://collegefootballplayoff.com/rankings"},
                    {"title": "ESPN scoreboard", "url": "https://www.espn.com/college-football/scoreboard"}],
    }

    compare = lambda d: {k: v for k, v in d.items() if k != "updated"}
    if compare(new) == compare(old):
        print("No changes.")
        return 0
    DATA.write_text(json.dumps(new, indent=1) + "\n", encoding="utf-8")
    print(f"Updated: {poll_info['name']} {poll_info['released']}, {len(game_list)} results, {len(upcoming)} upcoming games.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Refresh failed, data.json unchanged: {exc}")
        sys.exit(0)
