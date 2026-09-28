"""Parsers de las páginas públicas de ligas de fmpadel.com."""
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

DD_GRUPO = "ctl00$ContentPlaceHolder1$ddGrupo"
DD_JORNADA = "ctl00$ContentPlaceHolder1$ddJornada"


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _qs(href: str, key: str):
    q = parse_qs(urlparse(href).query)
    v = q.get(key) or q.get(key.lower()) or q.get(key[0].lower() + key[1:])
    return int(v[0]) if v else None


def _int(s):
    s = (s or "").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


# ---------- cabecera de liga ----------
def parse_categoria_title(soup: BeautifulSoup) -> str | None:
    h = soup.select_one("form h4")
    return _txt(h) if h else None


def parse_select(soup: BeautifulSoup, name: str) -> list[tuple[str, str, bool]]:
    sel = soup.select_one(f'select[name="{name}"]')
    if not sel:
        return []
    return [(o.get("value", ""), _txt(o), o.has_attr("selected")) for o in sel.select("option")]


# ---------- calendario ----------
def parse_calendario(soup: BeautifulSoup) -> list[dict]:
    """Filas de la tabla de partidos (encuentros) de una jornada."""
    out = []
    table = soup.select_one("table.table")
    if not table:
        return out
    for tr in table.select("tbody tr"):
        tds = tr.select("td")
        if len(tds) < 4:
            continue
        a_loc = tds[0].select_one("a")
        a_vis = tds[3].select_one("a")
        a_det = tr.select_one('a[href*="idResultado"]')
        out.append({
            "local": _txt(tds[0]),
            "local_id": _qs(a_loc["href"], "idEquipo") if a_loc else None,
            "res_local": _int(_txt(tds[1])),
            "res_visitante": _int(_txt(tds[2])),
            "visitante": _txt(tds[3]),
            "visitante_id": _qs(a_vis["href"], "idEquipo") if a_vis else None,
            "resultado_id": _qs(a_det["href"], "idResultado") if a_det else None,
            "fecha": _txt(tds[4]) if len(tds) > 4 and "detalle" not in _txt(tds[4]).lower() else None,
        })
    return out


# ---------- clasificación ----------
def parse_clasificacion(soup: BeautifulSoup) -> list[dict]:
    out = []
    table = soup.select_one("table.table")
    if not table:
        return out
    for tr in table.select("tbody tr"):
        c = [_txt(td) for td in tr.select("td")]
        if len(c) < 10:
            continue
        a = tr.select_one('a[href*="idEquipo"]')
        out.append({
            "pos": _int(c[0]), "equipo": c[1],
            "equipo_id": _qs(a["href"], "idEquipo") if a else None,
            "pj": _int(c[2]), "pg": _int(c[3]), "pp": _int(c[4]),
            "sg": _int(c[5]), "sp": _int(c[6]), "pts": _int(c[7]),
            "sancion": _int(c[8]), "total": _int(c[9]),
        })
    return out


# ---------- cabecera "Etiqueta: valor" ----------
def _labels(soup: BeautifulSoup, labels: list[str]) -> dict:
    """Extrae pares etiqueta/valor del texto del formulario."""
    form = soup.select_one("form") or soup
    lines = [l.strip() for l in form.get_text("\n", strip=True).split("\n")]
    want = {l.lower(): l for l in labels}
    res = {}
    for i, line in enumerate(lines):
        key = line.lower()
        if key in want and want[key] not in res:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            res[want[key]] = "" if nxt.endswith(":") else nxt
    return res


PLAYER_RE = re.compile(r"^(?P<name>.*?)\s*(?:\((?P<pts>-?\d+)\s*Ptos\.?\))?\s*$", re.S)
PTS_RE = re.compile(r"^\s*(-?\d+)\s*Ptos\.?\s*$")


def _player(td) -> dict:
    raw = _txt(td)
    m = PLAYER_RE.match(raw)
    name = re.sub(r"\s+", " ", m.group("name")).strip() if m else raw
    name = re.sub(r"\s*N/A$", "", name).strip()
    return {
        "nombre": name or None,
        "puntos": int(m.group("pts")) if m and m.group("pts") else None,
        "ganador": td.select_one("b") is not None,
    }


def parse_detalle(soup: BeautifulSoup) -> dict:
    """Detalle de un encuentro: cabecera, parejas por turno, sets y total."""
    head = _labels(soup, ["Jornada:", "Fecha:", "Club Org.:", "Equipo Local:",
                          "Equipo Visitante:", "Grupo:", "Categoria:", "Tipo de Turno:"])
    enc = {
        "jornada": _int(head.get("Jornada:")),
        "fecha": head.get("Fecha:") or None,
        "club_org": head.get("Club Org.:") or None,
        "local": head.get("Equipo Local:") or None,
        "visitante": head.get("Equipo Visitante:") or None,
        "grupo": head.get("Grupo:") or None,
        "categoria": head.get("Categoria:") or None,
        "tipo_turno": head.get("Tipo de Turno:") or None,
        "partidos": [],
        "res_local": None, "res_visitante": None,
    }
    orden = 0
    for table in soup.select("table.table"):
        th0 = table.select_one("thead th")
        title = _txt(th0)
        if title.lower().startswith("turno"):
            orden += 1
            rows = table.select("tbody tr")
            if len(rows) < 2:
                continue
            r1, r2 = rows[0].select("td"), rows[1].select("td")
            r3 = rows[2].select("td") if len(rows) > 2 else [None] * 4
            l1, v1 = _player(r1[0]), _player(r1[3])
            l2, v2 = _player(r2[0]), _player(r2[3])
            sets = []
            for r in (r1, r2, r3):
                if r and r[0] is not None:
                    a, b = _int(_txt(r[1])), _int(_txt(r[2]))
                    if a is not None and b is not None:
                        sets.append((a, b))
            m_l = PTS_RE.match(_txt(r3[0])) if r3[0] is not None else None
            m_v = PTS_RE.match(_txt(r3[3])) if r3[3] is not None else None
            gan_local = l1["ganador"] or l2["ganador"]
            gan_vis = v1["ganador"] or v2["ganador"]
            enc["partidos"].append({
                "orden": orden,
                "turno": _int(re.sub(r"\D", "", title)) or 1,
                "local1": l1["nombre"], "local1_pts": l1["puntos"],
                "local2": l2["nombre"], "local2_pts": l2["puntos"],
                "vis1": v1["nombre"], "vis1_pts": v1["puntos"],
                "vis2": v2["nombre"], "vis2_pts": v2["puntos"],
                "pareja_local_pts": int(m_l.group(1)) if m_l else None,
                "pareja_vis_pts": int(m_v.group(1)) if m_v else None,
                "sets": sets,
                "ganador": "local" if gan_local and not gan_vis else "visitante" if gan_vis and not gan_local else None,
            })
        elif title.lower().startswith("equipo local"):
            tds = table.select("tbody tr td")
            if len(tds) >= 4:
                enc["res_local"], enc["res_visitante"] = _int(_txt(tds[1])), _int(_txt(tds[2]))
    return enc


def parse_equipo(soup: BeautifulSoup) -> dict:
    """Ficha de equipo: datos del club/sede y plantilla con estadísticas."""
    head = _labels(soup, ["LIGA:", "CATEGORIA:", "GRUPO:", "NOMBRE DEL EQUIPO:", "NOMBRE DEL CLUB:",
                          "NOMBRE DE SEDE:", "DIRECCION DE SEDE:", "PISTAS DISPONIBLES:",
                          "DELEGADO:", "DELEGADO AUXILIAR:", "TECNICO 1:", "TECNICO 2:", "TECNICO 3:"])
    eq = {
        "nombre": head.get("NOMBRE DEL EQUIPO:") or None,
        "club": head.get("NOMBRE DEL CLUB:") or None,
        "grupo": head.get("GRUPO:") or None,
        "categoria": head.get("CATEGORIA:") or None,
        "sede": head.get("NOMBRE DE SEDE:") or None,
        "direccion": head.get("DIRECCION DE SEDE:") or None,
        "pistas": _int(head.get("PISTAS DISPONIBLES:")),
        "delegado": head.get("DELEGADO:") or None,
        "delegado_aux": head.get("DELEGADO AUXILIAR:") or None,
        "jugadores": [],
    }
    table = soup.select_one("table.table")
    if table:
        for tr in table.select("tbody tr"):
            tds = tr.select("td")
            if len(tds) < 11:
                continue
            a = tr.select_one('a[href*="idJugador"]')
            c = [_txt(td) for td in tds]
            style = tds[0].get("style", "")
            color = re.search(r"#([0-9A-Fa-f]{6})", style)
            eq["jugadores"].append({
                "orden": _int(c[0]),
                "jugador_id": _qs(a["href"], "idJugador") if a else None,
                "nombre": c[2], "apellido1": c[3], "apellido2": c[4],
                "puntos": _int(c[5]), "pj": _int(c[6]), "pg": _int(c[7]), "pp": _int(c[8]),
                "sg": _int(c[9]), "sp": _int(c[10]),
                "color": color.group(1).upper() if color else None,
            })
    return eq
