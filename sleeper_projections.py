"""
Sleeper's own projections, scored with THIS league's rules, and the rule that decides when to use them instead of the model.

Rule (set by the league owner): if Sleeper projects a player below 8 points AND the player has no history of averaging more than
3 points per game from return yardage, use Sleeper's number instead of the model's. Returners are exempt because Sleeper's
projections barely include return yardage (punt-return yards for a few dozen players, no kick-return yards) while the league
scores it (0.1 pt per return yard).
"""
import time

import requests

URL = "https://api.sleeper.app/v1/projections/nfl/regular/{season}/{week}"
SLEEPER_BELOW = 8.0          # use Sleeper when its projection is below this many points ...
RETURN_PTS_PER_GAME = 3.0    # ... unless the player averages MORE than this many return-yardage points per game
HISTORY_SEASONS = 2          # return history = the previous season plus this season so far


def fetch_sleeper_projections(season, week, retries=3):
    """{player_id: projected stat dict} from Sleeper (empty dict if it can't be fetched)."""
    for attempt in range(retries):
        try:
            r = requests.get(URL.format(season=season, week=week), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and len(data) > 100:
                return data
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return {}


def usable_count(sleeper):
    """How many players actually have projected points. Sleeper answers even for weeks it has no projections for, with a big
    object of empty rows, so the row count alone says nothing."""
    return sum(1 for v in sleeper.values() if isinstance(v, dict) and v.get("pts_ppr") is not None)


def league_projection(stats, scoring):
    """Sleeper's projected stat line scored with the league's settings; None if Sleeper has no projection for the player."""
    if not stats or all(stats.get(k) is None for k in ("pts_ppr", "pts_half_ppr", "pts_std")):
        return None
    return sum(float(stats[k]) * w for k, w in scoring.items() if w and stats.get(k) is not None)


def return_points_per_game(weekly_stats_cache, scoring, season, week, history_seasons=HISTORY_SEASONS):
    """
    {player_id: average return-yardage points per game played}, over the previous season(s) and this season's completed weeks.
    weekly_stats_cache is the pipeline's {(year, max_weeks): {week: {player_id: {gp, kr_yd, pr_yd, ...}}}}.
    """
    w_kr, w_pr = float(scoring.get("kr_yd", 0) or 0), float(scoring.get("pr_yd", 0) or 0)
    best = {}                                             # per year, the cached download that covers the most weeks
    for (year, max_weeks), weeks in weekly_stats_cache.items():
        if season - history_seasons + 1 <= year <= season and (year not in best or max_weeks > best[year][0]):
            best[year] = (max_weeks, weeks)
    pts, games = {}, {}
    for year, (_, weeks) in best.items():
        for wk, players in weeks.items():
            if year == season and wk >= week:             # only weeks already played
                continue
            for pid, rec in players.items():
                if not (rec.get("gp", 0) > 0 or rec.get("kr_yd", 0) or rec.get("pr_yd", 0)):
                    continue
                games[pid] = games.get(pid, 0) + 1
                pts[pid] = pts.get(pid, 0.0) + w_kr * rec.get("kr_yd", 0) + w_pr * rec.get("pr_yd", 0)
    return {pid: pts[pid] / games[pid] for pid in games}


def use_sleeper(sleeper_pts, return_pts_pg, available=True, below=SLEEPER_BELOW, ret_threshold=RETURN_PTS_PER_GAME):
    """The owner's rule. Unavailable players (bye / Out / IR) stay at 0 whatever either projection says."""
    return bool(available and sleeper_pts is not None and sleeper_pts < below and return_pts_pg <= ret_threshold)


def apply_rule(players, sleeper, scoring, return_pts_pg, below=SLEEPER_BELOW, ret_threshold=RETURN_PTS_PER_GAME):
    """
    Update an export's players dict in place. Each player gets: model_projection (what the model said, incl. bye / injury
    zeroing), sleeper_projection (league-scored, or None), return_pts_pg, projection_source ('sleeper' | 'model'), and
    projected_points = the value the page uses. Returns counts for the log.
    """
    counts = {"players": 0, "sleeper": 0, "model": 0, "no_sleeper_projection": 0, "returner_kept_model": 0}
    for pid, p in players.items():
        counts["players"] += 1
        model = float(p["projected_points"])
        sp = league_projection(sleeper.get(pid), scoring)
        ret = float(return_pts_pg.get(pid, 0.0))
        p["model_projection"] = round(model, 3)
        p["sleeper_projection"] = None if sp is None else round(sp, 3)
        p["return_pts_pg"] = round(ret, 2)
        if use_sleeper(sp, ret, available=(p.get("availability", "") == ""), below=below, ret_threshold=ret_threshold):
            p["projected_points"] = round(sp, 3)
            p["projection_source"] = "sleeper"
            counts["sleeper"] += 1
        else:
            p["projected_points"] = round(model, 3)
            p["projection_source"] = "model"
            counts["model"] += 1
            if sp is None:
                counts["no_sleeper_projection"] += 1
            elif sp < below and ret > ret_threshold and p.get("availability", "") == "":
                counts["returner_kept_model"] += 1
    return counts
