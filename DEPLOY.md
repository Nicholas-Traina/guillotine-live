# Hosting the Guillotine live page for your league

This folder is a complete, self-contained package: the Streamlit page, the hourly projection refresh, and the
GitHub Action that runs it. Nothing in it needs your PC once it is deployed.

## One-time setup (about 15 minutes)

1. **GitHub account + new repository.** Create a repo (for example `guillotine-live`).
   *Public* is simplest (unlimited free Action minutes, and Streamlit Community Cloud can deploy it directly).
   *Private* also works, but see "Privacy and limits" below.
2. **Push this folder** (run these inside `guillotine_live_deploy`):
   ```
   git init -b main
   git add .
   git commit -m "Guillotine live page"
   git remote add origin https://github.com/<your-username>/guillotine-live.git
   git push -u origin main
   ```
3. **Deploy the page.** Go to https://share.streamlit.io, sign in with GitHub, choose **Create app**, pick the repo,
   branch `main`, main file `live_app.py`, then Deploy. Send your leaguemates the URL it gives you.
4. **Turn on the hourly refresh.** In the repo open the **Actions** tab, enable workflows if asked, choose
   **refresh-live-inputs** and press **Run workflow** once to check it works (it takes about 2-3 minutes and
   commits a fresh `live_inputs_<season>_wk<week>.json`). After that it runs every hour by itself.
5. **Stop the PC-based schedule** if you set one up (otherwise both run):
   `Unregister-ScheduledTask -TaskName GuillotineLiveRefresh -Confirm:$false`

## Every week

* **Immune teams / double cuts:** open `live_config.json` on GitHub, click the pencil icon, edit, and commit.
  `immune_teams` takes usernames (not case-sensitive); `eliminations_override` is 0 to follow the league's schedule
  (1 per week, 2 in weeks 2, 4, 5, 9, 12, 14) or the number cut this week. The page picks it up within a minute or two.
  Viewers can still change these in their own sidebar; that only changes their own view.
* Nothing else. The refresh job picks up the new NFL week on its own. If a starter was added since the last hourly
  refresh, the page says so and uses the average starter projection for him until the next refresh.

## New season

Open the notebook copy in this folder and add last season to the `YEARS` list in the pipeline cell (the model
trains on past seasons), then commit. Everything else follows Sleeper's current season automatically.

## Privacy and limits

* A **public repo** exposes the code plus the league's usernames and projections (the usernames are already visible
  through Sleeper's public API). If that bothers you, use a private repo. Streamlit Community Cloud limits private apps,
  and GitHub gives private repos a monthly allowance of Action minutes: narrow the cron in
  `.github/workflows/refresh-live-inputs.yml` to game days if you hit it.
* Streamlit Community Cloud puts apps to sleep after ~12 hours without visitors; the next visitor wakes it in a few
  seconds. Its limits can change, so check their docs.
* GitHub scheduled workflows are best-effort: runs can be 5-30+ minutes late. The page shows how old the projections
  are and warns if they are more than 3 hours old. Live scores always come straight from Sleeper and ESPN.
* If a refresh run fails (Sleeper outage, incomplete data), the job refuses to publish and the previous file stays in
  place. Look at the failed run's log in the Actions tab.

## Files

| File | Purpose |
|---|---|
| `live_app.py`, `guillotine_live.py` | The page and its engine |
| `live_config.json` | Shared settings: immune teams, cut override, default highlighted team |
| `live_inputs_<season>_wk<week>.json` | The model's projections (rewritten hourly) |
| `refresh_live_inputs.py` + `Guillotine Week Projection 5.ipynb` | Rebuilds the projections (runs the notebook's own code) |
| `adp_cache/` | Last good ADP lists (fallback if the ADP site is down) |
| `.github/workflows/refresh-live-inputs.yml` | The hourly job |
| `requirements.txt` / `requirements-refresh.txt` | Dependencies for the page / for the refresh job |
