"""
Sleeper's own projections, scored with THIS league's rules, and the rule that decides which projection the page uses.

Rule (chosen by the league owner after a backtest on 2024-2026): use the MODEL for any player who averages MORE than 2 points per
game from return yardage so far this season (kick + punt return yards x the league's 0.1 pt per yard, over the games he has played
in completed weeks), and SLEEPER's projection for everyone else. Sleeper barely projects return yardage (punt-return yards for a
few dozen players, no kick-return yards) while this league scores it, so for real returners the model is the better guide; for
everyone else Sleeper was more accurate than the model. Players Sleeper has no projection for stay on the model, and players on a
bye or listed Out / IR stay at 0.
"""
import time

import requests

URL = "https://api.sleeper.app/v1/projections/nfl/regular/{season}/{week}"
RETURN_PPG_THRESHOLD = 2.0     # model if he averages MORE than this many return-yardage points per game so far this season


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


def return_points_per_game_so_far(weekly_stats_cache, scoring, season, week):
    """
    {player_id: return-yardage points per game played}, over this season's completed weeks (weeks before `week`).
    A game counts if he played (gp > 0) or had any return yards. weekly_stats_cache is the pipeline's
    {(year, max_weeks): {week: {player_id: {gp, kr_yd, pr_yd, ...}}}}. In week 1 nothing has been played, so the result is empty.
    """
    w_kr, w_pr = float(scoring.get("kr_yd", 0) or 0), float(scoring.get("pr_yd", 0) or 0)
    best = None
    for (year, max_weeks), weeks in weekly_stats_cache.items():
        if year == season and (best is None or max_weeks > best[0]):
            best = (max_weeks, weeks)
    pts, games = {}, {}
    if best is None:
        return {}
    for wk, players in best[1].items():
        if wk >= week:
            continue
        for pid, rec in players.items():
            if not (rec.get("gp", 0) > 0 or rec.get("kr_yd", 0) or rec.get("pr_yd", 0)):
                continue
            games[pid] = games.get(pid, 0) + 1
            pts[pid] = pts.get(pid, 0.0) + w_kr * rec.get("kr_yd", 0) + w_pr * rec.get("pr_yd", 0)
    return {pid: pts[pid] / games[pid] for pid in games}


def use_sleeper(sleeper_pts, return_ppg, available=True, threshold=RETURN_PPG_THRESHOLD):
    """The owner's rule: Sleeper's projection unless the player averages more than `threshold` return points per game this
    season (or has no Sleeper projection). Unavailable players (bye / Out / IR) are never switched."""
    return bool(available and sleeper_pts is not None and not return_ppg > threshold)


def apply_rule(players, sleeper, scoring, return_ppg, threshold=RETURN_PPG_THRESHOLD):
    """
    Update an export's players dict in place. Each player gets: model_projection (what the model said, incl. bye / injury
    zeroing), sleeper_projection (league-scored, or None), return_ppg_so_far, projection_source ('sleeper' | 'model'), and
    projected_points = the value the page uses. Returns counts for the log.
    """
    counts = {"players": 0, "sleeper": 0, "model": 0, "no_sleeper_projection": 0, "returner_kept_model": 0}
    for pid, p in players.items():
        counts["players"] += 1
        model = float(p["projected_points"])
        sp = league_projection(sleeper.get(pid), scoring)
        ppg = float(return_ppg.get(pid, 0.0))
        available = p.get("availability", "") == ""
        p["model_projection"] = round(model, 3)
        p["sleeper_projection"] = None if sp is None else round(sp, 3)
        p["return_ppg_so_far"] = round(ppg, 2)
        if use_sleeper(sp, ppg, available, threshold):
            p["projected_points"] = round(sp, 3)
            p["projection_source"] = "sleeper"
            counts["sleeper"] += 1
        else:
            p["projected_points"] = round(model, 3)
            p["projection_source"] = "model"
            counts["model"] += 1
            if sp is None:
                counts["no_sleeper_projection"] += 1
            elif ppg > threshold and available:
                counts["returner_kept_model"] += 1
    return counts
