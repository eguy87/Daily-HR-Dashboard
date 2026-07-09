"""
Daily HR League dashboard generator.

Pulls season HR totals + last night's batting lines for every rostered player
from the free MLB Stats API, computes league standings, and renders:
  - docs/index.html      (live dashboard, served by GitHub Pages)
  - docs/dashboard.png   (phone-friendly image for the group chat)
  - docs/data.json       (raw snapshot, useful for future features)

Run daily via GitHub Actions. No API keys required.
"""

import json
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API = "https://statsapi.mlb.com/api/v1"
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------- helpers ---

def norm(name: str) -> str:
    """Normalize a player name for matching (strip accents, punctuation, case)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


def get(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------ data access ---

def lookup_player_ids(names: list[str], season: int) -> dict[str, int]:
    """Map roster names -> MLBAM player IDs using the full active-player list."""
    data = get(f"{API}/sports/1/players", {"season": season, "gameType": "R"})
    directory = {norm(p["fullName"]): p["id"] for p in data.get("people", [])}
    ids, missing = {}, []
    for name in names:
        pid = directory.get(norm(name))
        if pid is None:
            # fallback: loose match on last name + first initial
            key = norm(name)
            candidates = [v for k, v in directory.items() if key[-6:] in k]
            pid = candidates[0] if len(candidates) == 1 else None
        if pid is None:
            missing.append(name)
        else:
            ids[name] = pid
    if missing:
        print(f"WARNING: could not resolve player IDs for: {missing}", file=sys.stderr)
    return ids


def fetch_stats(ids: dict[str, int], season: int, game_date: str) -> dict[int, dict]:
    """One batched call: season HR totals + batting line for game_date."""
    hydrate = (
        f"stats(group=[hitting],type=[season,byDateRange],"
        f"startDate={game_date},endDate={game_date},season={season})"
    )
    data = get(f"{API}/people", {
        "personIds": ",".join(str(i) for i in ids.values()),
        "hydrate": hydrate,
    })
    out = {}
    for person in data.get("people", []):
        season_hr, line = 0, None
        for block in person.get("stats", []):
            btype = block.get("type", {}).get("displayName", "")
            splits = block.get("splits", [])
            if not splits:
                continue
            stat = splits[0].get("stat", {})
            if btype == "season":
                season_hr = int(stat.get("homeRuns", 0) or 0)
            elif btype == "byDateRange":
                line = {
                    "ab":  int(stat.get("atBats", 0) or 0),
                    "h":   int(stat.get("hits", 0) or 0),
                    "hr":  int(stat.get("homeRuns", 0) or 0),
                    "rbi": int(stat.get("rbi", 0) or 0),
                    "bb":  int(stat.get("baseOnBalls", 0) or 0),
                    "k":   int(stat.get("strikeOuts", 0) or 0),
                    "r":   int(stat.get("runs", 0) or 0),
                }
        out[person["id"]] = {"season_hr": season_hr, "line": line}
    return out


# -------------------------------------------------------------- computing ---

def night_score(line: dict) -> float:
    return line["hr"] * 8 + line["h"] * 2 + line["rbi"] * 1.5 + line["r"] + line["bb"] * 0.5


def build_state(league: dict, ids: dict[str, int], stats: dict[int, dict],
                game_date: str) -> dict:
    teams, played = [], []
    for team in league["teams"]:
        roster = []
        for p in team["players"]:
            pid = ids.get(p["name"])
            s = stats.get(pid, {"season_hr": p["start_hr"], "line": None}) if pid else \
                {"season_hr": p["start_hr"], "line": None}
            row = {
                **p,
                "season_hr": s["season_hr"],
                "gained": s["season_hr"] - p["start_hr"],
                "line": s["line"],
                "hr_last_night": (s["line"] or {}).get("hr", 0),
            }
            roster.append(row)
            if row["line"] and row["line"]["ab"] > 0:
                played.append({**row, "team_name": team["name"]})
        teams.append({
            "name": team["name"],
            "start_total": team["start_total"],
            "season_total": sum(r["season_hr"] for r in roster),
            "gained": sum(r["gained"] for r in roster),
            "hr_last_night": sum(r["hr_last_night"] for r in roster),
            "roster": roster,
        })
    teams.sort(key=lambda t: t["season_total"], reverse=True)

    potn = max(played, key=lambda p: night_score(p["line"]), default=None)
    hitless = [p for p in played if p["line"]["h"] == 0 and p["line"]["ab"] >= 3]
    bad = max(hitless, key=lambda p: (p["line"]["ab"], p["line"]["k"]), default=None) \
        if hitless else (min(played, key=lambda p: night_score(p["line"]), default=None))
    if potn and night_score(potn["line"]) <= 0:
        potn = None

    return {
        "league_name": league["league_name"],
        "game_date": game_date,
        "generated_at": datetime.now(EASTERN).strftime("%b %d, %Y %I:%M %p ET"),
        "teams": teams,
        "potn": potn,
        "bad_day": bad,
        "any_games": bool(played),
    }


# -------------------------------------------------------------- rendering ---

def fmt_line(line: dict | None) -> str:
    if not line:
        return "No game"
    if line["ab"] == 0 and line["bb"] == 0:
        return "Did not bat"
    bits = f"{line['h']}-{line['ab']}"
    extras = []
    if line["hr"]:
        extras.append(f"{line['hr']} HR")
    if line["rbi"]:
        extras.append(f"{line['rbi']} RBI")
    if line["bb"]:
        extras.append(f"{line['bb']} BB")
    if line["k"]:
        extras.append(f"{line['k']} K")
    return bits + (", " + ", ".join(extras) if extras else "")


def render_html(state: dict) -> str:
    date_label = datetime.strptime(state["game_date"], "%Y-%m-%d").strftime("%A, %B %d")
    rank_labels = ["1ST", "2ND", "3RD", "4TH", "5TH"]

    def banner(tag, cls, p, extra=""):
        if not p:
            return ""
        return f"""
        <div class="banner {cls}">
          <span class="banner-tag">{tag}</span>
          <span class="banner-name">{p['name']}</span>
          <span class="banner-team">{p['team_name']}</span>
          <span class="banner-line">{fmt_line(p['line'])}{extra}</span>
        </div>"""

    teams_html = ""
    for i, t in enumerate(state["teams"]):
        rows = ""
        for r in t["roster"]:
            hr_pill = f'<span class="hrpill">HR&thinsp;&times;{r["hr_last_night"]}</span>' \
                if r["hr_last_night"] else ""
            rows += f"""
            <div class="row{' rowhr' if r['hr_last_night'] else ''}">
              <span class="pos">{r['pos']}</span>
              <span class="pname">{r['name']}<em>{r['mlb_team']}</em></span>
              <span class="pline">{fmt_line(r['line'])}{hr_pill}</span>
              <span class="phr">{r['season_hr']}<em>+{r['gained']}</em></span>
            </div>"""
        lead = ' teamlead' if i == 0 else ''
        night = f'<span class="nighthr">+{t["hr_last_night"]} last night</span>' \
            if t["hr_last_night"] else ''
        teams_html += f"""
        <section class="team{lead}">
          <header class="teamhead">
            <span class="rank">{rank_labels[i]}</span>
            <h2>{t['name']}</h2>
            {night}
            <div class="total"><span class="led">{t['season_total']}</span>
              <span class="totlbl">HR &middot; +{t['gained']} since draft</span></div>
          </header>
          <div class="cols"><span>POS</span><span>PLAYER</span><span>LAST NIGHT</span><span>HR / +</span></div>
          {rows}
        </section>"""

    no_games = '<div class="banner nogames">No games last night — scoreboard unchanged.</div>' \
        if not state["any_games"] else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{state['league_name']}</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Chivo+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --field:#0C1B12; --panel:#14261A; --hair:#29402F; --led:#FFB627;
    --chalk:#F1EDE2; --flare:#FF4E2E; --dim:#8FA694;
  }}
  * {{ box-sizing:border-box; margin:0; }}
  body {{ background:var(--field); color:var(--chalk);
         font:15px/1.4 "Chivo Mono", ui-monospace, monospace;
         width:880px; margin:0 auto; padding:28px 24px 36px; }}
  h1 {{ font-family:"Archivo Black", system-ui, sans-serif; font-size:34px;
       letter-spacing:.5px; text-transform:uppercase; }}
  .masthead {{ display:flex; align-items:baseline; justify-content:space-between;
               border-bottom:3px solid var(--led); padding-bottom:12px; }}
  .date {{ color:var(--dim); font-size:14px; text-align:right; }}
  .banner {{ display:flex; gap:14px; align-items:baseline; padding:12px 16px;
             margin-top:14px; border:1px solid var(--hair); background:var(--panel); }}
  .banner-tag {{ font-family:"Archivo Black",sans-serif; font-size:12px;
                 letter-spacing:1px; padding:3px 8px; }}
  .potn .banner-tag {{ background:var(--led); color:var(--field); }}
  .badday .banner-tag {{ background:var(--hair); color:var(--chalk); }}
  .banner-name {{ font-weight:700; font-size:17px; }}
  .banner-team {{ color:var(--dim); font-size:13px; }}
  .banner-line {{ margin-left:auto; color:var(--led); }}
  .badday .banner-line {{ color:var(--dim); }}
  .nogames {{ color:var(--dim); justify-content:center; }}
  .team {{ margin-top:22px; border:1px solid var(--hair); background:var(--panel); }}
  .teamlead {{ border-color:var(--led); box-shadow:0 0 0 1px var(--led); }}
  .teamhead {{ display:flex; align-items:center; gap:14px;
               padding:14px 18px 10px; border-bottom:1px solid var(--hair); }}
  .rank {{ font-family:"Archivo Black",sans-serif; font-size:13px; color:var(--dim); }}
  .teamlead .rank {{ color:var(--led); }}
  .teamhead h2 {{ font-family:"Archivo Black",sans-serif; font-size:22px;
                  text-transform:uppercase; letter-spacing:.5px; }}
  .nighthr {{ color:var(--flare); font-size:13px; font-weight:700; }}
  .total {{ margin-left:auto; text-align:right; }}
  .led {{ font-family:"Chivo Mono",monospace; font-weight:700; font-size:40px;
          color:var(--led); text-shadow:0 0 14px rgba(255,182,39,.45); line-height:1; }}
  .totlbl {{ display:block; color:var(--dim); font-size:11px; margin-top:2px; }}
  .cols, .row {{ display:grid; grid-template-columns:44px 1fr 1.15fr 92px;
                 gap:10px; padding:8px 18px; align-items:baseline; }}
  .cols {{ color:var(--dim); font-size:10px; letter-spacing:1.5px;
           border-bottom:1px solid var(--hair); }}
  .row {{ border-bottom:1px solid rgba(41,64,47,.5); }}
  .row:last-child {{ border-bottom:none; }}
  .rowhr {{ background:rgba(255,182,39,.08); }}
  .pos {{ color:var(--dim); font-size:12px; }}
  .pname {{ font-weight:700; }}
  .pname em, .phr em {{ display:block; font-style:normal; color:var(--dim);
                        font-size:11px; font-weight:400; }}
  .pline {{ font-size:13.5px; color:#C9D6C6; }}
  .hrpill {{ background:var(--led); color:var(--field); font-weight:700;
             font-size:11px; padding:2px 7px; margin-left:8px; border-radius:2px; }}
  .rowhr .pline {{ color:var(--chalk); }}
  .phr {{ text-align:right; font-weight:700; font-size:18px; }}
  footer {{ color:var(--dim); font-size:11px; margin-top:18px; text-align:center; }}
</style></head><body>
  <div class="masthead">
    <h1>{state['league_name']}</h1>
    <div class="date">{date_label}<br>every homer is a point</div>
  </div>
  {no_games}
  {banner('PLAYER OF THE NIGHT', 'potn', state['potn'])}
  {banner('BAD DAY AT THE PLATE', 'badday', state['bad_day'])}
  {teams_html}
  <footer>Updated {state['generated_at']} &middot; data: MLB Stats API</footer>
</body></html>"""


def screenshot(html_path: Path, png_path: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 880, "height": 900},
                                device_scale_factor=2)
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(1200)  # let fonts load
        page.screenshot(path=str(png_path), full_page=True)
        browser.close()


# ------------------------------------------------------------------- main ---

def main() -> int:
    league = json.loads((ROOT / "league.json").read_text())
    season = league["season"]
    yesterday = (datetime.now(EASTERN) - timedelta(days=1)).strftime("%Y-%m-%d")

    names = [p["name"] for t in league["teams"] for p in t["players"]]
    ids = lookup_player_ids(names, season)
    stats = fetch_stats(ids, season, yesterday)
    state = build_state(league, ids, stats, yesterday)

    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(render_html(state))
    (DOCS / "data.json").write_text(json.dumps(state, indent=2, default=str))
    print(f"Rendered dashboard for {yesterday}: "
          + ", ".join(f"{t['name']} {t['season_total']}" for t in state["teams"]))

    try:
        screenshot(DOCS / "index.html", DOCS / "dashboard.png")
        print("Screenshot saved to docs/dashboard.png")
    except Exception as e:  # HTML still ships even if the PNG step hiccups
        print(f"WARNING: screenshot failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
