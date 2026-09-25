"""
Return-points model: how many fantasy points a player will get from RETURNS in a game.

Return points = league points from return yardage (kick + punt return yards x the league's 0.1 pt per yard) plus return / special-teams
touchdowns (Sleeper's `st_td`, 6 pts here). The model is trained on real player-games and predicts a player's return points from what he
has done SO FAR THIS SEASON (return yards per game to date) and the week of the season; it is meant to be ADDED to a projection that
leaves return yardage out (Sleeper's).

All functions work on the pipeline's stats cache: {(year, max_weeks): {week: {player_id: {"gp", "kr_yd", "pr_yd", "st_td", ...}}}}.
"""
import numpy as np
import pandas as pd
import xgboost as xgb

# the owner's spec: return yards per game to date (kick and punt) and the week of the season
SPEC_FEATURES = ["kr_yd_pg", "pr_yd_pg", "week"]
MONOTONE = {"kr_yd_pg": 1, "pr_yd_pg": 1, "games": 0, "week": 0, "prior_ret_ppg": 1}


def weights_for(scoring):
    """(kick-return yd, punt-return yd, special-teams TD) point values from a league scoring dict."""
    return (float(scoring.get("kr_yd", 0) or 0), float(scoring.get("pr_yd", 0) or 0), float(scoring.get("st_td", 0) or 0))


def return_points(rec, w):
    return w[0] * (rec.get("kr_yd", 0) or 0) + w[1] * (rec.get("pr_yd", 0) or 0) + w[2] * (rec.get("st_td", 0) or 0)


def _is_player(pid):
    return str(pid).isdigit()              # team defenses / TEAM_x lines are not players


def _season_weeks(cache, season):
    """This season's weekly stats (the cached download that covers the most weeks)."""
    best = None
    for (year, max_weeks), weeks in cache.items():
        if year == season and (best is None or max_weeks > best[0]):
            best = (max_weeks, weeks)
    return best[1] if best else {}


def _played(rec):
    return (rec.get("gp", 0) or 0) > 0 or (rec.get("kr_yd", 0) or 0) != 0 or (rec.get("pr_yd", 0) or 0) != 0 or (rec.get("st_td", 0) or 0) != 0


def season_totals(cache, season, weights, before_week=None):
    """{pid: (kr_yd, pr_yd, games, return_points)} over the season's weeks (before `before_week` if given)."""
    tot = {}
    for wk, players in sorted(_season_weeks(cache, season).items()):
        if before_week is not None and wk >= before_week:
            continue
        for pid, rec in players.items():
            if not _is_player(pid) or not _played(rec):
                continue
            k, p, g, r = tot.get(pid, (0.0, 0.0, 0, 0.0))
            tot[pid] = (k + (rec.get("kr_yd", 0) or 0), p + (rec.get("pr_yd", 0) or 0), g + 1, r + return_points(rec, weights))
    return tot


def build_training_frame(cache, weights_by_year, seasons, before=None):
    """
    One row per player-game (the player played): features from the SAME season's earlier weeks only, target = that game's return points.
    Columns: season, week, pid, kr_yd_pg, pr_yd_pg, games, prior_ret_ppg (last season's return pts per game, NaN if none), ret_pts.
    `before=(season, week)` leaves out that season's weeks from `week` on (the week being projected, which may be partly played).
    """
    rows = []
    prior = {}
    for season in sorted(seasons):
        w = weights_by_year[season]
        run = {}                                              # pid -> [kr, pr, games]
        for wk, players in sorted(_season_weeks(cache, season).items()):
            if before is not None and season == before[0] and wk >= before[1]:
                continue
            for pid, rec in players.items():
                if not _is_player(pid) or not _played(rec):
                    continue
                k, p, g = run.get(pid, (0.0, 0.0, 0))
                rows.append((season, wk, pid, k / g if g else np.nan, p / g if g else np.nan, g, prior.get(pid, np.nan), return_points(rec, w)))
            for pid, rec in players.items():                  # update AFTER the week's rows were recorded (no leakage)
                if not _is_player(pid) or not _played(rec):
                    continue
                k, p, g = run.get(pid, (0.0, 0.0, 0))
                run[pid] = (k + (rec.get("kr_yd", 0) or 0), p + (rec.get("pr_yd", 0) or 0), g + 1)
        tot = season_totals(cache, season, w)
        prior = {pid: r / g for pid, (_, _, g, r) in tot.items() if g}
    return pd.DataFrame(rows, columns=["season", "week", "pid", "kr_yd_pg", "pr_yd_pg", "games", "prior_ret_ppg", "ret_pts"])


def features_for(cache, weights_by_year, season, week, pids):
    """Features for predicting `week` of `season` for these players, from the games already played this season (NaN if none)."""
    tot = season_totals(cache, season, weights_by_year[season], before_week=week)
    prior_w = weights_by_year.get(season - 1)
    prior = {}
    if prior_w is not None:
        prior = {pid: r / g for pid, (_, _, g, r) in season_totals(cache, season - 1, prior_w).items() if g}
    rows = []
    for pid in pids:
        k, p, g, _ = tot.get(pid, (0.0, 0.0, 0, 0.0))
        rows.append((pid, k / g if g else np.nan, p / g if g else np.nan, g, prior.get(pid, np.nan), week))
    return pd.DataFrame(rows, columns=["pid", "kr_yd_pg", "pr_yd_pg", "games", "prior_ret_ppg", "week"])


def fit(frame, features=SPEC_FEATURES, n_estimators=250, seed=0):
    """Gradient-boosted regression, increasing in the return-yard features (more return yards so far can't lower the forecast)."""
    model = xgb.XGBRegressor(n_estimators=n_estimators, max_depth=3, learning_rate=0.05, subsample=0.8, min_child_weight=20,
                             reg_lambda=5.0, monotone_constraints=tuple(MONOTONE.get(f, 0) for f in features), tree_method="hist",
                             random_state=seed, n_jobs=2)
    model.fit(frame[features], frame["ret_pts"])
    model.feature_names_used = list(features)
    return model


def predict(model, feats):
    """{pid: predicted return points (never negative)} for a features_for() frame."""
    out = np.clip(model.predict(feats[model.feature_names_used]), 0.0, None)
    return dict(zip(feats["pid"], out.astype(float)))
