"""
Daily HR League dashboard generator — v2.

v2 adds:
  - Pace projections: each team's projected end-of-season HR total
  - Streak & drought tracker: "HR in 3 straight" / "14 games w/o HR"
  - Sunday Weekly Recap: week's top HR hitters + team weekly totals

Outputs (committed by the GitHub Action):
  - docs/index.html      (live dashboard, served by GitHub Pages)
  - docs/dashboard.png   (phone-friendly image for the group chat)
  - docs/data.json       (raw snapshot)

Data: free MLB Stats API. No keys required.
"""

import json
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API = "https://statsapi.mlb.com/api/v1"
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
EASTERN = ZoneInfo("America/New_York")
SEASON_GAMES = 162


# ---------------------------------------------------------------- helpers ---

def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


def get(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------ data access ---

def lookup_player_ids(names: list[str], season: int) -> dict[str, int]:
    data = get(f"{API}/sports/1/players", {"season": season, "gameType": "R"})
    directory = {norm(p["fullName"]): p["id"] for p in data.get("people", [])}
    ids, missing = {}, []
    for name in names:
        pid = directory.get(norm(name))
        if pid is None:
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
    """One batched call: season totals + last-night line + full game log."""
    hydrate = (
        f"stats(group=[hitting],type=[season,byDateRange,gameLog],"
        f"startDate={game_date},endDate={game_date},season={season})"
    )
    data = get(f"{API}/people", {
        "personIds": ",".join(str(i) for i in ids.values()),
        "hydrate": hydrate,
    })
    out = {}
    for person in data.get("people", []):
        season_hr, line, games = 0, None, []
        season_line = {"h": 0, "avg": ".000", "rbi": 0, "k": 0}
        for block in person.get("stats", []):
            btype = block.get("type", {}).get("displayName", "")
            splits = block.get("splits", [])
            if btype == "season" and splits:
                stat = splits[0]["stat"]
                season_hr = int(stat.get("homeRuns", 0) or 0)
                season_line = {
                    "h":   int(stat.get("hits", 0) or 0),
                    "avg": stat.get("avg", ".000") or ".000",
                    "rbi": int(stat.get("rbi", 0) or 0),
                    "k":   int(stat.get("strikeOuts", 0) or 0),
                }
            elif btype == "byDateRange" and splits:
                stat = splits[0]["stat"]
                line = {
                    "ab":  int(stat.get("atBats", 0) or 0),
                    "h":   int(stat.get("hits", 0) or 0),
                    "hr":  int(stat.get("homeRuns", 0) or 0),
                    "rbi": int(stat.get("rbi", 0) or 0),
                    "bb":  int(stat.get("baseOnBalls", 0) or 0),
                    "k":   int(stat.get("strikeOuts", 0) or 0),
                    "r":   int(stat.get("runs", 0) or 0),
                }
            elif btype == "gameLog":
                games = [
                    {
                        "date": s.get("date", ""),
                        "hr": int(s["stat"].get("homeRuns", 0) or 0),
                        "ab": int(s["stat"].get("atBats", 0) or 0),
                    }
                    for s in splits
                ]
        games.sort(key=lambda g: g["date"])
        out[person["id"]] = {"season_hr": season_hr, "line": line,
                             "games": games, "season_line": season_line}
    return out


def league_avg_games_played(season: int) -> float:
    """Average games played across all 30 MLB teams (for pace projection)."""
    try:
        data = get(f"{API}/standings", {"leagueId": "103,104", "season": season})
        gp = [
            r["wins"] + r["losses"]
            for rec in data.get("records", [])
            for r in rec.get("teamRecords", [])
        ]
        return sum(gp) / len(gp) if gp else 0.0
    except Exception as e:
        print(f"WARNING: standings fetch failed ({e}); pace disabled", file=sys.stderr)
        return 0.0


# -------------------------------------------------------------- computing ---

def night_score(line: dict) -> float:
    return line["hr"] * 8 + line["h"] * 2 + line["rbi"] * 1.5 + line["r"] + line["bb"] * 0.5


def streaks(games: list[dict]) -> dict:
    """HR streak (consecutive games w/ HR) and drought (games since last HR)."""
    played = [g for g in games if g["ab"] > 0]
    streak = 0
    for g in reversed(played):
        if g["hr"] > 0:
            streak += 1
        else:
            break
    drought = 0
    for g in reversed(played):
        if g["hr"] > 0:
            break
        drought += 1
    return {"hr_streak": streak, "drought": drought}


def week_hr(games: list[dict], since: str) -> int:
    return sum(g["hr"] for g in games if g["date"] >= since)


def build_state(league: dict, ids: dict[str, int], stats: dict[int, dict],
                game_date: str, avg_gp: float) -> dict:
    now = datetime.now(EASTERN)
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    is_sunday = now.weekday() == 6

    teams, played, all_players = [], [], []
    for team in league["teams"]:
        roster = []
        for p in team["players"]:
            pid = ids.get(p["name"])
            s = stats.get(pid) if pid else None
            s = s or {"season_hr": p["start_hr"], "line": None, "games": [],
                      "season_line": {"h": 0, "avg": ".000", "rbi": 0, "k": 0}}
            row = {
                **p,
                "season_hr": s["season_hr"],
                "gained": s["season_hr"] - p["start_hr"],
                "line": s["line"],
                "hr_last_night": (s["line"] or {}).get("hr", 0),
                "week_hr": week_hr(s["games"], week_start),
                "season_line": s.get("season_line",
                                     {"h": 0, "avg": ".000", "rbi": 0, "k": 0}),
                **streaks(s["games"]),
            }
            roster.append(row)
            all_players.append({**row, "team_name": team["name"]})
            if row["line"] and row["line"]["ab"] > 0:
                played.append({**row, "team_name": team["name"]})
        season_total = sum(r["season_hr"] for r in roster)
        teams.append({
            "name": team["name"],
            "start_total": team["start_total"],
            "season_total": season_total,
            "gained": sum(r["gained"] for r in roster),
            "hr_last_night": sum(r["hr_last_night"] for r in roster),
            "week_hr": sum(r["week_hr"] for r in roster),
            "pace": round(season_total * SEASON_GAMES / avg_gp) if avg_gp else None,
            "roster": roster,
        })
    teams.sort(key=lambda t: t["season_total"], reverse=True)

    potn = max(played, key=lambda p: night_score(p["line"]), default=None)
    if potn and night_score(potn["line"]) <= 0:
        potn = None
    hitless = [p for p in played if p["line"]["h"] == 0 and p["line"]["ab"] >= 3]
    bad = max(hitless, key=lambda p: (p["line"]["ab"], p["line"]["k"]), default=None) \
        if hitless else (min(played, key=lambda p: night_score(p["line"]), default=None))

    weekly = None
    if is_sunday:
        top = sorted(all_players, key=lambda p: p["week_hr"], reverse=True)[:3]
        weekly = {
            "top_players": [p for p in top if p["week_hr"] > 0],
            "team_week": sorted(
                [{"name": t["name"], "week_hr": t["week_hr"]} for t in teams],
                key=lambda t: t["week_hr"], reverse=True),
        }

    # Race chart: reconstruct each team's cumulative season HR total by date
    all_dates = sorted({g["date"]
                        for s in stats.values() for g in s["games"] if g["date"]})
    race = {"dates": all_dates, "teams": []}
    for team in league["teams"]:
        pids = [ids.get(p["name"]) for p in team["players"]]
        daily = {d: 0 for d in all_dates}
        for pid in pids:
            for g in (stats.get(pid) or {}).get("games", []):
                if g["date"]:
                    daily[g["date"]] += g["hr"]
        series, run = [], 0
        for d in all_dates:
            run += daily[d]
            series.append(run)
        race["teams"].append({"name": team["name"], "series": series})

    return {
        "league_name": league["league_name"],
        "game_date": game_date,
        "race": race,
        "generated_at": now.strftime("%b %d, %Y %I:%M %p ET"),
        "teams": teams,
        "potn": potn,
        "bad_day": bad,
        "weekly": weekly,
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


def streak_tag(r: dict) -> str:
    if r["hr_streak"] >= 2:
        return f'<span class="tag hot">HR in {r["hr_streak"]} straight</span>'
    if r["drought"] >= 10:
        return f'<span class="tag cold">{r["drought"]} games w/o HR</span>'
    return ""


# ---------------------------------------------------------------- history ---

def load_history() -> dict:
    path = DOCS / "history.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            print("WARNING: history.json unreadable; starting fresh", file=sys.stderr)
    return {"dates": [], "totals": {}}


def update_history(history: dict, game_date: str, teams: list[dict]) -> dict:
    """Record each team's season total for game_date (safe to re-run same day)."""
    if game_date not in history["dates"]:
        history["dates"].append(game_date)
    idx = history["dates"].index(game_date)
    for t in teams:
        series = history["totals"].setdefault(t["name"], [])
        while len(series) <= idx:
            series.append(None)
        series[idx] = t["season_total"]
    return history


def race_from_history(history: dict, team_order: list[str]) -> dict:
    """Shape history into the chart's input, forward-filling any gaps."""
    dates = history["dates"]
    teams = []
    for name in team_order:
        raw = history["totals"].get(name, [])
        series, prev = [], None
        for i in range(len(dates)):
            v = raw[i] if i < len(raw) else None
            prev = v if v is not None else prev
            series.append(prev)
        first = next((v for v in series if v is not None), 0)
        series = [first if v is None else v for v in series]
        teams.append({"name": name, "series": series})
    return {"dates": dates, "teams": teams}


RACE_COLORS = ["#4ADE80", "#60A5FA", "#FBBF24", "#F472B6", "#A78BFA"]


def race_chart(race: dict) -> str:
    dates, teams = race.get("dates", []), race.get("teams", [])
    if len(dates) < 2 or not teams:
        return ""
    W, H, PL, PR, PT, PB = 1340, 300, 46, 150, 16, 30
    lo = min(min(t["series"]) for t in teams)
    hi = max(max(t["series"]) for t in teams)
    lo, hi = max(0, lo - 3), hi + 3
    def x(i): return PL + (W - PL - PR) * i / (len(dates) - 1)
    def y(v): return PT + (H - PT - PB) * (1 - (v - lo) / (hi - lo))
    grid, labels = "", ""
    for gv in range(int(lo) // 20 * 20 + 20, int(hi) + 1, 20):
        gy = y(gv)
        grid += f'<line x1="{PL}" y1="{gy:.1f}" x2="{W-PR}" y2="{gy:.1f}" stroke="var(--hair)" stroke-width="1"/>'
        labels += f'<text x="{PL-8}" y="{gy+4:.1f}" text-anchor="end" fill="var(--dim)" font-size="12">{gv}</text>'
    # month ticks
    seen = set()
    for i, d in enumerate(dates):
        m = d[:7]
        if m not in seen:
            seen.add(m)
            label = datetime.strptime(d, "%Y-%m-%d").strftime("%b")
            labels += f'<text x="{x(i):.1f}" y="{H-8}" fill="var(--dim)" font-size="12">{label}</text>'
    lines = ""
    order = sorted(range(len(teams)), key=lambda i: -teams[i]["series"][-1])
    used_y = []
    for rank, ti in enumerate(order):
        t, color = teams[ti], RACE_COLORS[ti % len(RACE_COLORS)]
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(t["series"]))
        lines += f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round"/>'
        ly = y(t["series"][-1])
        while any(abs(ly - u) < 18 for u in used_y):
            ly += 18
        used_y.append(ly)
        lines += (f'<text x="{W-PR+8}" y="{ly+4:.1f}" fill="{color}" font-size="14" '
                  f'font-weight="700">{t["name"]} {t["series"][-1]}</text>')
    return f"""
        <section class="race">
          <span class="racetitle">THE RACE &middot; SEASON HR TOTALS</span>
          <svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">
            {grid}{labels}{lines}
          </svg>
        </section>"""


TABS_JS = """
<script>
function showTab(id, btn) {
  document.querySelectorAll('.tabpane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tabbtn').forEach(el => el.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
function filterStats() {
  const q = document.getElementById('search').value.toLowerCase();
  const team = document.getElementById('teamfilter').value;
  document.querySelectorAll('#statsbody tr').forEach(tr => {
    const matchQ = tr.dataset.search.includes(q);
    const matchT = team === 'ALL' || tr.dataset.team === team;
    tr.style.display = (matchQ && matchT) ? '' : 'none';
  });
}
let sortState = { col: 3, dir: -1 };
function sortStats(col, type) {
  const dir = (sortState.col === col) ? -sortState.dir : (type === 'num' ? -1 : 1);
  sortState = { col: col, dir: dir };
  const body = document.getElementById('statsbody');
  const rows = Array.from(body.rows);
  rows.sort((a, b) => {
    const av = a.cells[col].dataset.v, bv = b.cells[col].dataset.v;
    return type === 'num' ? (av - bv) * dir : av.localeCompare(bv) * dir;
  });
  rows.forEach(r => body.appendChild(r));
  document.querySelectorAll('.statstable th').forEach((th, i) => {
    th.classList.toggle('sorted', i === col);
    th.classList.toggle('desc', i === col && dir === -1);
  });
}
</script>
"""


def render_html(state: dict) -> str:
    date_label = datetime.strptime(state["game_date"], "%Y-%m-%d").strftime("%A, %B %d")
    rank_labels = ["1ST", "2ND", "3RD", "4TH", "5TH"]

    def banner(tag, cls, p):
        if not p:
            return ""
        return f"""
        <div class="banner {cls}">
          <span class="banner-tag">{tag}</span>
          <div><span class="banner-name">{p['name']}</span>
          <span class="banner-team">{p['team_name']}</span><br>
          <span class="banner-line">{fmt_line(p['line'])}</span></div>
        </div>"""

    weekly_html = ""
    if state["weekly"]:
        rows = " &middot; ".join(
            f'{p["name"]} <b>{p["week_hr"]} HR</b>'
            for p in state["weekly"]["top_players"]) or "A quiet week at the plate."
        tw = " &middot; ".join(
            f'{t["name"]} +{t["week_hr"]}' for t in state["weekly"]["team_week"])
        weekly_html = f"""
        <section class="weekly">
          <span class="wktitle">SUNDAY EDITION &middot; WEEK IN HOMERS</span>
          <span>{rows}</span><span class="wkteams">{tw}</span>
        </section>"""

    stat_rows = ""
    for t in state["teams"]:
        for r in t["roster"]:
            sl = r["season_line"]
            avg_num = str(sl["avg"]).replace("—", "0") or "0"
            stat_rows += (
                f'<tr data-team="{t["name"]}" '
                f'data-search="{r["name"].lower()} {r["mlb_team"].lower()}">'
                f'<td data-v="{r["name"]}">{r["name"]}</td>'
                f'<td data-v="{t["name"]}">{t["name"].replace("Team ", "")}</td>'
                f'<td data-v="{r["mlb_team"]}">{r["mlb_team"]}</td>'
                f'<td data-v="{r["season_hr"]}" class="n hrcol">{r["season_hr"]}</td>'
                f'<td data-v="{sl["h"]}" class="n">{sl["h"]}</td>'
                f'<td data-v="{avg_num}" class="n">{sl["avg"]}</td>'
                f'<td data-v="{sl["rbi"]}" class="n">{sl["rbi"]}</td>'
                f'<td data-v="{sl["k"]}" class="n">{sl["k"]}</td></tr>')

    team_opts = "".join(f'<option value="{t["name"]}">{t["name"]}</option>'
                        for t in state["teams"])
    stats_tab = f"""
    <div class="statsbar">
      <input id="search" type="search" placeholder="Search player or MLB team…"
             oninput="filterStats()">
      <select id="teamfilter" onchange="filterStats()">
        <option value="ALL">All teams</option>{team_opts}
      </select>
    </div>
    <div class="tablewrap"><table class="statstable">
      <thead><tr>
        <th onclick="sortStats(0,'txt')">PLAYER</th>
        <th onclick="sortStats(1,'txt')">TEAM</th>
        <th onclick="sortStats(2,'txt')">MLB</th>
        <th onclick="sortStats(3,'num')" class="sorted desc">HR</th>
        <th onclick="sortStats(4,'num')">H</th>
        <th onclick="sortStats(5,'num')">AVG</th>
        <th onclick="sortStats(6,'num')">RBI</th>
        <th onclick="sortStats(7,'num')">K</th>
      </tr></thead>
      <tbody id="statsbody">{stat_rows}</tbody>
    </table></div>"""

    teams_html = ""
    for i, t in enumerate(state["teams"]):
        rows = ""
        for r in t["roster"]:
            hr_pill = f'<span class="hrpill">HR&thinsp;&times;{r["hr_last_night"]}</span>' \
                if r["hr_last_night"] else ""
            rows += f"""
            <div class="row{' rowhr' if r['hr_last_night'] else ''}">
              <span class="pos">{r['pos']}</span>
              <div class="mid">
                <span class="pname">{r['name']} <em>{r['mlb_team']}</em></span>
                <span class="pline">{fmt_line(r['line'])}{hr_pill}{streak_tag(r)}</span>
              </div>
              <span class="phr">{r['season_hr']}<em>+{r['gained']}</em></span>
            </div>"""
        lead = ' teamlead' if i == 0 else ''
        night = f'<span class="nighthr">+{t["hr_last_night"]} last night</span>' \
            if t["hr_last_night"] else ''
        pace = f'pace {t["pace"]}' if t["pace"] else ''
        teams_html += f"""
        <section class="team{lead}">
          <header class="teamhead">
            <div><span class="rank">{rank_labels[i]}</span>
            <h2>{t['name']}</h2>{night}</div>
            <div class="total"><span class="led">{t['season_total']}</span>
              <span class="totlbl">HR &middot; +{t['gained']} draft &middot; {pace}</span></div>
          </header>
          {rows}
        </section>"""

    no_games = '<div class="banner nogames">No games last night — scoreboard unchanged.</div>' \
        if not state["any_games"] else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{state['league_name']}</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Chivo+Mono:wght@400;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#101418">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="HR League">
<link rel="apple-touch-icon" href="icon.png">
<style>
  :root {{
    --field:#101418; --panel:#181E24; --hair:#2A333D; --led:#4ADE80;
    --chalk:#EDF2F7; --flare:#F87171; --dim:#8A97A5; --ice:#60A5FA;
  }}
  * {{ box-sizing:border-box; margin:0; }}
  body {{ background:var(--field); color:var(--chalk);
         font:16px/1.35 "Chivo Mono", ui-monospace, monospace;
         width:min(1420px, 100%); margin:0 auto; padding:26px 26px 30px; }}
  h1 {{ font-family:"Archivo Black", system-ui, sans-serif; font-size:40px;
       letter-spacing:.5px; text-transform:uppercase; }}
  .masthead {{ display:flex; align-items:baseline; justify-content:space-between;
               border-bottom:4px solid var(--led); padding-bottom:10px; }}
  .date {{ color:var(--dim); font-size:16px; text-align:right; }}
  .toprow {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
  .banner {{ display:flex; gap:14px; align-items:center; padding:12px 16px;
             border:1px solid var(--hair); background:var(--panel); }}
  .banner-tag {{ font-family:"Archivo Black",sans-serif; font-size:13px;
                 letter-spacing:1px; padding:4px 9px; white-space:nowrap; }}
  .potn .banner-tag {{ background:var(--led); color:var(--field); }}
  .badday .banner-tag {{ background:var(--hair); color:var(--chalk); }}
  .banner-name {{ font-weight:700; font-size:18px; }}
  .banner-team {{ color:var(--dim); font-size:14px; }}
  .banner-line {{ color:var(--led); font-size:15px; }}
  .badday .banner-line {{ color:var(--dim); }}
  .nogames {{ color:var(--dim); justify-content:center; grid-column:1 / -1; }}
  .weekly {{ margin-top:14px; border:1px solid var(--led); background:var(--panel);
             padding:10px 16px; display:flex; gap:18px; align-items:baseline;
             font-size:15px; flex-wrap:wrap; }}
  .wktitle {{ font-family:"Archivo Black",sans-serif; font-size:13px;
              color:var(--led); letter-spacing:1px; }}
  .weekly b {{ color:var(--led); }}
  .wkteams {{ color:var(--dim); font-size:13px; margin-left:auto; }}
  .grid3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-top:16px;
            align-items:start; }}
  .team {{ border:1px solid var(--hair); background:var(--panel); }}
  .teamlead {{ border-color:var(--led); box-shadow:0 0 0 1px var(--led); }}
  .teamhead {{ display:flex; align-items:center; justify-content:space-between;
               padding:12px 14px 10px; border-bottom:1px solid var(--hair); }}
  .rank {{ font-family:"Archivo Black",sans-serif; font-size:12px; color:var(--dim); }}
  .teamlead .rank {{ color:var(--led); }}
  .teamhead h2 {{ font-family:"Archivo Black",sans-serif; font-size:21px;
                  text-transform:uppercase; letter-spacing:.5px; }}
  .nighthr {{ display:block; color:var(--ice); font-size:13px; font-weight:700; }}
  .total {{ text-align:right; }}
  .led {{ font-weight:700; font-size:38px; color:var(--led);
          text-shadow:0 0 14px rgba(255,182,39,.45); line-height:1; }}
  .totlbl {{ display:block; color:var(--dim); font-size:11px; margin-top:2px;
             white-space:nowrap; }}
  .row {{ display:grid; grid-template-columns:34px 1fr 62px; gap:8px;
          padding:8px 14px; align-items:center;
          border-bottom:1px solid rgba(41,64,47,.5); }}
  .row:last-child {{ border-bottom:none; }}
  .rowhr {{ background:rgba(255,182,39,.09); }}
  .pos {{ color:var(--dim); font-size:12px; }}
  .mid {{ min-width:0; }}
  .pname {{ display:block; font-weight:700; font-size:15.5px; }}
  .pname em {{ font-style:normal; color:var(--dim); font-size:12px; font-weight:400; }}
  .pline {{ display:block; font-size:13px; color:#C9D6C6; margin-top:1px; }}
  .rowhr .pline {{ color:var(--chalk); }}
  .hrpill {{ background:var(--led); color:var(--field); font-weight:700;
             font-size:11px; padding:1px 7px; margin-left:7px; border-radius:2px; }}
  .tag {{ font-size:10.5px; padding:1px 6px; margin-left:7px; border-radius:2px;
          font-weight:700; white-space:nowrap; }}
  .hot {{ color:var(--flare); border:1px solid var(--flare); }}
  .cold {{ color:var(--ice); border:1px solid var(--ice); }}
  .phr {{ text-align:right; font-weight:700; font-size:20px; color:var(--ice); }}
  .phr em {{ display:block; font-style:normal; color:var(--dim); font-size:11px;
             font-weight:400; }}
  .race {{ margin-top:16px; border:1px solid var(--hair); background:var(--panel);
           padding:12px 16px 6px; display:block; }}
  .racetitle {{ font-family:"Archivo Black",sans-serif; font-size:13px;
                color:var(--led); letter-spacing:1px; display:block; margin-bottom:6px; }}
  .tabs {{ display:flex; gap:8px; margin-top:14px; }}
  .tabbtn {{ font:700 14px "Chivo Mono",monospace; letter-spacing:.5px;
             background:var(--panel); color:var(--dim); border:1px solid var(--hair);
             padding:8px 18px; cursor:pointer; }}
  .tabbtn.active {{ color:var(--field); background:var(--led); border-color:var(--led); }}
  .tabpane {{ display:none; }}
  .tabpane.active {{ display:block; }}
  .statsbar {{ display:flex; gap:10px; margin-top:14px; }}
  .statsbar input, .statsbar select {{
    font:14px "Chivo Mono",monospace; color:var(--chalk); background:var(--panel);
    border:1px solid var(--hair); padding:8px 12px; }}
  .statsbar input {{ flex:1; max-width:360px; }}
  .tablewrap {{ overflow-x:auto; margin-top:10px; border:1px solid var(--hair); }}
  .statstable {{ width:100%; border-collapse:collapse; background:var(--panel);
                 font-size:14px; }}
  .statstable th {{ font-family:"Archivo Black",sans-serif; font-size:11px;
                    letter-spacing:1px; color:var(--dim); text-align:left;
                    padding:10px 12px; border-bottom:2px solid var(--hair);
                    cursor:pointer; white-space:nowrap; user-select:none; }}
  .statstable th.sorted {{ color:var(--led); }}
  .statstable th.sorted::after {{ content:" ▲"; font-size:9px; }}
  .statstable th.sorted.desc::after {{ content:" ▼"; }}
  .statstable td {{ padding:8px 12px; border-bottom:1px solid rgba(42,51,61,.5);
                    color:var(--chalk); }}
  .statstable td.n {{ text-align:right; font-weight:700; }}
  .statstable td.hrcol {{ color:var(--ice); }}
  footer {{ color:var(--dim); font-size:12px; margin-top:14px; text-align:center; }}
  @media (max-width: 900px) {{
    body {{ padding:16px 12px 24px; font-size:15px; }}
    h1 {{ font-size:26px; }}
    .grid3, .toprow {{ grid-template-columns:1fr; }}
    .masthead {{ flex-direction:column; gap:2px; }}
    .date {{ text-align:left; }}
    .led {{ font-size:34px; }}
    .weekly {{ flex-direction:column; gap:4px; }}
    .wkteams {{ margin-left:0; }}
  }}
</style></head><body>
  <div class="masthead">
    <h1>{state['league_name']}</h1>
    <div class="date">{date_label}<br>every homer is a point</div>
  </div>
  <nav class="tabs">
    <button class="tabbtn active" onclick="showTab('tab-main', this)">Dashboard</button>
    <button class="tabbtn" onclick="showTab('tab-race', this)">The Race</button>
    <button class="tabbtn" onclick="showTab('tab-stats', this)">Stats</button>
  </nav>
  <div id="tab-main" class="tabpane active">
    {weekly_html}
    <div class="toprow">
    {no_games}
    {banner('PLAYER OF THE NIGHT', 'potn', state['potn'])}
    {banner('BAD DAY AT THE PLATE', 'badday', state['bad_day'])}
    </div>
    <div class="grid3">
    {teams_html}
    </div>
  </div>
  <div id="tab-race" class="tabpane">
    {race_chart(state.get("race") or dict()) or
     '<p class="racenote">The Race chart appears once two days of history exist.</p>'}
  </div>
  <div id="tab-stats" class="tabpane">
    {stats_tab}
  </div>
  <footer>Updated {state['generated_at']} &middot; data: MLB Stats API</footer>
  {TABS_JS}
</body></html>"""


def screenshot(html_path: Path, png_path: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1420, "height": 900},
                                device_scale_factor=2)
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(1200)
        page.screenshot(path=str(png_path), full_page=True)
        browser.close()


def make_icon(png_path: Path) -> None:
    """One-time 180x180 home-screen icon."""
    html = ("<body style='margin:0;width:180px;height:180px;background:#101418;"
            "display:flex;align-items:center;justify-content:center;"
            "font:900 64px Arial'><span style='color:#4ADE80;"
            "text-shadow:0 0 18px rgba(74,222,128,.6)'>HR</span></body>")
    tmp = png_path.parent / "_icon.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            page = b.new_page(viewport={"width": 180, "height": 180})
            page.goto(tmp.resolve().as_uri())
            page.screenshot(path=str(png_path))
            b.close()
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------- main ---

def main() -> int:
    league = json.loads((ROOT / "config" / "league.json").read_text(encoding="utf-8"))
    season = league["season"]
    yesterday = (datetime.now(EASTERN) - timedelta(days=1)).strftime("%Y-%m-%d")

    names = [p["name"] for t in league["teams"] for p in t["players"]]
    ids = lookup_player_ids(names, season)
    for t in league["teams"]:
        for p in t["players"]:
            if "mlbam_id" in p:
                ids[p["name"]] = p["mlbam_id"]

    stats = fetch_stats(ids, season, yesterday)
    avg_gp = league_avg_games_played(season)
    state = build_state(league, ids, stats, yesterday, avg_gp)

    DOCS.mkdir(exist_ok=True)
    history = update_history(load_history(), yesterday, state["teams"])
    (DOCS / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    state["race"] = race_from_history(history, [t["name"] for t in league["teams"]])
    (DOCS / "index.html").write_text(render_html(state), encoding="utf-8")
    (DOCS / "manifest.json").write_text(json.dumps({
        "name": state["league_name"], "short_name": "HR League",
        "start_url": ".", "display": "standalone",
        "background_color": "#101418", "theme_color": "#101418",
        "icons": [{"src": "icon.png", "sizes": "180x180", "type": "image/png"}],
    }, indent=2), encoding="utf-8")
    (DOCS / "data.json").write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8"
    )
    print(f"Rendered dashboard for {yesterday}: "
          + ", ".join(f"{t['name']} {t['season_total']}" for t in state["teams"]))

    try:
        screenshot(DOCS / "index.html", DOCS / "dashboard.png")
        if not (DOCS / "icon.png").exists():
            make_icon(DOCS / "icon.png")
        print("Screenshot saved to docs/dashboard.png")
    except Exception as e:
        print(f"WARNING: screenshot failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
