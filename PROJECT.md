# HR Derby League Dashboard — Project Summary

A zero-cost, fully automated daily dashboard for a three-team fantasy home run league. Rebuilds itself every morning from official MLB data, publishes as a web app anyone can add to their phone's home screen, and optionally texts the group a link or image.

**Live app:** https://eguy87.github.io/Daily-HR-Dashboard/
**Repo:** https://github.com/eguy87/Daily-HR-Dashboard

---

## League format

Three teams, nine drafted hitters each (one per position: DH, C, 1B, 2B, 3B, SS, RF, CF, LF). No transactions, no lineups, no waivers. **Every home run is one point.** The team with the most cumulative HRs at season's end wins.

Because the draft happened mid-season, each player has a `start_hr` baseline recorded in `league.json`. The dashboard shows both the player's true season total *and* how many he's added since the draft (`+X since draft`).

| Team | Starting total |
|---|---|
| Team Guy | 180 |
| Team Barmen | 191 |
| Team Dimisia | 172 |

---

## How it works

```
GitHub Actions (7:45 AM ET daily)
    ↓
update.py
    ├─ MLB Stats API → player IDs, season stats, last night's lines, game logs
    ├─ computes standings, streaks, pace, player of the night
    ├─ appends today's totals to docs/history.json  ← permanent record
    └─ writes docs/index.html, dashboard.png, data.json, manifest.json
    ↓
git commit + push
    ↓
GitHub Pages serves the updated app
    ↓
League mates open the home-screen icon → always current
```

Everything is free: GitHub Actions minutes (public repos are unlimited), GitHub Pages hosting, and the MLB Stats API (no key required).

### Files

| File | Purpose |
|---|---|
| `league.json` | Rosters, starting HR baselines, optional `mlbam_id` overrides. **The only file you normally edit.** |
| `update.py` | Everything: data fetching, calculations, HTML/CSS/JS rendering, screenshot. |
| `.github/workflows/daily.yml` | The 7:45 AM ET schedule and build steps. |
| `requirements.txt` | `requests`, `playwright`. |
| `docs/index.html` | The generated app (served by Pages). |
| `docs/dashboard.png` | Generated image snapshot for messaging. |
| `docs/history.json` | Accumulating daily team totals — powers The Race chart. Never delete this. |
| `docs/data.json` | Full raw snapshot of the day, useful for debugging or future features. |

---

## Features

### Dashboard tab
- Three team cards side by side, ranked, leader outlined in green
- Per team: season HR total, `+X since draft`, `+X last night` (light blue), end-of-season **pace projection**
- Per player: position, name, MLB team, last night's batting line, season HR count (light blue)
- HR nights highlighted with a gold row tint and `HR ×1` pill
- **Streak tags:** red `HR in 3 straight` (2+ games), blue `14 games w/o HR` (10+ games)
- **Player of the Night** — best line, weighted HR 8 / hit 2 / RBI 1.5 / run 1 / walk 0.5
- **Bad Day at the Plate** — worst hitless line (most ABs, most Ks)
- **Sunday Edition** — appears Sundays only: week's top 3 HR hitters plus each team's weekly total

### The Race tab
Line chart of all three teams' season totals over time, one color per team, current total labeled at each line's end. Builds from `history.json`, so it grows a day at a time and requires two days of history before appearing.

### Stats tab
Sortable, filterable table of all 27 players: fantasy team, MLB team, HR, hits, AVG, RBI, K. Search box matches player or MLB team; dropdown filters by fantasy team; every column header sorts (click again to reverse).

### App behavior
Responsive layout stacks to one column on phones. PWA tags mean Safari → Share → **Add to Home Screen** produces a full-screen "HR League" app with its own icon. Since the page rebuilds every morning, opening the icon always shows current data — no notification needed.

---

## Setup reference

**Repo configuration (already done):**
1. Public repo with the files above
2. Settings → Pages → Deploy from a branch → `main` / `/docs`
3. Settings → Actions → General → Workflow permissions → **Read and write**

**Share with league mates:**
> **HR League Dashboard:** https://eguy87.github.io/Daily-HR-Dashboard/
> 1. Open the link in **Safari**
> 2. Tap **Share** (square with arrow)
> 3. Scroll → **Add to Home Screen** → **Add**
>
> (Android: open in Chrome → ⋮ menu → Add to Home screen.)

**Optional iPhone auto-post:** Shortcuts → new shortcut with a **Send Message** action containing the dashboard URL → Automation tab → Time of Day, 8:00 AM daily → Run Shortcut → Run Immediately.

---

## Maintenance

**Editing anything:** open the file in GitHub → pencil icon → edit → Commit changes. To see results immediately rather than waiting for 7:45 AM: Actions → Daily HR Dashboard → **Run workflow**.

**Roster or baseline changes:** edit `league.json` only. Keep JSON syntax intact (quotes, commas). Season HR totals from the API are always authoritative — if a player's `+X since draft` looks wrong, the baseline is wrong, not the API.

**Duplicate player names:** if a player resolves to the wrong person, add his MLBAM ID to his entry in `league.json`:
```json
{ "pos": "3B", "name": "Max Muncy", "mlb_team": "Dodgers", "start_hr": 17, "mlbam_id": 571970 }
```
Find the ID in the player's mlb.com URL (`mlb.com/player/max-muncy-571970`).

**Colors:** the `:root` block near the top of the `<style>` section in `update.py` drives the entire theme. Current scheme is "Jumbotron": `--field` #101418 (background), `--panel` #181E24, `--hair` #2A333D, `--led` #4ADE80 (green accents), `--chalk` #EDF2F7 (text), `--flare` #F87171 (hot streaks), `--dim` #8A97A5, `--ice` #60A5FA (HR counts, droughts).

**Careful with:** the HTML in `render_html()` is inside a Python f-string, so all literal CSS braces must be doubled (`{{` and `}}`). Single braces will crash the run.

**Changing the schedule:** the cron line in `daily.yml` is UTC. `45 11 * * *` = 7:45 AM ET. Subtract 4 hours from ET during daylight saving, 5 in winter.

---

## Known issues and history

- **Ben Rice baseline** — the original draft sheet listed 29 HRs, but MLB.com showed him at 25 in early July. Verify against the current dashboard; a negative `+X since draft` means the baseline is still too high.
- **Junior Caminero baseline** — 26 was also worth double-checking against a cached ESPN snapshot showing a much lower figure.
- **Max Muncy** — two players share this name (Dodgers 3B and an Athletics infielder). Resolved via `mlbam_id` override; this is the template for any future name collision.
- **Push conflicts** — an early workflow failure (`[rejected] main -> main`) was fixed by adding `git pull --rebase origin main` before `git push`. If a run fails on the commit step again, that line is the thing to check.
- **Re-run vs. fresh run** — "Re-run failed jobs" replays the *old* code. After editing a workflow file, always use the **Run workflow** dropdown instead.
- **iOS caching** — Safari may briefly serve a stale page; pull down to refresh. A no-cache header can be added if it becomes a nuisance.

---

## Ideas not yet built

- Tap-to-expand player rows with last-10-game logs and an HR sparkline
- Tonight's matchups (opposing starter and handedness) from the schedule API
- Lead change log ("Barmen took the lead July 3, held it 5 days")
- Catch-up calculator with a scenario slider driven by pace data
- Auto-generated trash-talk headline from the night's results
- Leaderboard toggle re-sorting all 27 players by season / since-draft / weekly HRs
