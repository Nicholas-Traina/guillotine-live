"""
Recorded history of the live standings odds, for the Trends tab.

The x-axis is "football time": it only advances while at least one NFL game is in progress, so the days between
Thursday night and Sunday don't stretch the chart. Each recorded row carries the running total of live seconds.

A background Sampler thread (started once per server process) computes a snapshot every ~30 s during games and
appends one row per team to live_history_<season>_wk<week>.csv, whether or not anyone has the page open.
Limits: it can only record while the server is running (the hosted app sleeps after ~12 h without any visitor and its disk
is wiped on redeploys), and it uses the shared settings from live_config.json (immune teams / cut count).
"""
import os
import threading
import time
import traceback

import numpy as np
import pandas as pd

import guillotine_live as gl

COLUMNS = ["epoch", "live_seconds", "owner", "current", "expected_final", "sd_remaining",
           "p_eliminated", "p_in_elim_spot", "p_first", "k"]
MAX_CREDIT_GAP = 240      # seconds: a longer gap between two live samples is a break (no football time is credited)
BREAK_GAP = 900           # seconds: gaps at least this long are drawn as breaks on the plots


def history_path(season, week, folder=None):
    folder = folder or os.environ.get("GUILLOTINE_HISTORY_DIR") or gl.HERE
    return os.path.join(folder, f"live_history_{season}_wk{week}.csv")


class Recorder:
    """Appends live-game snapshots to the week's history file and keeps the football-time clock."""

    def __init__(self, season, week, folder=None):
        self.path = history_path(season, week, folder)
        self.live_seconds = 0.0
        self.last_epoch = None
        try:
            df = load_history(season, week, folder)
            if len(df):
                self.live_seconds = float(df["live_seconds"].iloc[-1])
                self.last_epoch = float(df["epoch"].iloc[-1])        # so a restart mid-game continues the clock
        except Exception:
            pass

    def record(self, snap, now=None):
        """Store one row per team if any game is in progress. Returns True when something was recorded."""
        if not any(g["state"] == "in" for g in snap["games"].values()):
            return False
        now = time.time() if now is None else float(now)
        if self.last_epoch is not None and 0 < now - self.last_epoch <= MAX_CREDIT_GAP:
            self.live_seconds += now - self.last_epoch
        self.last_epoch = now
        s = snap["standings"]
        rows = pd.DataFrame({
            "epoch": round(now, 1), "live_seconds": round(self.live_seconds, 1), "owner": s["owner"].values,
            "current": s["current"].round(2).values, "expected_final": s["expected_final"].round(2).values,
            "sd_remaining": s["sd_remaining"].round(2).values, "p_eliminated": s["p_eliminated"].round(4).values,
            "p_in_elim_spot": s["p_in_elim_spot"].round(4).values, "p_first": s["p_first"].round(4).values, "k": int(snap["k"]),
        })[COLUMNS]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        rows.to_csv(self.path, mode="a", header=not os.path.exists(self.path), index=False)
        return True


def load_history(season, week, folder=None):
    """The week's recorded history (empty DataFrame with the right columns if nothing was recorded yet)."""
    path = history_path(season, week, folder)
    if not os.path.exists(path):
        return pd.DataFrame(columns=COLUMNS)
    for _ in range(3):                                   # the sampler may be mid-append: retry on a torn read
        try:
            return pd.read_csv(path, on_bad_lines="skip")
        except Exception:
            time.sleep(0.2)
    return pd.DataFrame(columns=COLUMNS)


def find_breaks(df):
    """[(football_minutes_at_resume, resume_epoch), ...] wherever recording resumed after a gap of BREAK_GAP+ seconds."""
    if df.empty:
        return []
    t = df.drop_duplicates("epoch").sort_values("epoch")[["epoch", "live_seconds"]]
    gap = t["epoch"].diff()
    hit = t[gap >= BREAK_GAP]
    return [(float(r.live_seconds) / 60.0, float(r.epoch)) for r in hit.itertuples()]


def downsample(df, max_points=400):
    """Keep at most max_points sample times (evenly spread, always including the last) so charts stay light."""
    times = np.sort(df["epoch"].unique())
    if len(times) <= max_points:
        return df
    keep = set(times[np.unique(np.linspace(0, len(times) - 1, max_points).round().astype(int))])
    return df[df["epoch"].isin(keep)]


def plot_frame(df, teams, metric):
    """Long-format frame for one plot: football minutes, team, probability in %, Central clock time of the sample."""
    d = downsample(df[df["owner"].isin(teams)])
    out = pd.DataFrame({"minutes": d["live_seconds"] / 60.0, "team": d["owner"], "pct": d[metric] * 100.0,
                        "clock": [gl.format_central(e, seconds=False, day=True) for e in d["epoch"]]})
    return out.reset_index(drop=True)


class Sampler(threading.Thread):
    """Background thread: snapshot every ~30 s while games are live (every ~60 s otherwise), record via a Recorder."""

    def __init__(self, interval_live=30, interval_idle=60, n_sims=10000, folder=None):
        super().__init__(daemon=True, name="guillotine-history-sampler")
        self.interval_live, self.interval_idle, self.n_sims, self.folder = interval_live, interval_idle, n_sims, folder
        self.recorders = {}
        self.last_status, self.last_error, self.last_time = "starting", None, None
        self._stop_flag = threading.Event()

    def step(self):
        """One sampling pass. Returns 'recorded', 'idle' (no game live) or a short reason it was skipped."""
        st = gl.nfl_state()
        season, week = st["season"], st["week"]
        if st.get("season_type") != "regular":
            return "not regular season"
        path = gl.find_inputs_file(season, week)
        if not path:
            return "no projections file for this week"
        inputs = gl.load_inputs(path)
        cfg = gl.load_config()
        k = cfg["eliminations_override"] or int(inputs.get("eliminations_by_week", {}).get(str(week), 1))
        snap = gl.live_snapshot(inputs["league_id"], season, week, inputs, immune_owners=cfg["immune_teams"], k=k, n_sims=self.n_sims)
        rec = self.recorders.setdefault((season, week), Recorder(season, week, self.folder))
        return "recorded" if rec.record(snap) else "idle"

    def run(self):
        while not self._stop_flag.is_set():
            try:
                self.last_status = self.step()
                self.last_error = None
            except Exception:
                self.last_status = "error"
                self.last_error = traceback.format_exc(limit=3)
            self.last_time = time.time()
            self._stop_flag.wait(self.interval_live if self.last_status == "recorded" else self.interval_idle)

    def stop(self):
        self._stop_flag.set()
