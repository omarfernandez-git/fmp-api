import os
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE_URL = "https://www.fmpadel.com"
FMP_USER = os.getenv("FMP_USER", "")
FMP_PASS = os.getenv("FMP_PASS", "")
FMP_TEAM = os.getenv("FMP_TEAM", "CERCEDILLA A")
DB_PATH = ROOT / os.getenv("FMP_DB", "data/fmp.db")
HTML_CACHE = ROOT / "data" / "html_cache"


def norm(s: str) -> str:
    """Normaliza un texto para comparar: sin tildes, minúsculas, un solo espacio."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


_ALIASES = [("a la par", "alapar"), ("club deportivo", ""), ("c. d.", ""), ("c.d.", ""), ("csd", ""), ("c. t.", "ct"), ("r. c.", "rc")]
_STOP = {"club", "deportivo", "padel", "pádel", "y", "tenis", "sport", "sports", "center", "centro", "de", "del", "la", "el", "los", "las", "cd", "csd", "indoor", "training", "team"}


def team_key(name: str) -> tuple[str, str | None]:
    """Clave comparable de un nombre de equipo y su letra (A/B/C...).

    'MVPADEL CERCEDILLA A' y 'MV PADEL CERCEDILLA A' -> ('mvcercedilla', 'a')
    'A LA PAR PADEL Y TENIS FUENCARRAL B' y 'FUENCARRAL ALAPAR B' -> ('alaparfuencarral', 'b')
    'CSD PARQUE DE LISBOA A' y 'CLUB DEPORTIVO PARQUE DE LISBOA' -> ('lisboaparque', 'a') / ('lisboaparque', None)
    """
    n = norm(name)
    for a, b in _ALIASES:
        n = n.replace(a, b)
    toks = [t for t in re.split(r"[^a-z0-9]+", n) if t]
    letter = None
    if len(toks) > 1 and len(toks[-1]) == 1 and toks[-1].isalpha():
        letter = toks.pop()
    toks = [t for t in toks if t not in _STOP]
    toks = [re.sub(r"padel|sports?|club", "", t) for t in toks]
    toks = sorted(t for t in toks if t)
    return "".join(toks), letter


def same_team(a: str, b: str) -> bool:
    """Mismo equipo aunque el nombre cambie de una temporada a otra; la letra debe coincidir si ambos la tienen."""
    ka, la = team_key(a)
    kb, lb = team_key(b)
    return bool(ka) and ka == kb and (la is None or lb is None or la == lb)


def team_matches(name: str, pattern: str = FMP_TEAM) -> bool:
    """El equipo seguido: 'CERCEDILLA A' casa con 'MVPADEL CERCEDILLA A', 'MV PADEL CERCEDILLA A' y 'MVPADEL CERCEDILLA'
    (temporadas con un solo equipo), pero no con 'MV PADEL CERCEDILLA B'."""
    if same_team(name, pattern):
        return True
    kn, ln = team_key(name)
    kp, lp = team_key(pattern)
    return bool(kp) and kp in kn and (lp is None or ln is None or ln == lp)
