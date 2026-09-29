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
.gbn { display: flex; flex-direction: column; gap: .05rem; padding: .3rem .6rem .35rem; border-radius: 0 0 10px 10px; border: 1px solid rgba(128,128,128,.45); border-top: 0;
       background: rgba(255,255,255,.94); box-shadow: 0 2px 8px rgba(0,0,0,.12); backdrop-filter: blur(6px); }
@media (prefers-color-scheme: dark) { .gbn { background: rgba(14,17,23,.94); } }
.gbn .gbr { display: flex; justify-content: space-around; gap: .5rem; }
.gbn .gbr div { display: flex; flex-direction: column; align-items: center; min-width: 0; text-align: center; }
.gbn b { font-size: 1.25rem; line-height: 1.1; font-variant-numeric: tabular-nums; }
.gbn .gbo { font-size: .75rem; font-weight: 700; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: .85; }
.gbn .gbp { font-size: .82rem; text-align: center; padding: .3rem 0; } .gbn .gbp b { font-size: inherit; }
.gbn small { opacity: .7; font-size: .66rem; line-height: 1.15; white-space: nowrap; }
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
.gf.el { background: #e5484d; } .gf.fi { background: #30a46c; }
.gp { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.gpl { margin-top: .35rem; font-size: .72rem; line-height: 1.4; opacity: .8; }
.gpl b { opacity: 1; font-weight: 700; }
@media (max-width: 640px) {
  div[data-testid="stMainBlockContainer"], .block-container { padding: 3.6rem .6rem 3rem !important; }   /* clear Streamlit's fixed top bar */
  h1 { font-size: 1.5rem !important; } h3 { font-size: 1.25rem !important; }
  .gg { grid-template-columns: 1fr; gap: .55rem; }
  .gc { padding: .6rem .7rem .6rem calc(.7rem + 6px); }
}
</style>"""


def banner_html(lines):
    """Sticky banner for one team: its safe scores (50% and 95%) and its winning score. `lines` is guillotine_live.score_lines(owner=...);
    '' if unavailable. For an immune team the safe scores are the scores that keep its immunity."""
    if not lines or lines.get("winning") is None:
        return ""
    sp, mp, wp = (float(lines.get(x, d)) for x, d in (("safe_pct", 95), ("mid_pct", 50), ("win_pct", 50)))
    who = html.escape(str(lines.get("owner") or ""))
    imm = bool(lines.get("immune"))
    word = "Keeps immunity" if imm else "Safe score"
    what = "keeps your immunity (beats the elimination line)" if imm else "keeps you out of the elimination spots"
    def num(v):
        return "–" if v is None else format(float(v), ".1f")
    def item(label, value, tip):
        return f'<div title="{who}: {tip}"><small>{label}</small><b>{num(value)}</b></div>'
    return (f'<div class="gbn"><div class="gbo">★ {who}{" 🛡" if imm else ""}</div><div class="gbr">'
            + item(f"{word} · {mp:g}%", lines.get("safe_mid"), f"a score above this {what} in {mp:g}% of simulations")
            + item(f"{word} · {sp:g}%", lines.get("safe"), f"a score above this {what} in {sp:g}% of simulations")
            + item(f"Winning score · {wp:g}%", lines["winning"], f"0.1 above the best other team in the median simulation: beats every other team in {wp:g}% of simulations")
            + '</div></div>')


def banner_prompt_html():
    """Shown in place of the banner until a team is picked: the scores depend on which team you are."""
    return '<div class="gbn"><div class="gbp">Pick <b>★ My team</b> to see your safe and winning scores</div></div>'


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
    """Bool list, one per team: red-flagged teams. p_in_elim_spot already equals a non-immune team's real elimination chance and an
    immune team's chance of losing immunity, so both groups are picked the same way: the k teams (within each group) with the
    highest p_in_elim_spot -- a non-immune team is red when it's one of the k real cuts, an immune team is red when it WOULD be
    one of them without immunity."""
    n = len(standings)
    imm = [bool(i) for i in standings["immune"]]
    pin = [float(x) for x in standings["p_in_elim_spot"]]
    k = max(int(k), 0)
    cut = set(sorted((i for i in range(n) if not imm[i]), key=lambda i: -pin[i])[:k])
    would_cut = set(sorted(range(n), key=lambda i: -pin[i])[:k])
    return [(i in cut) if not imm[i] else (i in would_cut) for i in range(n)]


POS_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}


def _player_sort_key(r):
    name = str(r["player"]).rstrip("*").strip()
    last = name.rsplit(" ", 1)[-1].lower() if name else ""
    return (POS_ORDER.get(r["pos"], 99), last)


def _player_label(r):
    name = str(r["player"]).rstrip("*").strip()
    team = r.get("nfl_team") or ""
    return f"{name} ({r['pos']} - {team})" if team else f"{name} ({r['pos']})"


def player_list_html(details, state, label):
    """One '<b>label:</b> Name (POS - TEAM), ...' line for a team's starters in this game state (QB/RB/WR/TE/K/DEF, then last name); '' if none."""
    if details is None or not len(details):
        return ""
    rows = sorted((r for _, r in details.iterrows() if r["state"] == state), key=_player_sort_key)
    if not rows:
        return ""
    names = ", ".join(_player_label(r) for r in rows)
    return f'<div class="gpl"><b>{html.escape(label)}:</b> {html.escape(names)}</div>'


def team_card(row, k, is_me=False, details=None):
    """row: one team of the standings table with probabilities as fractions (0-1). `details` (optional): that team's starter
    table (snap['details'][roster_id]), to list who's yet to play / currently playing. Returns an HTML string."""
    pin, pf = (float(row[c]) * 100 for c in ("p_in_elim_spot", "p_first"))
    immune, locked = bool(row["immune"]), bool(row["locked"])
    cls = risk_class(pin, pf, bool(row.get("in_zone", False))) + (" imm" if immune else "") + (" me" if is_me else "")
    tags = ""
    if immune:
        tags += '<span class="gtag">🛡 immune</span>'
    if locked:
        tags += '<span class="gtag">LOCKED</span>'
    name = ("★ " if is_me else "") + str(row["owner"])
    rank = f'{"T" if bool(row.get("rank_tied", False)) else "#"}{int(row["rank_now"])}'      # T = tied (e.g. everyone on 0 before kickoff)
    bars = _bar("Elim spot", pin, "el") + _bar("First", pf, "fi")
    players = player_list_html(details, "pre", "Yet to play") + player_list_html(details, "in", "In play")
    return (
        f'<div class="gc {cls}"><div class="gh"><span class="gr">{rank}</span>'
        f'<span class="gnm">{html.escape(name)}</span>{tags}</div>'
        f'<div class="gn"><div><b>{float(row["current"]):.1f}</b><small>points</small></div>'
        f'<div><b>{float(row["expected_final"]):.1f}</b><small>proj. final (±{float(row["sd_remaining"]):.0f})</small></div>'
        f'<div><b>{float(row["progress"]) * 100:.0f}%</b><small>games done</small></div></div>'
        f'{bars}{players}</div>'
    )


def cards_html(standings, k, my_team="", details=None):
    """standings: DataFrame with owner, roster_id, current, expected_final, sd_remaining, progress, rank_now, p_*, immune, locked.
    `details` (optional): snap['details'], {roster_id: starter table}, to list each team's yet-to-play / in-play starters."""
    standings = standings.assign(in_zone=in_elim_zone(standings, k))
    cards = "".join(
        team_card(r, k, is_me=(r["owner"] == my_team), details=(details.get(int(r["roster_id"])) if details else None))
        for _, r in standings.iterrows()
    )
    return CSS + f'<div class="gg">{cards}</div>'
