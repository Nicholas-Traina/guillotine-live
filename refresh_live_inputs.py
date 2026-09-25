"""
Hourly refresh of the live view's projections.

Runs the projection notebook's own code headlessly (so the numbers are identical to running it by hand):
current rosters, injury / bye flags, and a projection for every rostered player -> live_inputs_<season>_wk<week>.json.
The live page already re-reads each team's *starters* from Sleeper on every refresh; this job keeps the
*projections* fresh (players added since the last run, injury designations, ...).

    python refresh_live_inputs.py

Safe by design: the old file is replaced atomically and only after a fully successful run, a failed run leaves the
previous file in place, overlapping runs are skipped, and everything is logged to refresh_log.txt.
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
import warnings

import requests

import sleeper_projections as sp

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
NOTEBOOK = os.path.join(HERE, "Guillotine Week Projection 5.ipynb")
LOG = os.path.join(HERE, "refresh_log.txt")
LOCK = os.path.join(HERE, "refresh.lock")
LOCK_STALE_SECONDS = 30 * 60

# (name, text that identifies the notebook cell) in execution order
CELLS = [
    ("settings", "# ===== SETTINGS ====="),
    ("pipeline", "# points_prediction_xgb.py"),
    ("model", "Refitting final model on all seasons"),
    ("helpers", "def get_roster_owner_map"),
    ("projection", "# ===== Week projection: player points"),
    ("run", "# ===== Run the baseline projection"),
    ("export", "# ===== Export this week's projections for the live view"),
]


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def remove_tree(path):
    """rmtree that also clears read-only flags (Windows refuses to delete read-only files / folders)."""
    def clear_and_retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_and_retry)
    else:
        shutil.rmtree(path, onerror=clear_and_retry)


def load_cells():
    with open(NOTEBOOK, encoding="utf-8") as fh:
        nb = json.load(fh)
    sources = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    picked = []
    for name, marker in CELLS:
        hits = [s for s in sources if marker in s]
        if len(hits) != 1:
            raise RuntimeError(f"expected exactly one notebook cell containing {marker!r} ({name}), found {len(hits)}")
        picked.append((name, hits[0]))
    return picked


def summarize_changes(old, new):
    """What changed between two exports: the 'did any lineups change?' check."""
    op = {p: v for p, v in old["players"].items() if not v.get("free_agent")}
    np_ = {p: v for p, v in new["players"].items() if not v.get("free_agent")}
    added, dropped = set(np_) - set(op), set(op) - set(np_)
    common = set(op) & set(np_)
    lineup = [p for p in common if op[p]["starter_at_export"] != np_[p]["starter_at_export"]]
    moved = [p for p in common if op[p]["roster_id"] != np_[p]["roster_id"]]
    avail = [p for p in common if op[p]["availability"] != np_[p]["availability"] or op[p]["injury_status"] != np_[p]["injury_status"]]
    name = lambda p, d: d[p]["name"]
    parts = [f"{len(np_)} players"]
    if added: parts.append(f"+{len(added)} added ({', '.join(name(p, np_) for p in sorted(added)[:6])})")
    if dropped: parts.append(f"-{len(dropped)} dropped ({', '.join(name(p, op) for p in sorted(dropped)[:6])})")
    if moved: parts.append(f"{len(moved)} changed team ({', '.join(name(p, np_) for p in moved[:6])})")
    if lineup: parts.append(f"{len(lineup)} start/bench changes ({', '.join(name(p, np_) for p in lineup[:6])})")
    if avail: parts.append(f"{len(avail)} injury/availability changes ({', '.join(name(p, np_) for p in avail[:6])})")
    if len(parts) == 1: parts.append("no roster, lineup or injury changes")
    return "; ".join(parts)


def add_free_agent_pool(ns, payload, season, week):
    """
    Projections are refreshed once a day, but players get picked up off waivers all day. Project every relevant player who
    is NOT on a roster too (anyone who has played this season, every K and defense, and anyone with an ADP), so a pickup put
    into a lineup later gets a real projection instead of a generic fallback. Returns how many were added.
    """
    meta, frame, ctx = ns["PLAYERS_META"], ns["frame"], ns["ctx"]
    rostered = set(frame["player_id"])
    stats = ns["fetch_weekly_stats"](season, week)                     # already downloaded by the run cell (cached)
    played = {pid for wk in range(1, week) for pid, rec in stats.get(wk, {}).items() if rec["gp"] > 0 or rec["pts"] != 0}
    norm, name_of = ns["normalize_name_for_match"], ns["player_display_name"]
    adp_names = set(ctx["adp_map"])
    pool = []
    for pid, info in meta.items():
        pos = info.get("position")
        if pos not in ns["FANTASY_POSITIONS"] or pid in rostered or not info.get("team") or info.get("active") is False:
            continue
        if pos in ("DEF", "K") or pid in played or norm(name_of(pid)) in adp_names:
            pool.append(pid)
    if not pool:
        return 0
    fa = ns["build_free_agent_rows"](pool, ctx, 0, "")
    fa = ns["add_projection"](fa, week, ctx["bye"], ctx["apply_availability"])
    for r in fa.itertuples():
        payload["players"][r.player_id] = {
            "name": r.player_name, "position": r.position, "team": r.nfl_team, "roster_id": 0, "owner": "",
            "projected_points": round(float(r.projected_points), 3), "raw_projection": round(float(r.raw_projection), 3),
            "availability": r.availability_flag or "", "injury_status": r.injury_status if isinstance(r.injury_status, str) else "",
            "starter_at_export": 0, "free_agent": True}
    return len(fa)


def validate_run(ns, season, week, payload):
    """
    The data pipeline swallows request errors (an empty week just looks like 'no data'), so a network hiccup could
    silently give a degraded model. Refuse to publish a run whose inputs look incomplete.
    """
    problems = []
    for (year, _), weeks in ns["_WEEKLY_STATS_CACHE"].items():
        empty = [w for w, m in weeks.items() if len(m) < 300 and (year < season or w < week)]     # a real week has ~1500+ players
        if empty:
            problems.append(f"Sleeper returned no stats for {year} weeks {empty}")
    thin = [(int(d['season'].iloc[0]), len(d)) for d in ns["all_rows"] if len(d) < 2000]           # ~3,200-3,600 rows per season
    if thin:
        problems.append(f"training seasons with too few rows (season, rows): {thin}")
    if len(ns["all_rows"]) < 5:
        problems.append(f"only {len(ns['all_rows'])} training seasons loaded")
    if len(payload.get("players", {})) < 100:
        problems.append(f"export has only {len(payload.get('players', {}))} players")
    missing = {"QB", "RB", "WR", "TE", "K", "DEF"} - set(payload.get("sigma_by_position", {}))
    if missing:
        problems.append(f"no uncertainty estimate for positions {sorted(missing)}")
    if not 5.0 <= payload.get("global_rmse", 0) <= 10.0:
        problems.append(f"out-of-sample RMSE {payload.get('global_rmse')} is outside the plausible 5-10 range")
    if problems:
        raise RuntimeError("refusing to publish this run: " + "; ".join(problems))


def refresh():
    state = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=20).json()
    season, week = int(state["season"]), int(state["week"])
    if state.get("season_type") != "regular":
        log(f"skipped: season_type is {state.get('season_type')!r}, not regular season")
        return 0
    cells = load_cells()
    final_path = os.path.join(HERE, f"live_inputs_{season}_wk{week}.json")
    old = None
    if os.path.exists(final_path):
        try:
            old = json.load(open(final_path, encoding="utf-8"))
        except (OSError, ValueError):
            old = None

    t0 = time.time()
    work = tempfile.mkdtemp(prefix="guillotine_refresh_")
    cache_src = os.path.join(HERE, "adp_cache")
    if os.path.isdir(cache_src):                         # plain copies: OneDrive marks folders read-only and copytree would copy that flag
        os.makedirs(os.path.join(work, "adp_cache"))
        for f in os.listdir(cache_src):
            shutil.copyfile(os.path.join(cache_src, f), os.path.join(work, "adp_cache", f))
    os.environ["MPLBACKEND"] = "Agg"
    cwd = os.getcwd()
    try:
        os.chdir(work)                                   # the notebook writes CSVs / a model file: keep them out of the project
        ns = {"__name__": "__main__", "display": lambda *a, **k: None}
        for name, src in cells:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(src, f"notebook:{name}", "exec"), ns)
            if name == "settings":                       # always the CURRENT season / week
                ns["TARGET_YEAR"], ns["TARGET_WEEK"] = season, week
        made = os.path.join(work, f"live_inputs_{season}_wk{week}.json")
        payload = json.load(open(made, encoding="utf-8"))
        payload["generated_epoch"] = time.time()         # timezone-proof timestamp for the page's freshness check
        validate_run(ns, season, week, payload)
        try:
            n_pool = add_free_agent_pool(ns, payload, season, week)
        except Exception:
            n_pool = 0
            log("WARNING: free-agent pool skipped (rostered players are still projected):\n" + traceback.format_exc(limit=3))
        rule_note = ""
        try:                                              # owner's rule: model for players averaging > 2 return pts/game this season, else Sleeper
            scoring = ns["get_league_scoring"](season)
            sleeper = sp.fetch_sleeper_projections(season, week)
            if sp.usable_count(sleeper) < 300 or not scoring:
                raise RuntimeError(f"Sleeper projections unavailable ({sp.usable_count(sleeper)} players with points) or league scoring missing")
            ret = sp.return_points_per_game_so_far(ns["_WEEKLY_STATS_CACHE"], scoring, season, week)
            counts = sp.apply_rule(payload["players"], sleeper, scoring, ret)
            n_start = sum(1 for v in payload["players"].values() if v.get("projection_source") == "sleeper" and v.get("starter_at_export"))
            payload["projection_rule"] = {"rule": f"model if the player averages more than {sp.RETURN_PPG_THRESHOLD:g} return-yardage points per game "
                                                  "so far this season, else Sleeper; a returner whose model projection is below Sleeper's gets Sleeper's plus his return "
                                                  "points per game, so nothing is below Sleeper's (model when Sleeper has no projection; bye / Out / IR stay 0)",
                                          "return_ppg_threshold": sp.RETURN_PPG_THRESHOLD, **counts}
            rule_note = (f" | Sleeper projection used for {counts['sleeper']} players ({n_start} starters at export), "
                         f"{counts['returner_kept_model']} returners kept on the model, "
                         f"{counts['sleeper_plus_returns']} returners raised to Sleeper + return ppg")
        except Exception:
            log("WARNING: Sleeper-projection rule skipped, model projections used for everyone:\n" + traceback.format_exc(limit=3))
        tmp_final = final_path + ".tmp"
        with open(tmp_final, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp_final, final_path)                # atomic: the app never sees a half-written file
        cache_out = os.path.join(work, "adp_cache")
        if os.path.isdir(cache_out):
            os.makedirs(cache_src, exist_ok=True)
            for f in os.listdir(cache_out):
                shutil.copy2(os.path.join(cache_out, f), os.path.join(cache_src, f))
    finally:
        os.chdir(cwd)
        remove_tree(work)
        atexit.register(remove_tree, work)               # Windows can keep a folder busy while this process lives: retry at exit
    log(f"OK {season} wk{week} in {time.time() - t0:.0f}s (+{n_pool} free agents projected): "
        + (summarize_changes(old, payload) if old else f"{len(payload['players'])} players (first export)") + rule_note)
    return 0


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < LOCK_STALE_SECONDS:
        log("skipped: another refresh is still running")
        return 0
    open(LOCK, "w").write(str(os.getpid()))
    try:
        return refresh()
    except Exception:
        log("FAILED (the previous live_inputs file is untouched):\n" + traceback.format_exc())
        return 1
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
