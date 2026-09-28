"""Descarga las ligas de fmpadel.com a SQLite.

Uso:
  python -m fmp.scrape discover            # lista categorías (temporadas) disponibles
  python -m fmp.scrape team [--all]        # temporadas/grupos en los que juega FMP_TEAM y descarga todo el grupo
  python -m fmp.scrape grupo <idCategoria> <idGrupo>   # descarga un grupo concreto
  python -m fmp.scrape refresh             # vuelve a descargar la temporada más reciente (sin caché)
  python -m fmp.scrape equipo <idCategoria> <idEquipo>  # añade/actualiza la ficha de un equipo
"""
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
import re
import sys
from datetime import datetime

from . import parsers as P
from .client import FMPClient
from .config import FMP_TEAM, norm, team_matches
from .db import tx

log = logging.getLogger("fmp.scrape")
NOW = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731


def temporada_de(nombre: str) -> str | None:
    m = re.search(r"(20\d\d)\D{0,3}(20\d\d|\d\d)", nombre or "")
    if not m:
        m2 = re.search(r"20\d\d", nombre or "")
        return m2.group(0) if m2 else None
    a, b = m.group(1), m.group(2)
    b = b if len(b) == 4 else a[:2] + b
    return f"{a}/{b}"


def discover(client: FMPClient, ids=range(95, 200), use_cache=True) -> list[dict]:
    """Recorre idCategoria y devuelve las que existen con sus grupos."""
    found = []
    for cid in ids:
        html = client.get(f"ligas_calendario.aspx?idCategoria={cid}", use_cache=use_cache)
        soup = client.soup(html)
        title = P.parse_categoria_title(soup)
        grupos = P.parse_select(soup, P.DD_GRUPO)
        if not title or not grupos:
            continue
        found.append({"id": cid, "nombre": title, "temporada": temporada_de(title),
                      "grupos": [(int(v), n) for v, n, _ in grupos]})
        log.info("categoria %s: %s (%d grupos)", cid, title, len(grupos))
    return found


def save_categoria(con, cat: dict):
    con.execute("INSERT OR REPLACE INTO categoria(id,nombre,temporada,scraped_at) VALUES (?,?,?,?)",
                (cat["id"], cat["nombre"], cat["temporada"], NOW()))
    for gid, gname in cat["grupos"]:
        con.execute("INSERT OR REPLACE INTO grupo(id,categoria_id,nombre) VALUES (?,?,?)",
                    (gid, cat["id"], gname))


def clasificacion(client: FMPClient, cid: int, gid: int, use_cache=True) -> list[dict]:
    path = f"ligas_clasificacion.aspx?idCategoria={cid}"
    soup = client.soup(client.get(path, use_cache=use_cache))
    html = client.postback(path, soup, P.DD_GRUPO, {P.DD_GRUPO: str(gid)}, use_cache=use_cache)
    return P.parse_clasificacion(client.soup(html))


def calendario(client: FMPClient, cid: int, gid: int, use_cache=True):
    """Genera (jornada, filas) para todas las jornadas del grupo."""
    path = f"ligas_calendario.aspx?idCategoria={cid}"
    soup = client.soup(client.get(path, use_cache=use_cache))
    html = client.postback(path, soup, P.DD_GRUPO, {P.DD_GRUPO: str(gid), P.DD_JORNADA: ""},
                           use_cache=use_cache)
    soup = client.soup(html)
    jornadas = P.parse_select(soup, P.DD_JORNADA)
    for val, _name, selected in jornadas:
        if selected:
            yield int(val), P.parse_calendario(soup)
        else:
            h = client.postback(path, soup, P.DD_JORNADA, {P.DD_GRUPO: str(gid), P.DD_JORNADA: val},
                                use_cache=use_cache)
            yield int(val), P.parse_calendario(client.soup(h))


def scrape_grupo(client: FMPClient, cid: int, gid: int, use_cache=True, con=None):
    """Descarga clasificación, calendario, detalles y plantillas de un grupo."""
    own = con is None
    if own:
        ctx = tx(); con = ctx.__enter__()
    try:
        # asegura categoria/grupo
        soup = client.soup(client.get(f"ligas_calendario.aspx?idCategoria={cid}", use_cache=use_cache))
        title = P.parse_categoria_title(soup)
        grupos = P.parse_select(soup, P.DD_GRUPO)
        save_categoria(con, {"id": cid, "nombre": title, "temporada": temporada_de(title),
                             "grupos": [(int(v), n) for v, n, _ in grupos]})
        gname = next((n for v, n, _ in grupos if int(v) == gid), None)
        log.info("== %s / %s (grupo %s)", title, gname, gid)

        # clasificación
        for row in clasificacion(client, cid, gid, use_cache):
            con.execute("""INSERT OR REPLACE INTO clasificacion
                (grupo_id,pos,equipo,equipo_id,pj,pg,pp,sg,sp,pts,sancion,total,scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (gid, row["pos"], row["equipo"], row["equipo_id"], row["pj"], row["pg"], row["pp"],
                         row["sg"], row["sp"], row["pts"], row["sancion"], row["total"], NOW()))

        # calendario + detalles
        equipo_ids = {}
        n_enc = 0
        for jornada, filas in calendario(client, cid, gid, use_cache):
            for f in filas:
                if f["local_id"]:
                    equipo_ids[f["local_id"]] = f["local"]
                if f["visitante_id"]:
                    equipo_ids[f["visitante_id"]] = f["visitante"]
                if not f["resultado_id"]:
                    continue
                rid = f["resultado_id"]
                jugado = f["res_local"] is not None and f["res_visitante"] is not None
                prev = con.execute("SELECT detalle_ok, res_local FROM encuentro WHERE id=?", (rid,)).fetchone()
                con.execute("""INSERT INTO encuentro(id,categoria_id,grupo_id,jornada,fecha,club_org,local_id,local,
                    visitante_id,visitante,res_local,res_visitante)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET jornada=excluded.jornada, local_id=excluded.local_id,
                    local=excluded.local, visitante_id=excluded.visitante_id, visitante=excluded.visitante,
                    res_local=excluded.res_local, res_visitante=excluded.res_visitante""",
                            (rid, cid, gid, jornada, f["fecha"], None, f["local_id"], f["local"],
                             f["visitante_id"], f["visitante"], f["res_local"], f["res_visitante"]))
                n_enc += 1
                # detalle: siempre si no lo tenemos; si ya lo teníamos completo y no cambió el marcador, saltar
                if prev and prev["detalle_ok"] and prev["res_local"] == f["res_local"] and use_cache:
                    continue
                det_html = client.get(f"ligas_detalleResultadoT3.aspx?idCategoria={cid}&idResultado={rid}",
                                      use_cache=use_cache and jugado)
                det = P.parse_detalle(client.soup(det_html))
                con.execute("""UPDATE encuentro SET fecha=?, club_org=?, tipo_turno=?, detalle_ok=?
                               WHERE id=?""",
                            (det["fecha"], det["club_org"], det["tipo_turno"],
                             1 if det["partidos"] and jugado else 0, rid))
                con.execute("DELETE FROM partido WHERE encuentro_id=?", (rid,))
                for p in det["partidos"]:
                    s = p["sets"] + [(None, None)] * (3 - len(p["sets"]))
                    con.execute("""INSERT INTO partido(encuentro_id,orden,turno,local1,local1_pts,local2,local2_pts,
                        vis1,vis1_pts,vis2,vis2_pts,pareja_local_pts,pareja_vis_pts,s1l,s1v,s2l,s2v,s3l,s3v,ganador)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (rid, p["orden"], p["turno"], p["local1"], p["local1_pts"], p["local2"], p["local2_pts"],
                                 p["vis1"], p["vis1_pts"], p["vis2"], p["vis2_pts"],
                                 p["pareja_local_pts"], p["pareja_vis_pts"],
                                 s[0][0], s[0][1], s[1][0], s[1][1], s[2][0], s[2][1], p["ganador"]))
            log.info("  jornada %s: %d encuentros", jornada, len(filas))
            con.commit()  # commits cortos: la web puede escribir mientras se descarga

        # sin calendario publicado aún: los equipos del grupo se localizan sondeando idEquipo
        if not equipo_ids and gname:
            equipo_ids = probe_equipos(client, con, cid, gname)

        # plantillas de todos los equipos del grupo
        for eid, ename in sorted(equipo_ids.items()):
            scrape_equipo(client, con, cid, gid, eid, use_cache)
        con.commit()
        log.info("  grupo %s: %d encuentros, %d equipos", gid, n_enc, len(equipo_ids))
        return equipo_ids
    finally:
        if own:
            ctx.__exit__(None, None, None)


def probe_equipos(client: FMPClient, con, cid: int, gname: str, span: int = 700, workers: int = 8) -> dict:
    """Sondea ligas_verEquipos.aspx en un rango de idEquipo por encima del mayor conocido.

    Los idEquipo son correlativos por temporada, así que los de la temporada nueva están justo después
    de los de la anterior. Devuelve {idEquipo: nombre} de los equipos cuyo grupo coincide con gname.
    """
    start = (con.execute("SELECT MAX(id) FROM equipo").fetchone()[0] or 6000) + 1
    known = con.execute("SELECT MAX(id) FROM equipo WHERE categoria_id=?", (cid,)).fetchone()[0]
    if known:  # ya hay equipos de esta liga: amplía por encima
        start = min(start, known - span // 2)
    log.info("  sondeando idEquipo %d..%d para localizar %s", start, start + span, gname)

    def probe(eid):
        try:
            html = client.get(f"ligas_verEquipos.aspx?idCategoria={cid}&idEquipo={eid}")
            eq = P.parse_equipo(client.soup(html))
            return eid, eq
        except Exception as e:  # noqa: BLE001
            log.warning("probe %s: %s", eid, e)
            return eid, None

    title = norm(P.parse_categoria_title(client.soup(client.get(f"ligas_calendario.aspx?idCategoria={cid}"))) or "")
    found = {}
    with ThreadPoolExecutor(workers) as ex:
        for eid, eq in ex.map(probe, range(start, start + span)):
            if eq and eq["nombre"] and norm(eq.get("grupo") or "") == norm(gname) and norm(eq.get("categoria") or "") == title:
                found[eid] = eq["nombre"]
    log.info("  localizados %d equipos en %s", len(found), gname)
    return found


def scrape_equipo(client: FMPClient, con, cid: int, gid: int | None, eid: int, use_cache=True):
    html = client.get(f"ligas_verEquipos.aspx?idCategoria={cid}&idEquipo={eid}", use_cache=use_cache)
    eq = P.parse_equipo(client.soup(html))
    if not eq["nombre"]:
        log.warning("equipo %s sin datos", eid)
        return None
    if gid is None:
        r = con.execute("SELECT id FROM grupo WHERE categoria_id=? AND nombre=?", (cid, eq["grupo"])).fetchone()
        gid = r["id"] if r else None
    con.execute("""INSERT INTO equipo(id,categoria_id,grupo_id,nombre,club,sede,direccion,pistas,delegado,delegado_aux,seguido)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET grupo_id=excluded.grupo_id, nombre=excluded.nombre, club=excluded.club,
        sede=excluded.sede, direccion=excluded.direccion, pistas=excluded.pistas, delegado=excluded.delegado,
        delegado_aux=excluded.delegado_aux, seguido=excluded.seguido""",
                (eid, cid, gid, eq["nombre"], eq["club"], eq["sede"], eq["direccion"], eq["pistas"],
                 eq["delegado"], eq["delegado_aux"], 1 if team_matches(eq["nombre"]) else 0))
    con.execute("DELETE FROM plantilla WHERE equipo_id=?", (eid,))
    for j in eq["jugadores"]:
        full = " ".join(x for x in (j["nombre"], j["apellido1"], j["apellido2"]) if x and x != "N/A")
        full = re.sub(r"\s+", " ", full).strip()
        con.execute("""INSERT OR REPLACE INTO plantilla(equipo_id,jugador_id,orden,nombre,apellido1,apellido2,nombre_completo,
            puntos,pj,pg,pp,sg,sp,color) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (eid, j["jugador_id"], j["orden"], j["nombre"], j["apellido1"], j["apellido2"], full,
                     j["puntos"], j["pj"], j["pg"], j["pp"], j["sg"], j["sp"], j["color"]))
    log.info("  equipo %s %s: %d jugadores%s", eid, eq["nombre"], len(eq["jugadores"]),
             "  <-- SEGUIDO" if team_matches(eq["nombre"]) else "")
    return eq


def find_team_groups(client: FMPClient, cats: list[dict], pattern=FMP_TEAM, use_cache=True):
    """Devuelve [(cid, gid, nombre_equipo)] de los grupos donde aparece el equipo en la clasificación."""
    out = []
    for cat in cats:
        for gid, _gname in cat["grupos"]:
            for row in clasificacion(client, cat["id"], gid, use_cache):
                if team_matches(row["equipo"], pattern):
                    out.append((cat["id"], gid, row["equipo"]))
                    log.info("%s | %s | %s", cat["nombre"], _gname, row["equipo"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["discover", "team", "grupo", "refresh", "equipo"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--no-cache", action="store_true", help="ignora la caché de HTML")
    ap.add_argument("--team", default=FMP_TEAM, help="patrón del equipo (por defecto FMP_TEAM)")
    ap.add_argument("--from", dest="from_id", type=int, default=95)
    ap.add_argument("--to", dest="to_id", type=int, default=200)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = FMPClient()
    use_cache = not a.no_cache

    if a.cmd == "discover":
        cats = discover(client, range(a.from_id, a.to_id), use_cache)
        with tx() as con:
            for c in cats:
                save_categoria(con, c)
        for c in cats:
            print(c["id"], c["temporada"], c["nombre"], f"({len(c['grupos'])} grupos)")
        return

    if a.cmd == "equipo":  # añade/actualiza un equipo concreto: equipo <idCategoria> <idEquipo>
        with tx() as con:
            scrape_equipo(client, con, int(a.args[0]), None, int(a.args[1]), use_cache)
        return

    if a.cmd == "grupo":
        cid, gid = int(a.args[0]), int(a.args[1])
        scrape_grupo(client, cid, gid, use_cache)
        return

    if a.cmd in ("team", "refresh"):
        cats = discover(client, range(a.from_id, a.to_id), use_cache=True)
        # sólo ligas de veteranos masculinas (donde juega el equipo); el resto se salta para ir rápido
        cats = [c for c in cats if re.search(r"veteranos", c["nombre"], re.I)
                and not re.search(r"senior|express|veteranas", c["nombre"], re.I)]
        if a.cmd == "refresh":
            cats = cats[-1:]
            use_cache = False
        with tx() as con:
            for c in cats:
                save_categoria(con, c)
        groups = find_team_groups(client, cats, a.team, use_cache=True)
        print("Grupos encontrados:", groups)
        for cid, gid, _ in groups:
            scrape_grupo(client, cid, gid, use_cache)
        # área privada (plantilla oficial y puntos del simulador) si hay credenciales
        try:
            from .private import sync
            log.info("privado: %s", sync())
        except Exception as e:  # noqa: BLE001
            log.warning("sincronización privada fallida: %s", e)


if __name__ == "__main__":
    main(sys.argv[1:])
