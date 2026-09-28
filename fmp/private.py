"""Área privada de fmpadel.com (perfil DELEGADO): plantilla oficial con licencias y, cuando el simulador
de alineaciones lo ofrece, los puntos oficiales de ranking de cada jugador.

  python -m fmp.private            # sincroniza plantilla privada + puntos oficiales (si hay credenciales en .env)
"""
import logging
import re
import sys
from datetime import datetime

from . import parsers as P
from .client import FMPClient
from .config import BASE_URL, FMP_PASS, FMP_USER, norm
from .db import tx

log = logging.getLogger("fmp.private")
CP = "ctl00$ContentPlaceHolder1$"


def login(client: FMPClient, ret="/ligas_gestionEquipos.aspx") -> bool:
    if not FMP_USER or not FMP_PASS:
        log.info("sin credenciales FMP_USER/FMP_PASS")
        return False
    r = client.s.get(f"{BASE_URL}/login_back.aspx?ReturnUrl={ret}", timeout=60)
    soup = client.soup(r.text)
    d = client.hidden_fields(soup)
    d.update({"__EVENTTARGET": CP + "Button1", "__EVENTARGUMENT": "", CP + "ddPerfil": "CAPITAN",
              CP + "tbUser": FMP_USER, CP + "tbPass": FMP_PASS})
    r2 = client.s.post(r.url, data=d, timeout=60)
    ok = "login_back.aspx" not in r2.url
    log.info("login delegado %s", "OK" if ok else "FALLIDO")
    return ok


def equipos_gestionados(client: FMPClient) -> list[tuple[int, str]]:
    r = client.s.get(f"{BASE_URL}/ligas_gestionEquipos.aspx", timeout=60)
    soup = client.soup(r.text)
    return [(int(v), n) for v, n, _ in P.parse_select(soup, CP + "ddEquipos") if v.isdigit()]


def plantilla_privada(client: FMPClient, equipo_id: int) -> dict:
    url = f"{BASE_URL}/ligas_gestionEquipos.aspx"
    soup = client.soup(client.s.get(url, timeout=60).text)
    html = client.postback(url, soup, CP + "ddEquipos", {CP + "ddEquipos": str(equipo_id)}, use_cache=False)
    sp = client.soup(html)
    form = sp.select_one("form")
    lines = [l.strip() for l in form.get_text("\n", strip=True).split("\n")]
    def after(label):
        return lines[lines.index(label) + 1] if label in lines and lines.index(label) + 1 < len(lines) else None
    info = {"categoria": after("CATEGORIA:"), "grupo": after("GRUPO:"), "email": after("E-MAIL:"), "movil": after("MOVIL:"),
            "delegado": after("DELEGADO:"), "delegado_aux": after("DELEGADO AUXILIAR:"), "jugadores": []}
    table = next((t for t in sp.select("table") if "licencia" in t.get_text().lower()), None)
    if table:
        for tr in table.select("tbody tr"):
            c = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.select("td")]
            if len(c) < 9:
                continue
            info["jugadores"].append({"orden": int(c[0]) if c[0].isdigit() else None, "nombre": c[1], "apellido1": c[2], "apellido2": c[3],
                                      "licencia": c[4], "fecha_nac": c[5], "estado": c[6], "sancionado": c[7], "acepto": c[8]})
    return info


def puntos_simulador(client: FMPClient, equipo_id: int) -> dict:
    """Puntos oficiales por jugador desde el simulador de alineaciones ("Nombre ptos : N"). Vacío si no está disponible."""
    url = f"{BASE_URL}/ligas_simulaActaT3.aspx"
    soup = client.soup(client.s.get(url, timeout=60).text)
    clubs = P.parse_select(soup, CP + "ddClub")
    if not clubs:
        return {}
    html = client.postback(url, soup, CP + "ddClub", {CP + "ddClub": clubs[0][0]}, use_cache=False)
    sp = client.soup(html)
    eqs = P.parse_select(sp, CP + "ddEquipos")
    val = next((v for v, n, _ in eqs if str(equipo_id) in v), eqs[0][0] if eqs else None)
    if not val:
        return {}
    html = client.postback(url, sp, CP + "ddEquipos", {CP + "ddClub": clubs[0][0], CP + "ddEquipos": val}, use_cache=False)
    out = {}
    for v, name, _ in P.parse_select(client.soup(html), CP + "dd_pareja1_A_local"):
        m = re.match(r"(.*?)\s*ptos\s*:\s*(-?\d+)", name)
        if m:
            out[norm(m.group(1))] = {"jugador": m.group(1).strip(), "puntos": int(m.group(2)), "id": v}
    return out


def sync(client: FMPClient | None = None) -> dict:
    client = client or FMPClient(cache=False)
    if not login(client):
        return {"ok": False, "msg": "sin acceso privado"}
    res = {"ok": True, "equipos": []}
    now = datetime.now().isoformat(timespec="seconds")
    with tx() as con:
        for eid, nombre in equipos_gestionados(client):
            info = plantilla_privada(client, eid)
            con.execute("""CREATE TABLE IF NOT EXISTS plantilla_privada (equipo_id INTEGER, orden INTEGER, nombre TEXT, apellido1 TEXT,
                apellido2 TEXT, nombre_completo TEXT, licencia TEXT, fecha_nac TEXT, estado TEXT, sancionado TEXT, acepto TEXT,
                scraped_at TEXT, PRIMARY KEY (equipo_id, licencia))""")
            con.execute("""CREATE TABLE IF NOT EXISTS ranking_oficial (jugador TEXT PRIMARY KEY, nombre TEXT, puntos INTEGER, scraped_at TEXT)""")
            con.execute("DELETE FROM plantilla_privada WHERE equipo_id=?", (eid,))
            for j in info["jugadores"]:
                full = re.sub(r"\s+", " ", " ".join(x for x in (j["nombre"], j["apellido1"], j["apellido2"]) if x)).strip()
                con.execute("INSERT OR REPLACE INTO plantilla_privada VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (eid, j["orden"], j["nombre"], j["apellido1"], j["apellido2"], full, j["licencia"], j["fecha_nac"],
                             j["estado"], j["sancionado"], j["acepto"], now))
                # si la plantilla pública está vacía, la completamos con la privada para que la app tenga jugadores
                if not con.execute("SELECT 1 FROM plantilla WHERE equipo_id=? AND nombre_completo=?", (eid, full)).fetchone():
                    con.execute("""INSERT OR IGNORE INTO plantilla(equipo_id,jugador_id,orden,nombre,apellido1,apellido2,nombre_completo,
                        puntos,pj,pg,pp,sg,sp,color) VALUES (?,?,?,?,?,?,?,0,0,0,0,0,0,'PRIV')""",
                                (eid, None, j["orden"], j["nombre"], j["apellido1"], j["apellido2"], full))
            con.execute("UPDATE equipo SET delegado=COALESCE(?,delegado), delegado_aux=COALESCE(?,delegado_aux) WHERE id=?",
                        (info["delegado"], info["delegado_aux"], eid))
            pts = puntos_simulador(client, eid)
            for k, v in pts.items():
                con.execute("INSERT OR REPLACE INTO ranking_oficial VALUES (?,?,?,?)", (k, v["jugador"], v["puntos"], now))
            res["equipos"].append({"id": eid, "nombre": nombre, "jugadores": len(info["jugadores"]), "puntos_oficiales": len(pts)})
            log.info("equipo %s %s: %d jugadores, %d con puntos oficiales", eid, nombre, len(info["jugadores"]), len(pts))
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(sync())
