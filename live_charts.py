"""
Trend charts for the live page (Altair; no Streamlit import so the chart specs can be tested on their own).

x = hours of live football (the clock only runs while at least one NFL game is on), y = probability in %.
Dotted vertical lines mark breaks (e.g. between Thursday night and Sunday) and are labelled with the Central time play resumed.
"""
import math

import altair as alt
import pandas as pd

import guillotine_live as gl

TITLES = {
    "p_eliminated": ("Elimination probability",
                     "Chance of being cut this week. Immune teams can't be cut, so they stay at 0%."),
    "p_in_elim_spot": ("Last place (finishing in an elimination spot)",
                       "Chance of finishing among the lowest scorers that get cut, whether or not the team is immune."),
    "p_first": ("First place",
                "Chance of having the week's highest score."),
}


def break_frame(breaks):
    """[(football_minutes, resume_epoch)] -> rows for the vertical break markers."""
    return pd.DataFrame({"hours": [m / 60.0 for m, _ in breaks],
                         "label": [gl.format_central(e, seconds=False, day=True) for _, e in breaks]})


def trend_chart(df, breaks=(), plot_height=210):
    """df: guillotine_history.plot_frame output (minutes, team, pct, clock). Returns an Altair chart."""
    d = df.assign(hours=df["minutes"] / 60.0)
    top = float(d["pct"].max()) if len(d) else 0.0
    n_teams = int(d["team"].nunique()) if len(d) else 1
    # Streamlit fits the whole chart (axes + legend) into one height: leave room for the legend rows so the plot area stays tall
    height = plot_height + 26 * math.ceil(n_teams / 2) + 60
    scheme = "tableau10" if n_teams <= 10 else "category20"      # tableau10 has 10 clearly different colours
    line = alt.Chart(d).mark_line(interpolate="monotone", strokeWidth=2.2).encode(
        x=alt.X("hours:Q", title="Hours of live football",
                axis=alt.Axis(tickMinStep=0.5, format=".1f", grid=False)),
        y=alt.Y("pct:Q", title="Chance (%)", scale=alt.Scale(domainMin=0, domainMax=max(top * 1.08, 1.0), nice=True),
                axis=alt.Axis(format=".0f")),
        color=alt.Color("team:N", scale=alt.Scale(scheme=scheme), legend=alt.Legend(orient="bottom", title=None, columns=2)),
        tooltip=[alt.Tooltip("team:N", title="Team"), alt.Tooltip("pct:Q", title="Chance %", format=".1f"),
                 alt.Tooltip("clock:N", title="Time (Central)"), alt.Tooltip("hours:Q", title="Live hours", format=".2f")],
    )
    layers = [line]
    if len(breaks):
        b = break_frame(breaks)
        layers.append(alt.Chart(b).mark_rule(strokeDash=[4, 3], color="gray", opacity=0.7).encode(x="hours:Q", tooltip=[alt.Tooltip("label:N", title="Play resumes")]))
        layers.append(alt.Chart(b).mark_text(align="left", baseline="top", dx=4, dy=2, fontSize=10, color="gray")
                      .encode(x="hours:Q", y=alt.value(0), text="label:N"))
    return alt.layer(*layers).properties(height=height)
