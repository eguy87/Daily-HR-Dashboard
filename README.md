# HR Derby League — Daily Dashboard

Every morning at 7:45 AM ET, a GitHub Action pulls last night's box scores from the free MLB Stats API, updates the league scoreboard, and publishes:

- **Live dashboard:** `https://<your-username>.github.io/<repo-name>/`
- **Image for the group chat:** `https://<your-username>.github.io/<repo-name>/dashboard.png`

Scoring: a team's score is its total season HRs. The dashboard also shows `+X since draft` for every player and team, computed against the `start_hr` baselines in `config/league.json`.

## Run locally

Requirements: Python 3.10+ and PowerShell.

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

The launcher refreshes the MLB data, serves `docs/` at
`http://127.0.0.1:8000`, and opens it in your browser. Later, use
`.\scripts\start.ps1 -SkipBuild` to serve the last generated dashboard without
calling the MLB API. Choose another port with
`.\scripts\start.ps1 -Port 8080`.

On macOS/Linux, run the equivalent commands directly:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python app/update.py
python scripts/serve.py --open
```

## GitHub setup (~15 min)

1. **Create the repo.** On github.com: New repository (public — required for free Pages) → upload these files, keeping the folder structure (`.github/workflows/daily.yml` must keep its path).
2. **Enable Pages.** Repo → Settings → Pages → Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save.
3. **Allow the Action to push.** Settings → Actions → General → Workflow permissions → select *Read and write permissions* → Save.
4. **First run.** Actions tab → *Daily HR Dashboard* → *Run workflow*. Takes ~2 min. Then open your Pages URL to see the scoreboard. This first run also gives you the true current HR total for every player.
5. **Check the baselines.** If any player's `+X since draft` looks wrong, fix his `start_hr` in `config/league.json` (edit directly on GitHub). The season totals from the API are always authoritative.

## Auto-send to the group chat (iPhone Shortcuts)

1. Shortcuts app → **Shortcuts tab** → + → add these actions:
   - **Get Contents of URL** → paste your `.../dashboard.png` URL
   - **Send Message** → tap the input variable, set recipient to your group chat
   - Name it "HR League".
2. **Automation tab** → + → *Time of Day* → 8:00 AM, Daily → Next → choose **Run Shortcut → HR League** → set to **Run Immediately** (no confirmation prompt).

Done — the fresh image lands in the chat every morning after the 7:45 rebuild. (Note: "Run Immediately" for Send Message may still show a brief confirmation on some iOS versions; sending as an image attachment works in both iMessage and mixed groups.)

## Editing the league

Everything lives in `config/league.json` — add/drop players, fix spellings (must match the player's official MLB name closely), or adjust `start_hr` baselines. The next run picks it up automatically.

## How players are matched

`app/update.py` downloads the full 2026 MLB player directory and matches your roster names (accent- and punctuation-insensitive). If a name can't be matched, the Action log prints a `WARNING` and the player shows "No game" with his baseline HR count — check the Actions log after the first run.

## Project layout

| Path | Purpose |
|---|---|
| `app/` | Dashboard generator source code |
| `config/` | League roster and scoring configuration |
| `scripts/` | Local setup, build, and localhost commands |
| `documentation/` | Detailed project and maintenance notes |
| `docs/` | Generated GitHub Pages site and dashboard data |
| `.github/workflows/` | Scheduled GitHub Actions automation |
