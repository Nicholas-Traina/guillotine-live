"""
Mobile-friendly team cards for the live page (pure functions: no Streamlit import, so they can be tested on their own).

One card per team in a CSS grid: several columns on a desktop, a single column on a phone. Colours use translucent
greys plus a few fixed accents, so the cards read well in both Streamlit's light and dark themes.
Usernames come from Sleeper (user-controlled text), so everything that goes into the HTML is escaped.
"""
import html

CSS = """<style>
div[data-testid="stMainBlockContainer"], .block-container { padding-top: 1.4rem !important; }
.gg { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: .7rem; margin: .25rem 0 1rem; }
.gc { border: 1px solid rgba(128,128,128,.35); border-left: 6px solid rgba(128,128,128,.5); border-radius: 10px;
      padding: .7rem .85rem; background: rgba(128,128,128,.07); }
.gc.hi { border-left-color: #e5484d; } .gc.mid { border-left-color: #f5a524; } .gc.lo { border-left-color: #30a46c; }
.gc.imm { border-left-color: #3b82f6; } .gc.me { box-shadow: 0 0 0 2px rgba(59,130,246,.6); }
.gh { display: flex; align-items: baseline; gap: .45rem; flex-wrap: wrap; margin-bottom: .35rem; }
.gr { font-weight: 700; opacity: .6; font-size: .9rem; }
.gnm { font-weight: 700; font-size: 1.05rem; word-break: break-word; }
.gtag { font-size: .7rem; padding: .05rem .42rem; border-radius: 999px; border: 1px solid rgba(128,128,128,.55); }
.gn { display: flex; justify-content: space-between; gap: .5rem; margin: .2rem 0 .55rem; }
.gn div { display: flex; flex-direction: column; min-width: 0; }
.gn b { font-size: 1.15rem; line-height: 1.15; } .gn small { opacity: .65; font-size: .72rem; }
.gb { display: grid; grid-template-columns: 5rem 1fr 3.4rem; align-items: center; gap: .45rem; margin: .2rem 0; font-size: .82rem; }
.gt { height: .62rem; background: rgba(128,128,128,.28); border-radius: 999px; overflow: hidden; }
.gf { height: 100%; border-radius: 999px; }
.gf.el { background: #e5484d; } .gf.la { background: #f5a524; } .gf.se { background: #f5a524; opacity: .65; } .gf.fi { background: #30a46c; }
.gp { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
@media (max-width: 640px) {
  div[data-testid="stMainBlockContainer"], .block-container { padding: 3.6rem .6rem 3rem !important; }   /* clear Streamlit's fixed top bar */
  h1 { font-size: 1.5rem !important; } h3 { font-size: 1.25rem !important; }
  .gg { grid-template-columns: 1fr; gap: .55rem; }
  .gc { padding: .6rem .7rem; }
}
</style>"""


def _bar(label, pct, cls):
    width = 0.0 if pct <= 0 else max(min(pct, 100.0), 0.8)        # keep tiny non-zero chances visible
    return (f'<div class="gb"><span>{html.escape(label)}</span>'
            f'<div class="gt"><div class="gf {cls}" style="width:{width:.1f}%"></div></div>'
            f'<span class="gp">{pct:.1f}%</span></div>')


def risk_class(p_elim_pct, immune):
    if immune:
        return "imm"
    return "hi" if p_elim_pct >= 15 else ("mid" if p_elim_pct >= 6 else "lo")


def team_card(row, k, show_last, is_me=False):
    """row: one team of the standings table with probabilities as fractions (0-1). Returns an HTML string."""
    pe, pl, p2, pf = (float(row[c]) * 100 for c in ("p_eliminated", "p_last", "p_second_last", "p_first"))
    immune, locked = bool(row["immune"]), bool(row["locked"])
    cls = risk_class(pe, immune) + (" me" if is_me else "")
    tags = ""
    if immune:
        tags += '<span class="gtag">🛡 immune</span>'
    if locked:
        tags += '<span class="gtag">LOCKED</span>'
    name = ("★ " if is_me else "") + str(row["owner"])
    rank = f'{"T" if bool(row.get("rank_tied", False)) else "#"}{int(row["rank_now"])}'      # T = tied (e.g. everyone on 0 before kickoff)
    bars = _bar("Eliminated" if k == 1 else f"Bottom {k}", pe, "el")
    if show_last:
        bars += _bar("Last", pl, "la")
        if k > 1:
            bars += _bar("2nd last", p2, "se")
    bars += _bar("First", pf, "fi")
    return (
        f'<div class="gc {cls}"><div class="gh"><span class="gr">{rank}</span>'
        f'<span class="gnm">{html.escape(name)}</span>{tags}</div>'
        f'<div class="gn"><div><b>{float(row["current"]):.1f}</b><small>points</small></div>'
        f'<div><b>{float(row["expected_final"]):.1f}</b><small>proj. final (±{float(row["sd_remaining"]):.0f})</small></div>'
        f'<div><b>{float(row["progress"]) * 100:.0f}%</b><small>games done</small></div></div>'
        f'{bars}</div>'
    )


def cards_html(standings, k, my_team=""):
    """standings: DataFrame with owner, current, expected_final, sd_remaining, progress, rank_now, p_*, immune, locked."""
    show_last = k > 1 or bool(standings["immune"].any())        # with one cut and no immunity, 'last' equals 'eliminated'
    cards = "".join(team_card(r, k, show_last, is_me=(r["owner"] == my_team)) for _, r in standings.iterrows())
    return CSS + f'<div class="gg">{cards}</div>'
