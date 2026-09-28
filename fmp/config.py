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


def _split_suffix(s: str):
    """'MV PADEL CERCEDILLA B' -> ('mvpadelcercedilla', 'b'); 'MVPADEL CERCEDILLA' -> ('mvpadelcercedilla', None)."""
    m = re.match(r"^(.*?)(?:\s+([a-z]))?$", norm(s))
    return m.group(1).replace(" ", ""), m.group(2)


def team_matches(name: str, pattern: str = FMP_TEAM) -> bool:
    """Compara nombres de equipo ignorando espacios/tildes y tolerando la letra de equipo.

    'CERCEDILLA A' casa con 'MVPADEL CERCEDILLA A', 'MV PADEL CERCEDILLA A' y con 'MVPADEL CERCEDILLA'
    (temporadas con un solo equipo), pero no con 'MV PADEL CERCEDILLA B'.
    """
    nb, nl = _split_suffix(name)
    pb, pl = _split_suffix(pattern)
    return pb in nb and (pl is None or nl is None or nl == pl)
