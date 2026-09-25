"""
Mobile-friendly team cards for the live page (pure functions: no Streamlit import, so they can be tested on their own).

One card per team in a CSS grid: several columns on a desktop, a single column on a phone. Colours use translucent
greys plus a few fixed accents, so the cards read well in both Streamlit's light and dark themes.
Usernames come from Sleeper (user-controlled text), so everything that goes into the HTML is escaped.
"""
import html

CSS = """<style>
div[data-testid="stMainBlockContainer"], .block-container { padding-top: 1.4rem !important; }
/* label the sidebar's double-arrow button: "Settings" when closed, "Hide" when open */
button[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapseButton"] button {
    width: auto !important; height: auto !important; padding: .25rem .7rem .25rem .45rem !important; border-radius: 999px !important;
    border: 1px solid rgba(128,128,128,.45) !important; background: rgba(128,128,128,.14) !important;
    display: inline-flex !important; align-items: center; gap: .2rem; }
button[data-testid="stExpandSidebarButton"]::after { content: "Settings"; font-size: .85rem; font-weight: 600; color: inherit; }
[data-testid="stSidebarCollapseButton"] button::after { content: "Hide"; font-size: .85rem; font-weight: 600; color: inherit; }
/* score banner: stays at the top while the page scrolls (sticky, so it also works inside Streamlit's layout) */
div[data-testid="stElementContainer"]:has(.gbn) { position: sticky; top: 3.75rem; z-index: 990; }
.gbn { display: flex; justify-content: space-around; gap: .6rem; padding: .35rem .6rem; border-radius: 0 0 10px 10px; border: 1px solid rgba(128,128,128,.45); border-top: 0;
       background: rgba(255,255,255,.94); box-shadow: 0 2px 8px rgba(0,0,0,.12); backdrop-filter: blur(6px); }
@media (prefers-color-scheme: dark) { .gbn { background: rgba(14,17,23,.94); } }
.gbn div { display: flex; flex-direction: column; align-items: center; min-width: 0; text-align: center; }
.gbn b { font-size: 1.35rem; line-height: 1.1; font-variant-numeric: tabular-nums; }
.gbn small { opacity: .7; font-size: .68rem; line-height: 1.15; }
.gg { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: .7rem; margin: .25rem 0 1rem; }
.gc { --c: rgba(128,128,128,.5); position: relative; overflow: hidden; border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
      padding: .7rem .85rem .7rem calc(.85rem + 6px); background: rgba(128,128,128,.07); }
.gc::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; background: var(--c); }
/* left-edge colour: red = one of the teams most likely to be cut, dark green = >10% to finish first, orange = >5% elimination risk, light green = the rest */
.gc.zn { --c: #e5484d; } .gc.dg { --c: #15803d; } .gc.or { --c: #f5a524; } .gc.lg { --c: #86d9a3; }
.gc.imm::before { background: linear-gradient(to bottom, #3b82f6 50%, var(--c) 50%); }      /* immune: top half blue, bottom half by the usual rule */
.gc.me { box-shadow: 0 0 0 2px rgba(59,130,246,.6); }
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
  .gc { padding: .6rem .7rem .6rem calc(.7rem + 6px); }
}
</style>"""


def banner_html(lines):
    """Sticky banner with the safe score and the winning score. `lines` is guillotine_live.score_lines(); '' if it isn't available."""
    if not lines or lines.get("winning") is None:
        return ""
    def num(v):
        return "–" if v is None else f"{float(v):.1f}"
    sp, wp = float(lines.get("safe_pct", 95)), float(lines.get("win_pct", 50))
    return (f'<div class="gbn">'
            f'<div title="A score above this beats the elimination cut line in {sp:g}% of simulations"><small>Safe score · {sp:g}%</small><b>{num(lines.get("safe"))}</b></div>'
            f'<div title="A score of this wins the week in {wp:g}% of simulations"><small>Winning score · {wp:g}%</small><b>{num(lines.get("winning"))}</b></div></div>')


def _bar(label, pct, cls):
    width = 0.0 if pct <= 0 else max(min(pct, 100.0), 0.8)        # keep tiny non-zero chances visible
    return (f'<div class="gb"><span>{html.escape(label)}</span>'
            f'<div class="gt"><div class="gf {cls}" style="width:{width:.1f}%"></div></div>'
            f'<span class="gp">{pct:.1f}%</span></div>')


def risk_class(p_elim_pct, p_first_pct=0.0, in_zone=False):
    """Colour of a card's left edge. In an elimination spot now -> red 'zn'; else >10% to finish first -> dark green 'dg';
    else >= 5% elimination risk -> orange 'or'; else light green 'lg'."""
    if in_zone:
        return "zn"
    if p_first_pct > 10:
        return "dg"
    return "or" if p_elim_pct >= 5 else "lg"


def in_elim_zone(standings, k):
    """Bool list, one per team: red-flagged teams. A team that can be cut is red when it is one of the k teams with the highest chance of
    being eliminated. An immune team can't be cut, so it is red when it WOULD be red without immunity: when its chance of finishing in a cut
    position (p_in_elim_spot, which ignores immunity) is among the k highest of all teams."""
    n = len(standings)
    pe = [float(x) for x in standings["p_eliminated"]]
    imm = [bool(i) for i in standings["immune"]]
    pin = [float(x) for x in standings["p_in_elim_spot"]] if "p_in_elim_spot" in standings else [0.0] * n
    k = max(int(k), 0)
    cut = set(sorted((i for i in range(n) if not imm[i]), key=lambda i: -pe[i])[:k])
    would_cut = set(sorted(range(n), key=lambda i: -pin[i])[:k]) if "p_in_elim_spot" in standings else set()
    return [(i in cut) if not imm[i] else (i in would_cut) for i in range(n)]


def team_card(row, k, show_last, is_me=False):
    """row: one team of the standings table with probabilities as fractions (0-1). Returns an HTML string."""
    pe, pl, p2, pf = (float(row[c]) * 100 for c in ("p_eliminated", "p_last", "p_second_last", "p_first"))
    immune, locked = bool(row["immune"]), bool(row["locked"])
    # an immune team can't be eliminated, so its risk colour comes from its chance of finishing in a cut position instead
    risk = float(row["p_in_elim_spot"]) * 100 if immune and "p_in_elim_spot" in row else pe
    cls = risk_class(risk, pf, bool(row.get("in_zone", False))) + (" imm" if immune else "") + (" me" if is_me else "")
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
    standings = standings.assign(in_zone=in_elim_zone(standings, k))
    cards = "".join(team_card(r, k, show_last, is_me=(r["owner"] == my_team)) for _, r in standings.iterrows())
    return CSS + f'<div class="gg">{cards}</div>'
