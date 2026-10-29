"""Team-name normalisation so odds-feed names match football-data.co.uk / ESPN names."""
from __future__ import annotations

import difflib
import re
from functools import lru_cache

# Normalised short form -> normalised long form.
ALIASES = {
    "man city": "manchester city", "man united": "manchester united", "man utd": "manchester united",
    "nottm forest": "nottingham forest", "notts forest": "nottingham forest",
    "sheffield weds": "sheffield wednesday", "sheff wed": "sheffield wednesday",
    "sheffield utd": "sheffield united", "sheff utd": "sheffield united",
    "qpr": "queens park rangers", "wolves": "wolverhampton wanderers",
    "brighton": "brighton and hove albion", "west brom": "west bromwich albion",
    "spurs": "tottenham hotspur", "tottenham": "tottenham hotspur",
    "newcastle": "newcastle united", "west ham": "west ham united", "leeds": "leeds united",
    "leicester": "leicester city", "norwich": "norwich city", "stoke": "stoke city",
    "swansea": "swansea city", "cardiff": "cardiff city", "hull": "hull city",
    "coventry": "coventry city", "luton": "luton town", "ipswich": "ipswich town",
    "birmingham": "birmingham city", "blackburn": "blackburn rovers", "bolton": "bolton wanderers",
    "oxford": "oxford united", "peterboro": "peterborough united", "peterborough": "peterborough united",
    "mk dons": "milton keynes dons", "plymouth": "plymouth argyle", "preston": "preston north end",
    "derby": "derby county", "charlton": "charlton athletic", "huddersfield": "huddersfield town",
    "rotherham": "rotherham united", "wycombe": "wycombe wanderers", "stockport": "stockport county",
    "cambridge": "cambridge united", "lincoln": "lincoln city", "exeter": "exeter city",
    "shrewsbury": "shrewsbury town", "crawley": "crawley town", "burton": "burton albion",
    "wigan": "wigan athletic", "northampton": "northampton town", "mansfield": "mansfield town",
    "bradford": "bradford city", "doncaster": "doncaster rovers", "grimsby": "grimsby town",
    "carlisle": "carlisle united", "colchester": "colchester united", "crewe": "crewe alexandra",
    "harrogate": "harrogate town", "salford": "salford city", "swindon": "swindon town",
    "tranmere": "tranmere rovers", "accrington": "accrington stanley", "fleetwood": "fleetwood town",
    "forest green": "forest green rovers", "cheltenham": "cheltenham town", "bristol rvs": "bristol rovers",
    "oldham": "oldham athletic", "barnet": "barnet", "york": "york city", "chesterfield": "chesterfield",
    "wimbledon": "afc wimbledon", "dag and red": "dagenham and redbridge",
    "m gladbach": "borussia monchengladbach", "mgladbach": "borussia monchengladbach",
    "dortmund": "borussia dortmund", "leverkusen": "bayer leverkusen", "ein frankfurt": "eintracht frankfurt",
    "fc koln": "koln", "inter": "inter milan", "milan": "ac milan", "ath madrid": "atletico madrid",
    "ath bilbao": "athletic bilbao", "betis": "real betis", "sociedad": "real sociedad",
    "paris sg": "paris saint germain", "psg": "paris saint germain",
    "celtic": "celtic", "rangers": "rangers", "hearts": "heart of midlothian", "hibernian": "hibernian",
}

_DROP = {"fc", "afc", "cf", "sc", "ac", "the", "club", "de", "football"}
_TOKEN_SUBS = {"utd": "united", "st": "saint", "&": "and"}


@lru_cache(maxsize=8192)
def normalise(name: str) -> str:
    s = name.lower().replace("&", " and ").replace("'", "").replace(".", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = " ".join(s.split())
    if s in ALIASES:
        s = ALIASES[s]
    tokens = [_TOKEN_SUBS.get(t, t) for t in s.split()]
    tokens = [t for t in tokens if t not in _DROP] or tokens
    s = " ".join(tokens)
    return ALIASES.get(s, s)


def match_team(name: str, candidates: list[str] | tuple[str, ...]) -> str | None:
    """Return the candidate that refers to the same team as `name`, or None."""
    return _match(name, tuple(sorted(candidates)))


@lru_cache(maxsize=16384)
def _match(name: str, candidates: tuple[str, ...]) -> str | None:
    if not candidates:
        return None
    target = normalise(name)
    norm = {c: normalise(c) for c in candidates}
    for c, n in norm.items():
        if n == target:
            return c
    # Token containment: "Borussia Dortmund" vs "Dortmund".
    t_tokens = set(target.split())
    contained = [c for c, n in norm.items() if set(n.split()) <= t_tokens or t_tokens <= set(n.split())]
    if len(contained) == 1:
        return contained[0]
    pool = contained or list(candidates)
    best = difflib.get_close_matches(target, [norm[c] for c in pool], n=1, cutoff=0.78)
    if best:
        for c in pool:
            if norm[c] == best[0]:
                return c
    return None
