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


_SLOTS_CACHE = {}


def fetch_roster_positions(league_id):
    """The league's starting lineup slots in order (bench / IR / taxi removed), e.g. ['RB', 'RB', 'WR', ..., 'DEF']; None if unavailable.
    Matches the order of a matchup's `starters` list."""
    if league_id not in _SLOTS_CACHE:
        try:
            rp = _get(f"{SLEEPER}/league/{league_id}")["roster_positions"]
            _SLOTS_CACHE[league_id] = [s for s in rp if s not in ("BN", "IR", "TAXI")]
        except Exception:
            return None
    return _SLOTS_CACHE[league_id]


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

# Game-clock sources, tried in order. All three are ESPN (different hostnames / feeds), because some networks
# (cloud data centers in particular) are blocked on one but not the others.
_ESPN_PARAMS = lambda season, week: {"week": week, "seasontype": 2, "dates": season}
ESPN_SOURCES = [
    ("ESPN site.api", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard", _ESPN_PARAMS, lambda j: j["events"]),
    ("ESPN site.web.api", "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard", _ESPN_PARAMS, lambda j: j["events"]),
    ("ESPN cdn", "https://cdn.espn.com/core/nfl/scoreboard",
     lambda season, week: {"xhr": 1, "limit": 50, "year": season, "week": week, "seasontype": 2}, lambda j: j["content"]["sbData"]["events"]),
]
NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"     # schedule with kickoff times (ET)
NFLVERSE_TO_SLEEPER = {"LA": "LAR"}
GAME_REAL_SECONDS = 190 * 60          # typical real-time length of an NFL game (60 game minutes + halftime + stoppages)
ESPN_BACKOFF_SECONDS = 60             # after every ESPN source failed, don't retry for this long (keeps refreshes fast)
_espn_state = {"until": 0.0, "reasons": []}
_kickoff_cache = {}


def _err_text(e):
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    return f"{type(e).__name__}: {str(e)[:60]}"


def _parse_iso_epoch(text):
    """'2026-09-27T17:00Z' (ESPN, no seconds) or '2026-09-27T17:00:00Z' -> UTC epoch seconds, else None."""
    import calendar
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return calendar.timegm(time.strptime(str(text), fmt))
        except ValueError:
            continue
    return None


def parse_espn_events(events):
    """ESPN scoreboard events -> {sleeper_team: game}. Malformed events are skipped instead of failing the whole feed."""
    games = {}
    for ev in events:
        try:
            st = ev["status"]
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            state = st["type"]["state"]
            f = fraction_left(state, st.get("period"), st.get("clock"))
            detail = st["type"].get("shortDetail") or st["type"].get("detail", "")
            if state == "pre":                                    # ESPN words kickoff in Eastern time; show Central
                ko = _parse_iso_epoch(ev.get("date"))
                if ko:
                    detail = format_central(ko, seconds=False, day=True)
            ab = {side: ESPN_TO_SLEEPER.get(teams[side]["team"]["abbreviation"], teams[side]["team"]["abbreviation"]) for side in ("home", "away")}
            for side, other in (("home", "away"), ("away", "home")):
                games[ab[side]] = {
                    "state": state, "fraction_left": f, "detail": detail,
                    "opponent": ab[other], "home": side == "home", "score": teams[side].get("score"), "opp_score": teams[other].get("score"),
                    "kickoff": ev.get("date"), "estimated": False,
                }
        except (KeyError, IndexError, TypeError):
            continue
    return games


def fetch_espn(season, week):
    """(games or None, source name or None, [(source, 'ok' | reason it failed), ...])."""
    attempts = []
    if time.time() < _espn_state["until"]:
        return None, None, [("ESPN", f"skipped for {int(_espn_state['until'] - time.time())}s after failing: " + "; ".join(_espn_state["reasons"]))]
    for name, url, params, pick in ESPN_SOURCES:
        try:
            games = parse_espn_events(pick(_get(url, params=params(season, week), timeout=8)))
            if len(games) < 8:
                raise ValueError(f"only {len(games)} teams in the feed")
            attempts.append((name, "ok"))
            return games, name, attempts
        except Exception as e:
            attempts.append((name, _err_text(e)))
    _espn_state["until"] = time.time() + ESPN_BACKOFF_SECONDS
    _espn_state["reasons"] = [f"{n}: {r}" for n, r in attempts]
    return None, None, attempts


def _et_to_epoch(date_str, time_str):
    """US Eastern wall-clock time ('2026-09-27', '13:00') -> UTC epoch seconds (handles daylight saving without tz data)."""
    import calendar
    import datetime
    y, m, d = map(int, date_str.split("-"))
    hh, mm = map(int, time_str.split(":")[:2])
    local = datetime.datetime(y, m, d, hh, mm)

    def nth_sunday(month, n):
        first = datetime.date(y, month, 1)
        return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    dst_start = datetime.datetime.combine(nth_sunday(3, 2), datetime.time(2, 0))       # 2nd Sunday of March, 2:00 local
    dst_end = datetime.datetime.combine(nth_sunday(11, 1), datetime.time(2, 0))        # 1st Sunday of November, 2:00 local
    offset = 4 if dst_start <= local < dst_end else 5
    return calendar.timegm((local + datetime.timedelta(hours=offset)).timetuple())


def fetch_kickoffs(season, week):
    """{sleeper_team: kickoff UTC epoch} from the nflverse schedule (cached for 6 hours). {} if unavailable."""
    key = (season, week)
    hit = _kickoff_cache.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    import csv
    import io
    out = {}
    try:
        r = requests.get(NFLVERSE_GAMES, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        for row in csv.DictReader(io.StringIO(r.text)):
            if row["season"] == str(season) and row["week"] == str(week) and row.get("gameday") and row.get("gametime"):
                t = _et_to_epoch(row["gameday"], row["gametime"])
                for team in (row["home_team"], row["away_team"]):
                    out[NFLVERSE_TO_SLEEPER.get(team, team)] = t
    except Exception:
        out = {}
    _kickoff_cache[key] = (time.time(), out)
    return out


def fetch_game_states_estimated(season, week):
    """
    Fallback when no ESPN feed is reachable. Sleeper's schedule says pre_game / in_game / complete but has no clock, so a
    game in progress is placed using the nflverse kickoff time: fraction played ~ time since kickoff / 190 minutes
    (roughly +/-10% of a game). Without kickoff times a game in progress is assumed to be half over.
    Returns (games, used_kickoff_times).
    """
    kickoffs = fetch_kickoffs(season, week)
    now = time.time()
    games = {}
    for g in _get(f"https://api.sleeper.com/schedule/nfl/regular/{season}"):
        if g.get("week") != week:
            continue
        state = SLEEPER_STATUS.get(g.get("status"), "pre")
        ko = kickoffs.get(g["home"]) or kickoffs.get(g["away"])
        if state == "pre":
            f, detail = 1.0, (format_central(ko, seconds=False, day=True) if ko else "not started")
        elif state == "post":
            f, detail = 0.0, "final"
        elif ko:
            played = min(max((now - ko) / GAME_REAL_SECONDS, 0.03), 0.97)
            f, detail = 1.0 - played, f"in progress (~{played * 100:.0f}% played, estimated)"
        else:
            f, detail = 0.5, "in progress (clock unavailable)"
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ko)) if ko else g.get("date")
        for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
            games[team] = {"state": state, "fraction_left": f, "detail": detail, "opponent": opp, "home": home,
                           "score": None, "opp_score": None, "kickoff": iso, "estimated": True}
    return games, bool(kickoffs)


def fetch_game_states_detailed(season, week):
    """(games, {'source': str, 'estimated': bool, 'attempts': [(source, result), ...]})."""
    games, source, attempts = fetch_espn(season, week)
    if games:
        return games, {"source": source, "estimated": False, "attempts": attempts}
    games, used_kickoffs = fetch_game_states_estimated(season, week)
    return games, {"source": "kickoff-time estimate" if used_kickoffs else "Sleeper status only (in-progress games assumed half over)",
                   "estimated": True, "attempts": attempts}


def fetch_game_states(season, week):
    """{sleeper_team_abbr: {state, fraction_left, detail, opponent, home, score, opp_score, kickoff, estimated}} for the week."""
    return fetch_game_states_detailed(season, week)[0]


def to_central(epoch):
    """UTC epoch seconds -> (naive datetime in US Central time, 'CDT' or 'CST'). Daylight saving without tz data:
    it starts on the 2nd Sunday of March at 2:00 CST (08:00 UTC) and ends on the 1st Sunday of November at 2:00 CDT (07:00 UTC)."""
    import datetime
    utc = datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc).replace(tzinfo=None)

    def nth_sunday(month, n):
        first = datetime.date(utc.year, month, 1)
        return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    start = datetime.datetime.combine(nth_sunday(3, 2), datetime.time(8, 0))
    end = datetime.datetime.combine(nth_sunday(11, 1), datetime.time(7, 0))
    if start <= utc < end:
        return utc - datetime.timedelta(hours=5), "CDT"
    return utc - datetime.timedelta(hours=6), "CST"


def format_central(epoch, seconds=True, day=False):
    """'9:34:31 PM CDT' (or 'Thu 9/24 3:38 PM CDT' with day=True, seconds=False)."""
    dt, tz = to_central(epoch)
    clock = dt.strftime("%I:%M:%S %p" if seconds else "%I:%M %p").lstrip("0")
    return (f"{dt.strftime('%a')} {dt.month}/{dt.day} " if day else "") + f"{clock} {tz}"


def inputs_generated_text(inputs):
    """When the projections were generated, in Central time (falls back to the raw stamp for files without an epoch)."""
    if inputs.get("generated_epoch"):
        return format_central(inputs["generated_epoch"], seconds=False, day=True)
    return str(inputs.get("generated_at", "?"))


# The three projections the page can use (the export carries all three for every player)
PROJECTION_LABELS = {"sleeper_returns": "Sleeper + return points", "sleeper": "Sleeper", "model": "Model"}
DEFAULT_PROJECTION = "sleeper_returns"


def pick_projection(info, choice=None):
    """(value, source) of one player's projection under `choice`. Falls back Sleeper + return points -> Sleeper -> model, so a
    player Sleeper doesn't project (or an older export without the new fields) still gets a number."""
    choice = choice if choice in PROJECTION_LABELS else DEFAULT_PROJECTION
    if choice == "sleeper_returns" and info.get("sleeper_plus_returns") is not None:
        return float(info["sleeper_plus_returns"]), "sleeper+returns"
    if choice in ("sleeper_returns", "sleeper") and info.get("sleeper_projection") is not None:
        return float(info["sleeper_projection"]), "sleeper"
    return float(info.get("model_projection", info.get("projected_points", 0.0)) or 0.0), "model"


DEFAULT_CONFIG = {"immune_teams": [], "eliminations_override": 0, "highlight_default": "", "projection_source": DEFAULT_PROJECTION}


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
    if cfg["projection_source"] not in PROJECTION_LABELS:
        cfg["projection_source"] = DEFAULT_PROJECTION
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
def _lacks_sleeper_projection(info):
    """An available player (not Out / IR / bye / not in the export) that Sleeper has no projection for and who isn't a returner
    (a returner Sleeper doesn't project already has a Sleeper + return points value: 0 + his predicted return points)."""
    return ("sleeper_projection" in info and info["sleeper_projection"] is None and info.get("sleeper_plus_returns") is None
            and not info.get("availability"))


def _bench_by_position(matchup, players):
    """{position: [(sleeper + return points projection, name), ...] best first} for the team's available bench players
    that Sleeper does project. Each entry can replace one starter (the caller pops it)."""
    starters = {p for p in (matchup.get("starters") or []) if p not in (None, "0")}
    out = {}
    for pid in matchup.get("players") or []:
        info = players.get(pid)
        if pid in starters or info is None or info.get("availability"):
            continue
        value, source = pick_projection(info, "sleeper_returns")
        if source == "model":                                  # Sleeper doesn't project him either
            continue
        out.setdefault(info["position"], []).append((value, info["name"]))
    return {pos: sorted(lst, key=lambda x: -x[0]) for pos, lst in out.items()}


SLOT_ELIGIBLE = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"}, "FLEX": {"RB", "WR", "TE"},
                 "WRRB_FLEX": {"RB", "WR"}, "REC_FLEX": {"WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}}


def _best_assignment(slots, pool):
    """
    Best lineup: which pool player fills which slot. `pool` = [(pid, position, value), ...]; a slot may stay empty (worth 0).
    Returns {slot index: pool index} for the filled slots. Hungarian algorithm (the slots are few, so it is instant).
    """
    n, m = len(slots), len(pool)
    if n == 0 or m == 0:
        return {}
    cols = m + n                                               # n extra zero-value 'empty' columns so every slot can be left unfilled
    INF = 1e9
    cost = [[0.0] * cols for _ in range(n)]
    for i, slot in enumerate(slots):
        ok = SLOT_ELIGIBLE.get(slot, {slot})
        for j, (_, pos, value) in enumerate(pool):
            cost[i][j] = -float(value) if pos in ok else INF
    u, v, p, way = [0.0] * (n + 1), [0.0] * (cols + 1), [0] * (cols + 1), [0] * (cols + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [float("inf")] * (cols + 1), [False] * (cols + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            for j in range(1, cols + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            p[j0] = p[way[j0]]
            j0 = way[j0]
    return {p[j] - 1: j - 1 for j in range(1, m + 1) if p[j]}


def _ideal_replacements(rows, matchup, players, games, slots):
    """
    For starters Sleeper doesn't project and who aren't returners (`rows` flagged '_lack'): assume they are unavailable and fill the
    slots they leave from the rest of the roster, moving people between slots (a WR to FLEX / SUPER_FLEX frees a slot for someone else).
    Every other starter stays a starter (an owner's other lineup choices are not second-guessed); only bench players come in. Only players
    whose games haven't started can be moved. The bench players' points are shared out over the flagged rows (0 if nobody can fill the
    slot). Returns {player_id: (value, note)}; flagged players whose game has started are left out (they keep the model's number).
    """
    starters = matchup.get("starters") or []

    def locked(pid):
        info = players.get(pid)
        g = games.get(info.get("team")) if info else None
        return bool(g and g["state"] in ("in", "post"))

    flagged = [r for r in rows if r["_lack"] and not locked(r["player_id"])]
    if not flagged:
        return {}
    flagged_ids = {r["player_id"] for r in flagged}
    # slots that can still change: empty, or held by a player (in the export) whose game hasn't started
    free = [i for i, pid in enumerate(starters) if pid in (None, "0") or (pid in players and not locked(pid))]
    in_lineup = {pid for pid in starters if pid not in (None, "0")}
    pool, seen = [], set()
    for pid in list(matchup.get("players") or []) + list(in_lineup):
        info = players.get(pid)
        if pid in seen or info is None or pid in flagged_ids or info.get("availability") or locked(pid):
            continue
        seen.add(pid)
        value, source = pick_projection(info, "sleeper_returns")
        if source == "model":                                  # Sleeper doesn't project him either
            continue
        pool.append((pid, info["position"], value))
    # the other starters are kept in the lineup (a big bonus makes the solver always start them); it only decides who else comes in and
    # which slots everyone takes
    keep_bonus = 1e6
    biased = [(pid, pos, value + (keep_bonus if pid in in_lineup else 0.0)) for pid, pos, value in pool]
    chosen = _best_assignment([slots[i] for i in free], biased)
    promoted_idx = [j for j in chosen.values() if pool[j][0] not in in_lineup]
    promoted = [players[pool[j][0]]["name"] for j in promoted_idx]
    gain = sum(pool[j][2] for j in promoted_idx)
    weights = [max(float(r["_model"] or 0.0), 0.0) for r in flagged]
    total_w = sum(weights)
    shares = [(w / total_w if total_w else 1.0 / len(flagged)) * gain for w in weights]
    him = "him" if len(flagged) == 1 else "them"
    note = (f"* Sleeper has no projection: the ideal lineup without {him} starts {', '.join(promoted)} (team +{gain:.1f})" if promoted
            else f"* Sleeper has no projection and nobody on the roster can take {'his' if len(flagged) == 1 else 'their'} slot: counting 0")
    return {r["player_id"]: (share, note) for r, share in zip(flagged, shares)}


def starter_table(matchup, inputs, games, projection=None, slots=None):
    """One row per starter of one team: points so far, game state, expected remaining and expected final.
    `projection` is 'sleeper_returns' | 'sleeper' | 'model' (default: DEFAULT_PROJECTION).
    `slots` = the league's starting slots in order (fetch_roster_positions); lets a starter Sleeper doesn't project be replaced by the best
    possible lineup, otherwise (None) by the best bench player at his position."""
    players = inputs["players"]
    sigma = inputs["sigma_by_position"]
    global_sigma = inputs.get("global_rmse", 7.5)
    pos_mean = inputs.get("position_mean_projection", {})
    rows = []
    starters = matchup.get("starters") or []
    pts_list = matchup.get("starters_points") or [0.0] * len(starters)
    use_bench = (projection if projection in PROJECTION_LABELS else DEFAULT_PROJECTION) == "sleeper_returns"
    use_slots = bool(slots) and len(slots) == len(starters)
    bench = _bench_by_position(matchup, players) if use_bench and not use_slots else {}
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
        proj, source = pick_projection(info, projection)
        name = info["name"]
        note = ""
        lack = use_bench and _lacks_sleeper_projection(info)
        if lack and not use_slots:
            # Sleeper doesn't project him (injury doubt, a lost job, a returning starter ...): count the best bench player at his position instead
            queue = bench.get(info.get("position"), [])
            if queue:
                value, rep_name = queue.pop(0)
                proj, source = value, "replacement"
                note = f"* Sleeper has no projection: counting best bench {info.get('position')} {rep_name} ({value:.1f})"
            else:
                note = f"* Sleeper has no projection and no bench {info.get('position')}: using the model ({proj:.1f})"
            name += "*"
        elif lack:
            name += "*"                                        # value and note are filled in below, once the ideal lineup is known
        sig = float(sigma.get(info.get("position"), global_sigma))
        exp_rem = f * proj
        flag = info.get("availability", "") or note
        if info.get("position") == "DEF" and g and g["state"] == "in":
            # Defense scores in steps (points / yards allowed) that move down as well as up, so prorating a projection
            # doesn't fit: while the game is on, expected final = current. The +/- still shrinks with time left.
            exp_rem = 0.0
            flag = flag or "DEF: final = current"
        rows.append({
            "player_id": pid, "player": name, "pos": info["position"], "nfl_team": team,
            "game": (g["detail"] if g else "BYE"), "state": (g["state"] if g else "bye"),
            "pts_so_far": float(pts or 0.0), "fraction_left": f, "projection": proj,
            "expected_remaining": exp_rem, "expected_final": float(pts or 0.0) + exp_rem,
            "var_remaining": (sig ** 2) * f if proj > 0 else 0.0, "flag": flag,
            "source": source, "model_projection": info.get("model_projection", info.get("projected_points")),
            "sleeper_projection": info.get("sleeper_projection"), "return_pts": info.get("return_pts_projection"),
            "sleeper_plus_returns": info.get("sleeper_plus_returns"),
            "_lack": bool(lack and use_slots), "_model": info.get("model_projection", info.get("projected_points")),
        })
    if use_slots and any(r["_lack"] for r in rows):
        fixes = _ideal_replacements(rows, matchup, players, games, slots)
        for r in rows:
            if r["player_id"] in fixes:
                value, note = fixes[r["player_id"]]
                f = r["fraction_left"]
                r.update(projection=value, source="replacement", flag=note, expected_remaining=f * value,
                         expected_final=r["pts_so_far"] + f * value, var_remaining=(float(sigma.get(r["pos"], global_sigma)) ** 2) * f if value > 0 else 0.0)
            elif r["_lack"] and r["state"] in ("in", "post"):
                r["flag"] = f"* Sleeper has no projection and his game is under way: using the model ({r['projection']:.1f})"
    for r in rows:
        r.pop("_lack", None); r.pop("_model", None)
    return pd.DataFrame(rows)


def live_team_table(matchups, inputs, games, projection=None, slots=None):
    """One row per live team (teams with an empty roster were already eliminated)."""
    owners = inputs["owners"]
    out, details = [], {}
    for m in matchups:
        if not m.get("players"):
            continue
        rid = m["roster_id"]
        st = starter_table(m, inputs, games, projection, slots)
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
    t["p_in_elim_spot"] = (rank < min(int(k), T)).mean(axis=1)        # finishes in a cut position, whether or not immune
    t["immune"] = immune
    t["locked"] = (t["expected_remaining"] == 0) & (t["sd_remaining"] == 0)
    t["k"] = k_eff
    return t


WIN_MARGIN = 0.1        # the winning score is this far above the runner-up's score


def score_lines(teams, k, immune_owners=(), n_sims=20000, seed=42, safe_pct=95.0, win_pct=50.0, win_margin=WIN_MARGIN):
    """
    Two scores to aim for this week, from the same simulation as the odds:
      safe     the score that beats the elimination cut line in `safe_pct`% of simulations. The cut line is the score of the last team cut
               (the k-th lowest among teams that can be cut; immune teams are not cut), so a team above `safe` survives that often.
      winning  the score that would have won the week in `win_pct`% of simulations: the runner-up's score (the second highest of the week, so a
               team above it beats everyone else) plus `win_margin`. Its median across the simulations at the default 50%.
    Returns {"safe": float or None, "winning": float, "safe_pct": ..., "win_pct": ...}; safe is None if nobody can be cut.
    """
    t = teams.reset_index(drop=True)
    T = len(t)
    if T == 0:
        return {"safe": None, "winning": None, "safe_pct": safe_pct, "win_pct": win_pct}
    rng = np.random.default_rng(seed)
    Z = rng.standard_normal((T, n_sims))
    total = t["current"].values[:, None] + np.maximum(t["expected_remaining"].values[:, None] + t["sd_remaining"].values[:, None] * Z, 0.0)
    immune = t["owner"].str.lower().isin({u.lower() for u in immune_owners}).values
    eligible = total[~immune]
    k_eff = int(min(k, eligible.shape[0]))
    safe = None
    if k_eff >= 1:
        cut_line = np.partition(eligible, k_eff - 1, axis=0)[k_eff - 1]              # score of the last team cut, in each simulation
        safe = float(np.quantile(cut_line, safe_pct / 100.0))
    runner_up = np.partition(total, T - 2, axis=0)[T - 2] if T >= 2 else total.max(axis=0)          # second-highest score in each simulation
    winning = float(np.quantile(runner_up, win_pct / 100.0)) + (win_margin if T >= 2 else 0.0)
    return {"safe": safe, "winning": winning, "safe_pct": safe_pct, "win_pct": win_pct}


def live_snapshot(league_id, season, week, inputs, immune_owners=(), k=None, n_sims=20000, projection=None):
    """Everything the page needs in one call."""
    matchups = fetch_matchups(league_id, week)
    games, clock = fetch_game_states_detailed(season, week)
    teams, details = live_team_table(matchups, inputs, games, projection, fetch_roster_positions(league_id))
    if k is None:
        k = int(inputs.get("eliminations_by_week", {}).get(str(week), 1))
    standings = simulate_standings(teams, k, immune_owners, n_sims)
    return {"standings": standings, "details": details, "games": games, "k": k, "projection": projection or DEFAULT_PROJECTION,
            "lines": score_lines(teams, k, immune_owners, n_sims),
            "clock_source": clock["source"], "clock_estimated": clock["estimated"], "clock_attempts": clock["attempts"],
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "fetched_epoch": time.time(),
            "fetched_central": format_central(time.time())}


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
