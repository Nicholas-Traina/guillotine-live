"""
Sleeper's own projections, scored with THIS league's rules, and the three projections the page offers:

  model            the model's projection
  sleeper          Sleeper's projected stat line scored with the league's settings
  sleeper_returns  Sleeper + return points: Sleeper's projection with ITS OWN return yardage taken out, plus the return-points model's
                   prediction (return yards x 0.1 pt and return touchdowns, from return_model.py). Sleeper barely projects return
                   yardage, and the return yards it does project (punt returns for a few dozen players, and a phantom punt-return line
                   on team defenses that real defense scoring never credits) would otherwise be double counted.

Players on a bye or listed Out / IR are 0 in all three. Team defenses get no return points (they are not players).
"""
import time

import requests

URL = "https://api.sleeper.app/v1/projections/nfl/regular/{season}/{week}"


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


def projected_return_points(stats, scoring):
    """The return-yardage points already inside Sleeper's projected stat line (kick + punt return yards x the league's values)."""
    if not stats:
        return 0.0
    return sum(float(stats.get(k) or 0) * float(scoring.get(k, 0) or 0) for k in ("kr_yd", "pr_yd"))


RETURNER_MIN = 1.0      # predicted return points per game at or above this make a player a returner (the rest of the field is ~0.1 or less)


def add_return_points(players, sleeper, scoring, return_pts, returner_min=RETURNER_MIN):
    """
    Update an export's players dict in place. Each player gets:
      model_projection        the model's number (0 if on a bye / Out / IR)
      sleeper_projection      Sleeper's number scored with the league (None if Sleeper has none; 0 if unavailable)
      sleeper_return_comp     the return points inside Sleeper's number
      return_pts_projection   the return-points model's prediction (0 for team defenses)
      sleeper_plus_returns    sleeper_projection - sleeper_return_comp + return_pts_projection (None if Sleeper has no projection,
                              except for a returner (predicted return points >= returner_min): Sleeper's missing projection counts as 0,
                              so he is 0 + his predicted return points)
    projected_points is left as the model's number; guillotine_live.pick_projection chooses which one a page uses.
    Returns counts for the log.
    """
    counts = {"players": 0, "with_sleeper": 0, "no_sleeper_projection": 0, "with_return_points": 0, "returner_only": 0}
    for pid, p in players.items():
        counts["players"] += 1
        available = p.get("availability", "") == ""
        model = float(p["projected_points"])
        stats = sleeper.get(pid)
        sp = league_projection(stats, scoring)
        comp = projected_return_points(stats, scoring)
        ret = float(return_pts.get(pid, 0.0))
        p["model_projection"] = round(model, 3)
        p["sleeper_return_comp"] = round(comp, 3)
        p["return_pts_projection"] = round(ret, 3)
        if sp is None:
            p["sleeper_projection"] = None
            p["sleeper_plus_returns"] = None
            counts["no_sleeper_projection"] += 1
            if available and ret >= returner_min:
                p["sleeper_plus_returns"] = round(ret, 3)
                counts["returner_only"] += 1
        else:
            counts["with_sleeper"] += 1
            p["sleeper_projection"] = round(sp if available else 0.0, 3)
            p["sleeper_plus_returns"] = round(sp - comp + ret if available else 0.0, 3)
        if ret > 0:
            counts["with_return_points"] += 1
    return counts
