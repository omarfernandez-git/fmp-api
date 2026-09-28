"""Consultas de estadísticas sobre la base de datos."""
from collections import defaultdict

from .config import norm


def q(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def temporadas(con):
    return q(con, """SELECT c.id, c.nombre, c.temporada, e.id AS equipo_id, e.nombre AS equipo, g.id AS grupo_id, g.nombre AS grupo
                     FROM categoria c JOIN equipo e ON e.categoria_id=c.id AND e.seguido=1
                     LEFT JOIN grupo g ON g.id=e.grupo_id ORDER BY c.id DESC""")


def equipo_seguido(con, cid):
    r = con.execute("SELECT * FROM equipo WHERE categoria_id=? AND seguido=1 ORDER BY id DESC", (cid,)).fetchone()
    return dict(r) if r else None


def clasificacion(con, gid):
    return q(con, "SELECT * FROM clasificacion WHERE grupo_id=? ORDER BY pos", gid)


def encuentros(con, gid, equipo_id=None):
    sql = "SELECT * FROM encuentro WHERE grupo_id=?"
    args = [gid]
    if equipo_id:
        sql += " AND (local_id=? OR visitante_id=?)"; args += [equipo_id, equipo_id]
    return q(con, sql + " ORDER BY jornada, id", *args)


def encuentro(con, eid):
    e = con.execute("SELECT * FROM encuentro WHERE id=?", (eid,)).fetchone()
    if not e:
        return None
    e = dict(e)
    e["partidos"] = q(con, "SELECT * FROM partido WHERE encuentro_id=? ORDER BY orden", eid)
    return e


def plantilla(con, equipo_id):
    return q(con, "SELECT * FROM plantilla WHERE equipo_id=? ORDER BY orden", equipo_id)


# ---------- partidos por jugador ----------
def partidos_jugador_rows(con, cids=None, equipo_ids=None):
    """Devuelve una fila por (partido, jugador) para los equipos indicados."""
    sql = """SELECT p.*, e.id AS encuentro_id, e.categoria_id, e.grupo_id, e.jornada, e.fecha, e.local_id, e.visitante_id,
                    e.local AS eq_local, e.visitante AS eq_vis, e.res_local, e.res_visitante
             FROM partido p JOIN encuentro e ON e.id=p.encuentro_id WHERE e.detalle_ok=1"""
    args = []
    if cids:
        sql += f" AND e.categoria_id IN ({','.join('?'*len(cids))})"; args += list(cids)
    if equipo_ids:
        ph = ",".join("?" * len(equipo_ids))
        sql += f" AND (e.local_id IN ({ph}) OR e.visitante_id IN ({ph}))"; args += list(equipo_ids) * 2
    rows = []
    for p in q(con, sql, *args):
        for side in ("local", "vis"):
            eq_id = p["local_id"] if side == "local" else p["visitante_id"]
            if equipo_ids and eq_id not in equipo_ids:
                continue
            gano = p["ganador"] == ("local" if side == "local" else "visitante")
            perdio = p["ganador"] is not None and not gano
            sets = [(p["s1l"], p["s1v"]), (p["s2l"], p["s2v"]), (p["s3l"], p["s3v"])]
            sets = [s for s in sets if s[0] is not None]
            if side == "vis":
                sets = [(b, a) for a, b in sets]
            sg = sum(1 for a, b in sets if a > b); sp = sum(1 for a, b in sets if b > a)
            jg = sum(a for a, _ in sets); jp = sum(b for _, b in sets)
            for k in ("1", "2"):
                nombre = p[f"{side}{k}"]
                if not nombre:
                    continue
                other = p[f"{side}{'2' if k == '1' else '1'}"]
                riv = f"{p['vis1' if side == 'local' else 'local1']} / {p['vis2' if side == 'local' else 'local2']}"
                rows.append({
                    "jugador": nombre, "key": norm(nombre), "pareja": other, "pareja_key": norm(other or ""),
                    "puntos": p[f"{side}{k}_pts"], "pareja_pts": p["pareja_local_pts"] if side == "local" else p["pareja_vis_pts"],
                    "rival": riv, "rival_pts": p["pareja_vis_pts"] if side == "local" else p["pareja_local_pts"],
                    "rival_equipo": p["eq_vis"] if side == "local" else p["eq_local"],
                    "casa": side == "local", "equipo_id": eq_id,
                    "gano": gano, "perdio": perdio, "sg": sg, "sp": sp, "jg": jg, "jp": jp,
                    "sets": sets, "orden": p["orden"], "turno": p["turno"],
                    "encuentro_id": p["encuentro_id"], "categoria_id": p["categoria_id"], "jornada": p["jornada"],
                    "fecha": p["fecha"], "res_local": p["res_local"], "res_visitante": p["res_visitante"],
                })
    return rows


def _fecha_key(f):
    if not f:
        return ""
    d, m, y = f.split("/")
    return f"{y}-{m}-{d}"


def resumen_jugadores(con, equipo_ids, cids=None):
    """Estadísticas agregadas por jugador (partidos de los equipos indicados)."""
    rows = partidos_jugador_rows(con, cids, equipo_ids)
    agg = {}
    for r in rows:
        a = agg.setdefault(r["key"], {"jugador": r["jugador"], "key": r["key"], "pj": 0, "pg": 0, "pp": 0, "sg": 0, "sp": 0, "jg": 0, "jp": 0,
                                       "casa_pj": 0, "casa_pg": 0, "fuera_pj": 0, "fuera_pg": 0, "puntos_hist": [],
                                       "parejas": defaultdict(lambda: {"pj": 0, "pg": 0}), "temporadas": set(),
                                       "ordenes": defaultdict(lambda: {"pj": 0, "pg": 0})})
        a["pj"] += 1; a["pg"] += r["gano"]; a["pp"] += r["perdio"]
        a["sg"] += r["sg"]; a["sp"] += r["sp"]; a["jg"] += r["jg"]; a["jp"] += r["jp"]
        if r["casa"]:
            a["casa_pj"] += 1; a["casa_pg"] += r["gano"]
        else:
            a["fuera_pj"] += 1; a["fuera_pg"] += r["gano"]
        if r["puntos"] is not None:
            a["puntos_hist"].append((_fecha_key(r["fecha"]), r["categoria_id"], r["jornada"], r["puntos"]))
        if r["pareja"]:
            pk = a["parejas"][r["pareja"]]; pk["pj"] += 1; pk["pg"] += r["gano"]
        a["temporadas"].add(r["categoria_id"])
        o = a["ordenes"][r["orden"]]; o["pj"] += 1; o["pg"] += r["gano"]
    out = []
    for a in agg.values():
        a["puntos_hist"].sort()
        a["puntos_ult"] = a["puntos_hist"][-1][3] if a["puntos_hist"] else None
        a["puntos_max"] = max((h[3] for h in a["puntos_hist"]), default=None)
        a["pct"] = round(100 * a["pg"] / a["pj"]) if a["pj"] else 0
        a["parejas"] = sorted(({"pareja": k, **v, "pct": round(100 * v["pg"] / v["pj"])} for k, v in a["parejas"].items()),
                              key=lambda x: (-x["pj"], -x["pg"]))
        a["ordenes"] = dict(sorted(a["ordenes"].items()))
        a["temporadas"] = sorted(a["temporadas"])
        out.append(a)
    out.sort(key=lambda x: (-(x["puntos_ult"] or 0), -x["pct"], -x["pj"]))
    return out


def parejas(con, equipo_ids, cids=None):
    rows = partidos_jugador_rows(con, cids, equipo_ids)
    seen = set(); agg = {}
    for r in rows:
        if not r["pareja"]:
            continue
        k = tuple(sorted([r["key"], r["pareja_key"]]))
        ident = (r["encuentro_id"], r["orden"])
        if ident in seen:
            continue
        seen.add(ident)
        a = agg.setdefault(k, {"j1": r["jugador"], "j2": r["pareja"], "pj": 0, "pg": 0, "sg": 0, "sp": 0, "jg": 0, "jp": 0})
        a["pj"] += 1; a["pg"] += r["gano"]; a["sg"] += r["sg"]; a["sp"] += r["sp"]; a["jg"] += r["jg"]; a["jp"] += r["jp"]
    out = list(agg.values())
    for a in out:
        a["pct"] = round(100 * a["pg"] / a["pj"]) if a["pj"] else 0
    out.sort(key=lambda x: (-x["pj"], -x["pct"]))
    return out


def historial_jugador(con, key, equipo_ids=None):
    rows = [r for r in partidos_jugador_rows(con, None, equipo_ids) if r["key"] == key]
    rows.sort(key=lambda r: (r["categoria_id"], r["jornada"]))
    return rows


def rivales(con, gid, propio_id):
    """Equipos del grupo con su plantilla y puntos, salvo el propio."""
    eqs = q(con, "SELECT * FROM equipo WHERE grupo_id=? AND id<>? ORDER BY nombre", gid, propio_id)
    for e in eqs:
        e["plantilla"] = plantilla(con, e["id"])
        # puntos de ranking más recientes por jugador (de los detalles de partidos)
        pts = {}
        for r in partidos_jugador_rows(con, None, [e["id"]]):
            if r["puntos"] is not None:
                k = (_fecha_key(r["fecha"]), r["jornada"])
                if r["key"] not in pts or pts[r["key"]][0] < k:
                    pts[r["key"]] = (k, r["puntos"])
        for j in e["plantilla"]:
            j["ranking"] = pts.get(norm(j["nombre_completo"]), (None, None))[1]
        e["ranking_total"] = sum(j["ranking"] or 0 for j in e["plantilla"])
        e["ranking_top10"] = sum(sorted((j["ranking"] or 0 for j in e["plantilla"]), reverse=True)[:10])
    eqs.sort(key=lambda e: -e["ranking_top10"])
    return eqs


def historial_equipo(con, equipo_ids):
    """Resumen por temporada del equipo seguido."""
    out = []
    for t in temporadas(con):
        cl = [c for c in clasificacion(con, t["grupo_id"]) if norm(c["equipo"]).replace(" ", "") == norm(t["equipo"]).replace(" ", "")]
        t["clasificacion"] = cl[0] if cl else None
        t["n_equipos"] = con.execute("SELECT COUNT(*) FROM clasificacion WHERE grupo_id=?", (t["grupo_id"],)).fetchone()[0]
        out.append(t)
    return out
