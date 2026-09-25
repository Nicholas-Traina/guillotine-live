"""
Live Guillotine standings engine.

Combines
  * Sleeper's live matchup scores (already scored with the league's own settings, incl. return yards),
  * the ESPN scoreboard (game state + clock -> fraction of each NFL game still to play), and
  * the model's weekly projections exported by "Guillotine Week Projection 5.ipynb" (live_inputs_<year>_wk<week>.json)

into, for every live team:
    expected final = points so far + sum over starters of (fraction of game left x model projection)
    uncertainty    = sqrt( sum over starters of sigma_position^2 x fraction of game left )
and simulates the week to get P(last), P(2nd-to-last), P(eliminated) and P(first place).

Run `python guillotine_live.py` for a one-shot text table, or `streamlit run live_app.py` for the live page.
"""
import glob
import json
import math
import os
import time

import numpy as np
import pandas as pd
import requests

SLEEPER = "https://api.sleeper.app/v1"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_TO_SLEEPER = {"WSH": "WAS"}        # the only abbreviation that differs
HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ data fetching
def _get(url, params=None, timeout=20):
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def nfl_state():
    """{'season': 2026, 'week': 3, ...} according to Sleeper."""
    s = _get(f"{SLEEPER}/state/nfl")
    return {"season": int(s["season"]), "week": int(s["week"]), "season_type": s.get("season_type")}


def fetch_matchups(league_id, week):
    return _get(f"{SLEEPER}/league/{league_id}/matchups/{week}") or []


def fraction_left(state, period, clock_seconds):
    """Fraction of an NFL game still to be played (regulation = 60 minutes)."""
    if state == "pre":
        return 1.0
    if state == "post":
        return 0.0
    period = int(period or 0)
    clock_seconds = float(clock_seconds or 0.0)
    if period <= 0:
        return 1.0
    if period <= 4:                                   # quarters 1-4 (halftime = period 2, clock 0)
        return ((4 - period) * 900.0 + clock_seconds) / 3600.0
    return min(clock_seconds, 600.0) / 3600.0         # overtime (10 minutes in the regular season)


SLEEPER_STATUS = {"pre_game": "pre", "in_game": "in", "complete": "post"}


def fetch_game_states_sleeper(season, week):
    """Fallback when ESPN is unavailable: Sleeper only knows pre_game / in_game / complete (no clock), so a game in
    progress is assumed to be half over. Marked with estimated=True so the page can warn about it."""
    games = {}
    for g in _get(f"https://api.sleeper.com/schedule/nfl/regular/{season}"):
        if g.get("week") != week:
            continue
        state = SLEEPER_STATUS.get(g.get("status"), "pre")
        f = {"pre": 1.0, "in": 0.5, "post": 0.0}[state]
        for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
            games[team] = {"state": state, "fraction_left": f, "detail": {"pre": "not started", "in": "in progress (clock unavailable)", "post": "final"}[state],
                           "opponent": opp, "home": home, "score": None, "opp_score": None, "kickoff": g.get("date"), "estimated": True}
    return games


def fetch_game_states(season, week):
    """{sleeper_team_abbr: {state, fraction_left, detail, opponent, home, score, opp_score, kickoff, estimated}} for the week.
    Uses the ESPN scoreboard (has the game clock); falls back to Sleeper's coarser status if ESPN fails."""
    try:
        data = _get(ESPN_SCOREBOARD, params={"week": week, "seasontype": 2, "dates": season})
        if not data.get("events"):
            raise ValueError("ESPN returned no games")
    except Exception:
        return fetch_game_states_sleeper(season, week)
    games = {}
    for ev in data.get("events", []):
        st = ev["status"]
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        f = fraction_left(st["type"]["state"], st.get("period"), st.get("clock"))
        for side, other in (("home", "away"), ("away", "home")):
            abbr = teams[side]["team"]["abbreviation"]
            abbr = ESPN_TO_SLEEPER.get(abbr, abbr)
            games[abbr] = {
                "state": st["type"]["state"], "fraction_left": f, "detail": st["type"].get("shortDetail") or st["type"].get("detail", ""),
                "opponent": ESPN_TO_SLEEPER.get(teams[other]["team"]["abbreviation"], teams[other]["team"]["abbreviation"]),
                "home": side == "home", "score": teams[side].get("score"), "opp_score": teams[other].get("score"), "kickoff": ev.get("date"),
                "estimated": False,
            }
    return games


DEFAULT_CONFIG = {"immune_teams": [], "eliminations_override": 0, "highlight_default": ""}


def load_config(folder=HERE):
    """Shared page settings from live_config.json (edit it to set this week's immune teams for everyone)."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(os.path.join(folder, "live_config.json"), encoding="utf-8") as fh:
            cfg.update({k: v for k, v in json.load(fh).items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass                                    # missing / unreadable file -> defaults
    cfg["immune_teams"] = [str(u) for u in (cfg["immune_teams"] or [])]
    cfg["eliminations_override"] = int(cfg["eliminations_override"] or 0)
    cfg["highlight_default"] = str(cfg["highlight_default"] or "")
    return cfg


def inputs_age_minutes(inputs):
    """Minutes since the projections were generated (uses the UTC epoch when the refresh job wrote one)."""
    import datetime
    if inputs.get("generated_epoch"):
        return (time.time() - float(inputs["generated_epoch"])) / 60.0
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(inputs["generated_at"])).total_seconds() / 60.0
    except Exception:
        return None


def find_inputs_file(season, week, folder=HERE):
    exact = os.path.join(folder, f"live_inputs_{season}_wk{week}.json")
    if os.path.exists(exact):
        return exact
    cands = sorted(glob.glob(os.path.join(folder, f"live_inputs_{season}_wk{week}*.json")), key=os.path.getmtime, reverse=True)
    return cands[0] if cands else None


def load_inputs(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------------ the math
def starter_table(matchup, inputs, games):
    """One row per starter of one team: points so far, game state, expected remaining and expected final."""
    players = inputs["players"]
    sigma = inputs["sigma_by_position"]
    global_sigma = inputs.get("global_rmse", 7.5)
    pos_mean = inputs.get("position_mean_projection", {})
    rows = []
    starters = matchup.get("starters") or []
    pts_list = matchup.get("starters_points") or [0.0] * len(starters)
    for pid, pts in zip(starters, pts_list):
        if pid in (None, "0"):
            continue
        info = players.get(pid)
        if info is None:                                       # picked up after the export: no projection available
            info = {"name": f"(new) {pid}", "position": "?", "team": None, "projected_points": float(np.mean(list(pos_mean.values())) if pos_mean else 8.0),
                    "availability": "NOT IN EXPORT"}
        team = info.get("team")
        g = games.get(team)
        f = g["fraction_left"] if g else 0.0                   # no game this week (bye) -> nothing left to play
        proj = float(info.get("projected_points") or 0.0)
        sig = float(sigma.get(info.get("position"), global_sigma))
        rows.append({
            "player_id": pid, "player": info["name"], "pos": info["position"], "nfl_team": team,
            "game": (g["detail"] if g else "BYE"), "state": (g["state"] if g else "bye"),
            "pts_so_far": float(pts or 0.0), "fraction_left": f, "projection": proj,
            "expected_remaining": f * proj, "expected_final": float(pts or 0.0) + f * proj,
            "var_remaining": (sig ** 2) * f if proj > 0 else 0.0, "flag": info.get("availability", ""),
        })
    return pd.DataFrame(rows)


def live_team_table(matchups, inputs, games):
    """One row per live team (teams with an empty roster were already eliminated)."""
    owners = inputs["owners"]
    out, details = [], {}
    for m in matchups:
        if not m.get("players"):
            continue
        rid = m["roster_id"]
        st = starter_table(m, inputs, games)
        details[rid] = st
        cur = float(m.get("points") or 0.0)
        mu = float(st["expected_remaining"].sum()) if len(st) else 0.0
        var = float(st["var_remaining"].sum()) if len(st) else 0.0
        active = st[st["projection"] > 0] if len(st) else st
        progress = float(1.0 - active["fraction_left"].mean()) if len(active) else 1.0
        out.append({"roster_id": rid, "owner": owners.get(str(rid), f"team {rid}"), "current": cur, "expected_remaining": mu,
                    "expected_final": cur + mu, "sd_remaining": math.sqrt(var), "progress": progress,
                    "starters_done": int((st["state"].isin(["post", "bye"])).sum()) if len(st) else 0, "n_starters": len(st)})
    return pd.DataFrame(out), details


def simulate_standings(teams, k, immune_owners=(), n_sims=20000, seed=42):
    """Adds P(last), P(2nd-to-last), P(eliminated), P(first place) to the team table."""
    t = teams.copy().reset_index(drop=True)
    T = len(t)
    if T == 0:
        return t
    rng = np.random.default_rng(seed)
    Z = rng.standard_normal((T, n_sims))
    remaining = np.maximum(t["expected_remaining"].values[:, None] + t["sd_remaining"].values[:, None] * Z, 0.0)
    total = t["current"].values[:, None] + remaining + rng.uniform(0, 1e-6, (T, n_sims))       # jitter breaks exact ties
    rank = total.argsort(axis=0).argsort(axis=0)                                                 # 0 = lowest score
    immune = t["owner"].str.lower().isin({u.lower() for u in immune_owners}).values
    k_eff = int(min(k, (~immune).sum()))
    rank_elig = np.where(immune[:, None], np.inf, total).argsort(axis=0).argsort(axis=0)
    t["p_last"] = (rank == 0).mean(axis=1)
    t["p_second_last"] = (rank == 1).mean(axis=1)
    t["p_eliminated"] = ((rank_elig < k_eff) & ~immune[:, None]).mean(axis=1)
    t["p_first"] = (rank == T - 1).mean(axis=1)
    t["immune"] = immune
    t["locked"] = (t["expected_remaining"] == 0) & (t["sd_remaining"] == 0)
    t["k"] = k_eff
    return t


def live_snapshot(league_id, season, week, inputs, immune_owners=(), k=None, n_sims=20000):
    """Everything the page needs in one call."""
    matchups = fetch_matchups(league_id, week)
    games = fetch_game_states(season, week)
    teams, details = live_team_table(matchups, inputs, games)
    if k is None:
        k = int(inputs.get("eliminations_by_week", {}).get(str(week), 1))
    standings = simulate_standings(teams, k, immune_owners, n_sims)
    estimated = any(g.get("estimated") for g in games.values())
    return {"standings": standings, "details": details, "games": games, "k": k,
            "clock_source": "Sleeper status only (no game clock - games in progress assumed half done)" if estimated else "ESPN scoreboard",
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}


# ------------------------------------------------------------------ one-shot CLI
if __name__ == "__main__":
    import sys
    st = nfl_state()
    path = find_inputs_file(st["season"], st["week"])
    if not path:
        sys.exit(f"No live_inputs_{st['season']}_wk{st['week']}.json found next to this file. Run the projection notebook first.")
    inputs = load_inputs(path)
    snap = live_snapshot(inputs["league_id"], st["season"], st["week"], inputs)
    s = snap["standings"].sort_values("p_eliminated", ascending=False)
    pd.set_option("display.width", 200)
    print(f"{st['season']} week {st['week']} | {snap['fetched_at']} | {snap['k']} eliminated | inputs: {os.path.basename(path)}")
    print(s[["owner", "current", "expected_final", "sd_remaining", "progress", "p_last", "p_eliminated", "p_first"]].round(3).to_string(index=False))
