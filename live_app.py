"""
Live Guillotine standings page.   Start with:  start_live_view.bat   (or:  streamlit run live_app.py)

Needs live_inputs_<season>_wk<week>.json (the model's projections). "refresh_live_inputs.py" rewrites it hourly;
"Guillotine Week Projection 5.ipynb" produces the same file by hand.
Shared settings (immune teams, cut override) live in live_config.json so every viewer sees the same thing.
"""
import os

import pandas as pd
import streamlit as st

import guillotine_live as gl

st.set_page_config(page_title="Guillotine live", page_icon="⚔️", layout="wide")


@st.cache_data(ttl=60)
def cached_state():
    return gl.nfl_state()


@st.cache_data(ttl=30)
def cached_inputs(path, mtime):
    return gl.load_inputs(path)


def log_snapshot(snap, season, week):
    """Append to a CSV while games are in progress (lets us check calibration against real games later)."""
    if os.environ.get("GUILLOTINE_NO_LOG") or not any(g["state"] == "in" for g in snap["games"].values()):
        return
    df = snap["standings"][["owner", "current", "expected_final", "sd_remaining", "p_last", "p_eliminated", "p_first"]].copy()
    df.insert(0, "time", snap["fetched_at"])
    file = os.path.join(gl.HERE, f"live_snapshots_{season}_wk{week}.csv")
    try:
        df.to_csv(file, mode="a", header=not os.path.exists(file), index=False)
    except Exception:
        pass


@st.cache_data(ttl=15, show_spinner=False)
def cached_snapshot(path, mtime, season, week, immune, k, n_sims):
    """One computation shared by every viewer (at most one Sleeper/ESPN fetch + simulation per 15 seconds)."""
    inputs = cached_inputs(path, mtime)
    snap = gl.live_snapshot(inputs["league_id"], season, week, inputs, immune_owners=list(immune), k=k, n_sims=n_sims)
    log_snapshot(snap, season, week)
    return snap


# ---------------------------------------------------------------- sidebar
try:
    state = cached_state()
except Exception as e:
    st.error(f"Can't reach Sleeper to find the current NFL week: {e}")
    st.stop()

cfg = gl.load_config()
cfg_key = "|".join(sorted(u.lower() for u in cfg["immune_teams"])) + f"#{cfg['eliminations_override']}"      # new config -> widgets reset

st.sidebar.header("Settings")
season = st.sidebar.number_input("Season", value=state["season"], step=1, format="%d")
week = st.sidebar.number_input("Week", value=state["week"], min_value=1, max_value=18, step=1)
refresh = st.sidebar.slider("Refresh every (seconds)", 10, 120, 30, step=5)
n_sims = st.sidebar.select_slider("Simulations", options=[5000, 10000, 20000, 50000], value=10000)

path = gl.find_inputs_file(int(season), int(week))
if not path:
    st.title("⚔️ Guillotine live")
    st.error(f"No `live_inputs_{int(season)}_wk{int(week)}.json` next to this app yet. It is created by `refresh_live_inputs.py` "
             f"(hourly) or by **Guillotine Week Projection 5.ipynb**.")
    st.stop()
mtime = os.path.getmtime(path)
inputs = cached_inputs(path, mtime)

owners = sorted((inputs["owners"][str(r)] for r in inputs["alive_roster_ids"]), key=str.lower)
lower_owners = [o.lower() for o in owners]
hl = cfg["highlight_default"].lower()
my_team = st.sidebar.selectbox("My team (highlighted)", ["-"] + owners, index=(1 + lower_owners.index(hl)) if hl in lower_owners else 0)
immune_default = [o for o in owners if o.lower() in {u.lower() for u in cfg["immune_teams"]}]
immune = st.sidebar.multiselect("Immune teams (can't be eliminated)", owners, default=immune_default, key=f"immune_{cfg_key}")
default_k = int(inputs.get("eliminations_by_week", {}).get(str(int(week)), 1))
k_override = st.sidebar.number_input(f"Teams eliminated (0 = schedule: {default_k})", min_value=0, max_value=5,
                                     value=cfg["eliminations_override"], step=1, key=f"k_{cfg_key}")
k = int(k_override) if k_override else default_k
age = gl.inputs_age_minutes(inputs)
age_txt = f" ({age / 60:.1f} h ago)" if age is not None and age >= 90 else (f" ({age:.0f} min ago)" if age is not None else "")
st.sidebar.caption(f"Projections: {os.path.basename(path)}\n\ngenerated {inputs.get('generated_at', '?')}{age_txt}")
st.sidebar.caption("Immune teams and the cut count are shared settings (live_config.json). Changing them here only affects your own view.")


# ---------------------------------------------------------------- live panel
@st.fragment(run_every=f"{refresh}s")
def live_panel():
    try:
        snap = cached_snapshot(path, mtime, int(season), int(week), tuple(sorted(immune)), k, n_sims)
        st.session_state["last_snap"] = snap
    except Exception as e:
        snap = st.session_state.get("last_snap")
        st.warning(f"Refresh failed ({type(e).__name__}: {e}); showing the last good data.")
        if snap is None:
            return

    s = snap["standings"].copy()
    games = snap["games"]
    n_in = len({frozenset((t, g["opponent"])) for t, g in games.items() if g["state"] == "in"})
    st.caption(f"Updated {snap['fetched_at']}  ·  refreshes every {refresh}s  ·  game clock: {snap['clock_source']}  ·  {n_in} game(s) in progress")
    if snap.get("clock_estimated") and n_in:
        why = "; ".join(f"{name}: {result}" for name, result in snap.get("clock_attempts", []))
        how = "from kickoff times (accurate to roughly ±10% of a game)" if "kickoff" in snap["clock_source"] else "as half over"
        st.warning(f"The live game clock can't be reached from this server, so games in progress are estimated {how}. "
                   f"Probabilities are approximate. Details: {why or 'no source answered'}")
    if age is not None and age > 180:
        st.warning(f"The model projections are {age / 60:.1f} hours old (the hourly refresh may have stopped). Live scores are current.")

    # starters put in after the last projection refresh have no model projection yet
    missing = sorted({(r["player_id"], r["player"]) for d in snap["details"].values() for _, r in d.iterrows() if r["flag"] == "NOT IN EXPORT"})
    if missing:
        st.info(f"{len(missing)} starter(s) were added since the last projection refresh and use the average starter projection until the next "
                f"hourly refresh: {', '.join(pid for pid, _ in missing)}")

    s["rank_now"] = s["current"].rank(ascending=False, method="min").astype(int)
    s["team"] = [("★ " if o == my_team else "") + ("🛡 " if imm else "") + o for o, imm in zip(s["owner"], s["immune"])]
    s["status"] = ["LOCKED" if lk else ("immune" if imm else "") for lk, imm in zip(s["locked"], s["immune"])]
    s = s.sort_values(["p_eliminated", "p_last"], ascending=False).reset_index(drop=True)

    pct = st.column_config.ProgressColumn
    cols = {
        "rank_now": st.column_config.NumberColumn("Rank now", format="%d", help="Rank by points so far"),
        "team": "Team",
        "current": st.column_config.NumberColumn("Points", format="%.1f"),
        "expected_final": st.column_config.NumberColumn("Projected final", format="%.1f"),
        "sd_remaining": st.column_config.NumberColumn("± (1 sd)", format="%.1f", help="Uncertainty in the points still to come"),
        "done": pct("Game time done", format="%.0f%%", min_value=0, max_value=100, help="Average share of the starters' games already played"),
        "p_eliminated": pct(f"P(eliminated, bottom {snap['k']})" if snap["k"] > 1 else "P(eliminated)", format="%.1f%%", min_value=0, max_value=100),
        "p_last": pct("P(last)", format="%.1f%%", min_value=0, max_value=100),
        "p_second_last": pct("P(2nd last)", format="%.1f%%", min_value=0, max_value=100),
        "p_first": pct("P(first)", format="%.1f%%", min_value=0, max_value=100),
        "status": "Status",
    }
    s["done"] = s["progress"] * 100
    for c in ("p_eliminated", "p_last", "p_second_last", "p_first"):
        s[c] = s[c] * 100
    show = ["rank_now", "team", "current", "expected_final", "sd_remaining", "done", "p_eliminated", "p_last"]
    if snap["k"] > 1:
        show.append("p_second_last")
    show += ["p_first", "status"]
    st.dataframe(s[show], hide_index=True, width="stretch", column_config={c: cols[c] for c in show}, height=min(880, 40 + 35 * len(s)))

    left, right = st.columns([1, 1])
    with left:
        st.subheader("NFL games")
        seen, rows = set(), []
        for t, g in games.items():
            key = frozenset((t, g["opponent"]))
            if key in seen:
                continue
            seen.add(key)
            away, home = (g["opponent"], t) if g["home"] else (t, g["opponent"])
            sc = f"{g['opp_score'] if g['home'] else g['score']}-{g['score'] if g['home'] else g['opp_score']}" if g["score"] is not None else ""
            rows.append({"Game": f"{away} @ {home}", "Status": g["detail"], "Score (away-home)": sc, "_o": {"in": 0, "pre": 1, "post": 2}[g["state"]], "_k": g["kickoff"] or ""})
        gdf = pd.DataFrame(rows).sort_values(["_o", "_k"]).drop(columns=["_o", "_k"])
        st.dataframe(gdf, hide_index=True, width="stretch", height=min(600, 40 + 35 * len(gdf)))
    with right:
        st.subheader("Team detail")
        options = list(s["owner"])
        default = options.index(my_team) if my_team in options else 0
        who = st.selectbox("Team", options, index=default, key="detail_team")
        rid = int(s.loc[s["owner"] == who, "roster_id"].iloc[0])
        d = snap["details"][rid].copy()
        d["fraction_left"] = d["fraction_left"] * 100
        d = d.rename(columns={"player": "Player", "pos": "Pos", "nfl_team": "Team", "game": "Game", "pts_so_far": "Pts", "fraction_left": "% left",
                              "projection": "Full-game proj", "expected_remaining": "Proj remaining", "expected_final": "Expected final", "flag": "Flag"})
        st.dataframe(d[["Player", "Pos", "Team", "Game", "Pts", "% left", "Full-game proj", "Proj remaining", "Expected final", "Flag"]],
                     hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(c, format="%.1f") for c in ("Pts", "% left", "Full-game proj", "Proj remaining", "Expected final")})


st.title("⚔️ Guillotine live")
st.markdown(f"**{int(season)} · Week {int(week)}** — {k} team(s) eliminated"
            + (f" · immune: {', '.join(immune)}" if immune else "")
            + "  \nExpected final = points so far + (fraction of each starter's game left × the model's projection). "
              "Uncertainty shrinks with the square root of the game time remaining.")
live_panel()
