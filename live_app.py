"""
Live Guillotine standings page.   Start with:  start_live_view.bat   (or:  streamlit run live_app.py)

Needs live_inputs_<season>_wk<week>.json (the model's projections). "refresh_live_inputs.py" rewrites it hourly;
"Guillotine Week Projection 5.ipynb" produces the same file by hand.
Shared settings (immune teams, cut override) live in live_config.json so every viewer sees the same thing.
Built phone-first: team cards in a one-column grid on small screens, tabs instead of side-by-side tables,
and the settings sidebar starts collapsed.
"""
import os

import pandas as pd
import streamlit as st

import guillotine_history as gh
import guillotine_live as gl
import live_cards
import live_charts

st.set_page_config(page_title="Guillotine live", page_icon="⚔️", layout="wide", initial_sidebar_state="collapsed")
st.markdown(live_cards.CSS, unsafe_allow_html=True)

SORTS = {
    "Elimination risk": (["p_eliminated", "p_last"], False),
    "Points now": (["current"], False),
    "Projected final": (["expected_final"], False),
    "First-place chance": (["p_first"], False),
}


@st.cache_data(ttl=60)
def cached_state():
    return gl.nfl_state()


@st.cache_data(ttl=30)
def cached_inputs(path, mtime):
    return gl.load_inputs(path)


@st.cache_resource
def start_sampler():
    """One background recorder per server process: keeps the Trends history whether or not anyone has the page open."""
    if os.environ.get("GUILLOTINE_NO_LOG"):                      # tests / previews must not write real history
        return None
    sampler = gh.Sampler()
    sampler.start()
    return sampler


sampler = start_sampler()


def recorder_status_text():
    """One line saying whether the background recorder is alive (so an empty chart is never a mystery)."""
    if sampler is None:
        return ""
    when = gl.format_central(sampler.last_time, seconds=False) if sampler.last_time else "starting up"
    state = {"recorded": "recording (a game is live)", "idle": "waiting for a game to start"}.get(sampler.last_status, sampler.last_status)
    return f"Recorder: {state} · last check {when}" + (" · hit an error, retrying" if sampler.last_error else "")


def show_chart(chart):
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:                                            # older Streamlit
        st.altair_chart(chart, use_container_width=True)


@st.cache_data(ttl=15, show_spinner=False)
def cached_snapshot(path, mtime, season, week, immune, k, n_sims):
    """One computation shared by every viewer (at most one Sleeper/ESPN fetch + simulation per 15 seconds)."""
    inputs = cached_inputs(path, mtime)
    return gl.live_snapshot(inputs["league_id"], season, week, inputs, immune_owners=list(immune), k=k, n_sims=n_sims)


# ---------------------------------------------------------------- sidebar (settings; collapsed by default)
try:
    state = cached_state()
except Exception as e:
    st.error(f"Can't reach Sleeper to find the current NFL week: {e}")
    st.stop()

cfg = gl.load_config()
cfg_key = "|".join(sorted(u.lower() for u in cfg["immune_teams"])) + f"#{cfg['eliminations_override']}"      # new config -> widgets reset

st.sidebar.header("Settings")
default_view = os.environ.get("GUILLOTINE_DEFAULT_VIEW", "Cards")
view = st.sidebar.radio("View", ["Cards", "Table"], index=0 if default_view == "Cards" else 1, horizontal=True)
sort_by = st.sidebar.selectbox("Sort by", list(SORTS), index=0)
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
immune_default = [o for o in owners if o.lower() in {u.lower() for u in cfg["immune_teams"]}]
immune = st.sidebar.multiselect("Immune teams (can't be eliminated)", owners, default=immune_default, key=f"immune_{cfg_key}")
default_k = int(inputs.get("eliminations_by_week", {}).get(str(int(week)), 1))
k_override = st.sidebar.number_input(f"Teams eliminated (0 = schedule: {default_k})", min_value=0, max_value=5,
                                     value=cfg["eliminations_override"], step=1, key=f"k_{cfg_key}")
k = int(k_override) if k_override else default_k
age = gl.inputs_age_minutes(inputs)
age_txt = f" ({age / 60:.1f} h ago)" if age is not None and age >= 90 else (f" ({age:.0f} min ago)" if age is not None else "")
st.sidebar.caption(f"Projections: {os.path.basename(path)}\n\ngenerated {gl.inputs_generated_text(inputs)}{age_txt}")
st.sidebar.caption("Immune teams and the cut count are shared settings (live_config.json). Changing them here only affects your own view.")

# ---------------------------------------------------------------- header + "my team" (top of the page, not hidden in the sidebar)
st.markdown(f"### ⚔️ Guillotine · week {int(week)}")
my_team = st.selectbox("★ My team", ["-"] + owners, index=(1 + lower_owners.index(hl)) if hl in lower_owners else 0)
st.caption(f"{k} team{'s' if k != 1 else ''} eliminated this week" + (f" · 🛡 immune: {', '.join(immune)}" if immune else ""))


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
    st.caption(f"Updated {snap['fetched_central']} · {n_in} live · clock: {snap['clock_source']}")
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
    s["rank_tied"] = s.groupby("rank_now")["rank_now"].transform("size") > 1
    cols_, asc = SORTS[sort_by]
    s = s.sort_values(cols_, ascending=asc).reset_index(drop=True)

    tab_stand, tab_detail, tab_games, tab_trends = st.tabs(["Standings", "Team detail", "NFL games", "Trends"])

    with tab_stand:
        if view == "Cards":
            st.markdown(live_cards.cards_html(s, snap["k"], my_team), unsafe_allow_html=True)
        else:
            t = s.copy()
            t["team"] = [("★ " if o == my_team else "") + ("🛡 " if imm else "") + o for o, imm in zip(t["owner"], t["immune"])]
            t["status"] = ["LOCKED" if lk else ("immune" if imm else "") for lk, imm in zip(t["locked"], t["immune"])]
            t["done"] = t["progress"] * 100
            for c in ("p_eliminated", "p_last", "p_second_last", "p_first"):
                t[c] = t[c] * 100
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
            show = ["rank_now", "team", "current", "expected_final", "sd_remaining", "done", "p_eliminated", "p_last"]
            if snap["k"] > 1:
                show.append("p_second_last")
            show += ["p_first", "status"]
            st.dataframe(t[show], hide_index=True, width="stretch", column_config={c: cols[c] for c in show}, height=min(880, 40 + 35 * len(t)))

    with tab_detail:
        options = list(s["owner"])
        default = options.index(my_team) if my_team in options else 0
        who = st.selectbox("Team", options, index=default, key="detail_team")
        rid = int(s.loc[s["owner"] == who, "roster_id"].iloc[0])
        d = snap["details"][rid].copy()
        d["fraction_left"] = d["fraction_left"] * 100
        compact = pd.DataFrame({"Player": d["player"] + " (" + d["pos"] + ")", "Pts": d["pts_so_far"], "Exp. final": d["expected_final"], "Game": d["game"]})
        st.dataframe(compact, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(c, format="%.1f") for c in ("Pts", "Exp. final")})
        with st.expander("All columns"):
            full = d.rename(columns={"player": "Player", "pos": "Pos", "nfl_team": "Team", "game": "Game", "pts_so_far": "Pts", "fraction_left": "% left",
                                     "projection": "Full-game proj", "expected_remaining": "Proj remaining", "expected_final": "Expected final", "flag": "Flag",
                                     "source": "Source", "model_projection": "Model proj", "sleeper_projection": "Sleeper proj"})
            st.dataframe(full[["Player", "Pos", "Team", "Game", "Pts", "% left", "Full-game proj", "Source", "Model proj", "Sleeper proj", "Proj remaining", "Expected final", "Flag"]],
                         hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(c, format="%.1f") for c in ("Pts", "% left", "Full-game proj", "Model proj", "Sleeper proj", "Proj remaining", "Expected final")})

    with tab_games:
        seen, rows = set(), []
        for t_, g in games.items():
            key = frozenset((t_, g["opponent"]))
            if key in seen:
                continue
            seen.add(key)
            away, home = (g["opponent"], t_) if g["home"] else (t_, g["opponent"])
            sc = f"{g['opp_score'] if g['home'] else g['score']}-{g['score'] if g['home'] else g['opp_score']}" if g["score"] is not None else ""
            rows.append({"Game": f"{away} @ {home}", "Status": g["detail"], "Score": sc, "_o": {"in": 0, "pre": 1, "post": 2}[g["state"]], "_k": g["kickoff"] or ""})
        gdf = pd.DataFrame(rows).sort_values(["_o", "_k"]).drop(columns=["_o", "_k"])
        st.dataframe(gdf, hide_index=True, width="stretch", height=min(700, 40 + 35 * len(gdf)))

    with tab_trends:
        hist = gh.load_history(int(season), int(week))
        if hist.empty:
            st.info("No live-game history for this week yet. The server records the odds every ~30 seconds while an NFL game is in "
                    "progress, so this fills in once the first game kicks off.")
            st.caption(recorder_status_text())
        else:
            all_teams = sorted(hist["owner"].unique(), key=str.lower)
            latest = hist[hist["epoch"] == hist["epoch"].max()].set_index("owner")
            riskiest = [t for t in latest["p_eliminated"].sort_values(ascending=False).index if t in all_teams]
            default = ([my_team] if my_team in all_teams else []) + [t for t in riskiest if t != my_team]
            chosen = st.multiselect("Teams to plot", all_teams, default=default[:5], key="trend_teams")
            if not chosen:
                st.info("Pick at least one team above.")
            else:
                breaks = gh.find_breaks(hist)
                for metric in ("p_eliminated", "p_in_elim_spot", "p_first"):
                    title, blurb = live_charts.TITLES[metric]
                    st.markdown(f"**{title}**")
                    st.caption(blurb)
                    show_chart(live_charts.trend_chart(gh.plot_frame(hist, chosen, metric), breaks))
                first, last = hist["epoch"].min(), hist["epoch"].max()
                st.caption(f"Time only runs while an NFL game is on (dotted lines mark breaks, labelled with when play resumed, in Central time). "
                           f"{hist['live_seconds'].max() / 3600:.1f} live hours recorded, {gl.format_central(first, seconds=False, day=True)} to "
                           f"{gl.format_central(last, seconds=False, day=True)}. Uses the shared immune-team / cut settings. "
                           f"History restarts if the server restarts. {recorder_status_text()}")


live_panel()

with st.expander("How this works"):
    st.markdown("**Projected final** = points so far + (fraction of each starter's game still to play × the model's projection). "
                "Projections come from the model for players who average more than 2 points per game from return yardage this season (Sleeper barely counts return yardage) and from Sleeper for everyone else; "
                "if a returner's model number is below Sleeper's, Sleeper's plus his average return points per game is used, so no projection is below Sleeper's. "
                "'All columns' under Team detail shows both numbers. "
                "A defense that's mid-game is held at its current score. **±** is the uncertainty in the points still to come and shrinks as games finish. "
                "**Eliminated** = chance of being among the lowest scorers who get cut (immune teams can't be cut). **First** = chance of the week's highest score. "
                "Ties in points are shown as T-ranks.")
