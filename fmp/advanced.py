"""Estadísticas avanzadas de equipo y de jugadores, calculadas sobre las actas (tabla partido)."""
from collections import Counter, defaultdict

from .config import norm
from .stats import _fecha_key, partidos_jugador_rows, q

MESES = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _mes(fecha):
    if not fecha:
        return "?"
    d, m, y = fecha.split("/")
    return f"{y}-{m} {MESES[int(m)]}"


def _pct(g, n):
    return round(100 * g / n) if n else 0


def _rachas(seq):
    """seq: lista de True/False en orden cronológico -> (mejor racha victorias, peor racha derrotas, racha actual)."""
    best_w = best_l = cur = 0
    cur_sign = None
    for g in seq:
        if g is cur_sign:
            cur += 1
        else:
            cur_sign, cur = g, 1
        if g:
            best_w = max(best_w, cur)
        else:
            best_l = max(best_l, cur)
    actual = (cur if cur_sign else -cur) if seq else 0
    return best_w, best_l, actual


def _set_stats(sets, r):
    """Desglosa sets de un partido (perspectiva propia): tiebreaks, 6-0, primer set, remontada."""
    tb_g = sum(1 for a, b in sets if (a, b) == (7, 6))
    tb_p = sum(1 for a, b in sets if (a, b) == (6, 7))
    rosco_g = sum(1 for a, b in sets if a >= 6 and b == 0)
    rosco_p = sum(1 for a, b in sets if b >= 6 and a == 0)
    primer = None
    if sets:
        primer = sets[0][0] > sets[0][1]
    tres = len(sets) == 3
    remontada = bool(r["gano"] and primer is False)
    desperdicio = bool(r["perdio"] and primer is True)
    return dict(tb_g=tb_g, tb_p=tb_p, rosco_g=rosco_g, rosco_p=rosco_p, primer=primer, tres=tres,
                remontada=remontada, desperdicio=desperdicio)


def _bucket():
    return {"pj": 0, "pg": 0}


def _add(b, gano):
    b["pj"] += 1
    b["pg"] += 1 if gano else 0


def _finish(d):
    out = {}
    for k, v in d.items():
        v = dict(v); v["pct"] = _pct(v["pg"], v["pj"]); out[k] = v
    return out


# =====================================================================
# EQUIPO
# =====================================================================
def equipo_stats(con, equipo_ids, cids=None):
    """Estadísticas del equipo seguido (uno o varios equipo_id = temporadas)."""
    ph = ",".join("?" * len(equipo_ids))
    sql = f"""SELECT * FROM encuentro WHERE detalle_ok=1 AND (local_id IN ({ph}) OR visitante_id IN ({ph}))"""
    args = list(equipo_ids) * 2
    if cids:
        sql += f" AND categoria_id IN ({','.join('?'*len(cids))})"; args += list(cids)
    encs = q(con, sql + " ORDER BY categoria_id, jornada", *args)
    rows = partidos_jugador_rows(con, cids, equipo_ids)
    # partidos únicos (una fila por partido, no por jugador)
    seen = set(); partidos = []
    for r in rows:
        k = (r["encuentro_id"], r["orden"])
        if k in seen:
            continue
        seen.add(k); partidos.append(r)
    partidos.sort(key=lambda r: (r["categoria_id"], r["jornada"], r["orden"]))
    by_enc = defaultdict(list)
    for p in partidos:
        by_enc[p["encuentro_id"]].append(p)

    S = {"pj": 0, "pg": 0, "pp": 0, "casa": _bucket(), "fuera": _bucket(), "marcadores": Counter(),
         "puntos_favor": 0, "puntos_contra": 0, "por_rival": defaultdict(lambda: {"pj": 0, "pg": 0, "pp": 0, "pf": 0, "pc": 0}),
         "por_mes": defaultdict(_bucket), "por_temporada": defaultdict(lambda: {"pj": 0, "pg": 0, "pp": 0, "pf": 0, "pc": 0, "partidos_pj": 0, "partidos_pg": 0}),
         "decisivos": [], "secuencia": [], "encuentros": []}
    for e in encs:
        casa = e["local_id"] in equipo_ids
        pf, pc = (e["res_local"], e["res_visitante"]) if casa else (e["res_visitante"], e["res_local"])
        if pf is None:
            continue
        gano = pf > pc
        rival = e["visitante"] if casa else e["local"]
        S["pj"] += 1; S["pg"] += gano; S["pp"] += (not gano)
        _add(S["casa"] if casa else S["fuera"], gano)
        S["marcadores"][f"{pf}-{pc}"] += 1
        S["puntos_favor"] += pf; S["puntos_contra"] += pc
        r = S["por_rival"][rival]; r["pj"] += 1; r["pg"] += gano; r["pp"] += (not gano); r["pf"] += pf; r["pc"] += pc
        _add(S["por_mes"][_mes(e["fecha"])], gano)
        t = S["por_temporada"][e["categoria_id"]]; t["pj"] += 1; t["pg"] += gano; t["pp"] += (not gano); t["pf"] += pf; t["pc"] += pc
        S["secuencia"].append(gano)
        ps = by_enc.get(e["id"], [])
        if {pf, pc} == {3, 2}:
            # el "punto decisivo": posiciones que ganaron (o perdieron) en un 3-2
            S["decisivos"].append({"encuentro_id": e["id"], "jornada": e["jornada"], "rival": rival, "gano": gano,
                                   "ganadas": [p["orden"] for p in ps if p["gano"]], "fecha": e["fecha"],
                                   "categoria_id": e["categoria_id"]})
        S["encuentros"].append({"id": e["id"], "categoria_id": e["categoria_id"], "jornada": e["jornada"], "fecha": e["fecha"],
                                "rival": rival, "casa": casa, "pf": pf, "pc": pc, "gano": gano})

    # partidos individuales
    P = {"pj": 0, "pg": 0, "sg": 0, "sp": 0, "jg": 0, "jp": 0, "tres": _bucket(), "primer_g": _bucket(), "primer_p": _bucket(),
         "remontadas": 0, "desperdicios": 0, "tb_g": 0, "tb_p": 0, "rosco_g": 0, "rosco_p": 0,
         "favorito": _bucket(), "underdog": _bucket(), "igualado": _bucket(),
         "por_posicion": defaultdict(_bucket), "por_turno": defaultdict(_bucket), "casa": _bucket(), "fuera": _bucket(),
         "por_rival": defaultdict(_bucket), "puntos_propios": [], "puntos_rival": [], "por_temporada": defaultdict(_bucket)}
    apariciones = Counter(); victorias_j = Counter(); parejas = defaultdict(lambda: {"pj": 0, "pg": 0, "sg": 0, "sp": 0})
    for p in partidos:
        if not p["gano"] and not p["perdio"]:
            continue
        g = p["gano"]
        P["pj"] += 1; P["pg"] += g; P["sg"] += p["sg"]; P["sp"] += p["sp"]; P["jg"] += p["jg"]; P["jp"] += p["jp"]
        ss = _set_stats(p["sets"], p)
        if ss["tres"]:
            _add(P["tres"], g)
        if ss["primer"] is True:
            _add(P["primer_g"], g)
        elif ss["primer"] is False:
            _add(P["primer_p"], g)
        P["remontadas"] += ss["remontada"]; P["desperdicios"] += ss["desperdicio"]
        P["tb_g"] += ss["tb_g"]; P["tb_p"] += ss["tb_p"]; P["rosco_g"] += ss["rosco_g"]; P["rosco_p"] += ss["rosco_p"]
        if p["pareja_pts"] is not None and p["rival_pts"] is not None:
            P["puntos_propios"].append(p["pareja_pts"]); P["puntos_rival"].append(p["rival_pts"])
            key = "favorito" if p["pareja_pts"] > p["rival_pts"] else "underdog" if p["pareja_pts"] < p["rival_pts"] else "igualado"
            _add(P[key], g)
        _add(P["por_posicion"][p["orden"]], g); _add(P["por_turno"][p["turno"]], g)
        _add(P["casa"] if p["casa"] else P["fuera"], g)
        _add(P["por_rival"][p["rival_equipo"]], g)
        _add(P["por_temporada"][p["categoria_id"]], g)
        S["por_temporada"][p["categoria_id"]]["partidos_pj"] += 1
        S["por_temporada"][p["categoria_id"]]["partidos_pg"] += g
        for j in (p["jugador"], p["pareja"]):
            if j:
                apariciones[j] += 1; victorias_j[j] += g
        if p["pareja"]:
            k = " / ".join(sorted([p["jugador"], p["pareja"]]))
            parejas[k]["pj"] += 1; parejas[k]["pg"] += g; parejas[k]["sg"] += p["sg"]; parejas[k]["sp"] += p["sp"]

    best_w, best_l, actual = _rachas(S["secuencia"])
    S.update({
        "pct": _pct(S["pg"], S["pj"]),
        "casa": {**S["casa"], "pct": _pct(S["casa"]["pg"], S["casa"]["pj"])},
        "fuera": {**S["fuera"], "pct": _pct(S["fuera"]["pg"], S["fuera"]["pj"])},
        "marcadores": sorted(S["marcadores"].items(), key=lambda x: (-int(x[0][0]), x[0])),
        "por_rival": dict(sorted(S["por_rival"].items(), key=lambda x: (-x[1]["pj"], x[0]))),
        "por_mes": _finish(dict(sorted(S["por_mes"].items()))),
        "por_temporada": dict(S["por_temporada"]),
        "racha_mejor": best_w, "racha_peor": best_l, "racha_actual": actual,
        "forma": "".join("G" if g else "P" for g in S["secuencia"][-8:]),
        "decisivos_g": sum(1 for d in S["decisivos"] if d["gano"]),
        "punto_decisivo_por_posicion": Counter(o for d in S["decisivos"] if d["gano"] for o in d["ganadas"]),
    })
    n = len(P["puntos_propios"])
    P.update({
        "pct": _pct(P["pg"], P["pj"]), "pp": P["pj"] - P["pg"],
        "tres": {**P["tres"], "pct": _pct(P["tres"]["pg"], P["tres"]["pj"])},
        "primer_g": {**P["primer_g"], "pct": _pct(P["primer_g"]["pg"], P["primer_g"]["pj"])},
        "primer_p": {**P["primer_p"], "pct": _pct(P["primer_p"]["pg"], P["primer_p"]["pj"])},
        "favorito": {**P["favorito"], "pct": _pct(P["favorito"]["pg"], P["favorito"]["pj"])},
        "underdog": {**P["underdog"], "pct": _pct(P["underdog"]["pg"], P["underdog"]["pj"])},
        "igualado": {**P["igualado"], "pct": _pct(P["igualado"]["pg"], P["igualado"]["pj"])},
        "casa": {**P["casa"], "pct": _pct(P["casa"]["pg"], P["casa"]["pj"])},
        "fuera": {**P["fuera"], "pct": _pct(P["fuera"]["pg"], P["fuera"]["pj"])},
        "por_posicion": _finish(dict(sorted(P["por_posicion"].items()))),
        "por_turno": _finish(dict(sorted(P["por_turno"].items()))),
        "por_rival": _finish(dict(sorted(P["por_rival"].items(), key=lambda x: -x[1]["pj"]))),
        "por_temporada": _finish(dict(sorted(P["por_temporada"].items()))),
        "media_pts_propios": round(sum(P["puntos_propios"]) / n) if n else None,
        "media_pts_rival": round(sum(P["puntos_rival"]) / n) if n else None,
        "dif_juegos_media": round((P["jg"] - P["jp"]) / P["pj"], 2) if P["pj"] else 0,
    })
    jug = [{"jugador": j, "pj": apariciones[j], "pg": victorias_j[j], "pct": _pct(victorias_j[j], apariciones[j]),
            "participacion": _pct(apariciones[j], P["pj"]) if P["pj"] else 0}
           for j in apariciones]
    jug.sort(key=lambda x: (-x["pj"], -x["pg"]))
    par = [{"pareja": k, **v, "pct": _pct(v["pg"], v["pj"])} for k, v in parejas.items()]
    par.sort(key=lambda x: (-x["pj"], -x["pct"]))
    return {"encuentros": S, "partidos": P, "jugadores": jug, "parejas": par, "n_jugadores": len(apariciones)}


# =====================================================================
# JUGADOR
# =====================================================================
def jugador_stats(con, key, equipo_ids, cids=None):
    """Estadísticas detalladas de un jugador del equipo (todas las temporadas o las indicadas)."""
    rows = [r for r in partidos_jugador_rows(con, cids, equipo_ids) if r["key"] == key]
    rows.sort(key=lambda r: (_fecha_key(r["fecha"]), r["categoria_id"], r["jornada"], r["orden"]))
    rows = [r for r in rows if r["gano"] or r["perdio"]]
    if not rows:
        return None
    J = {"jugador": rows[0]["jugador"], "key": key, "pj": len(rows), "pg": sum(r["gano"] for r in rows)}
    J["pp"] = J["pj"] - J["pg"]; J["pct"] = _pct(J["pg"], J["pj"])
    J["sg"] = sum(r["sg"] for r in rows); J["sp"] = sum(r["sp"] for r in rows)
    J["jg"] = sum(r["jg"] for r in rows); J["jp"] = sum(r["jp"] for r in rows)
    J["pct_sets"] = _pct(J["sg"], J["sg"] + J["sp"]); J["pct_juegos"] = _pct(J["jg"], J["jg"] + J["jp"])
    J["dif_juegos_media"] = round((J["jg"] - J["jp"]) / J["pj"], 2)
    b = {k: _bucket() for k in ("casa", "fuera", "tres", "dos", "primer_g", "primer_p", "favorito", "underdog", "igualado", "decisivo")}
    por = {k: defaultdict(_bucket) for k in ("posicion", "turno", "rival_equipo", "mes", "temporada", "pareja")}
    cnt = Counter()
    hist_pts = []
    rivales_pts = []
    # encuentros 3-2 en los que participó (partido decisivo = cualquiera de los suyos en un 3-2)
    for r in rows:
        g = r["gano"]
        ss = _set_stats(r["sets"], r)
        _add(b["casa"] if r["casa"] else b["fuera"], g)
        _add(b["tres"] if ss["tres"] else b["dos"], g)
        if ss["primer"] is True:
            _add(b["primer_g"], g)
        elif ss["primer"] is False:
            _add(b["primer_p"], g)
        cnt["remontadas"] += ss["remontada"]; cnt["desperdicios"] += ss["desperdicio"]
        cnt["tb_g"] += ss["tb_g"]; cnt["tb_p"] += ss["tb_p"]; cnt["rosco_g"] += ss["rosco_g"]; cnt["rosco_p"] += ss["rosco_p"]
        if r["pareja_pts"] is not None and r["rival_pts"] is not None:
            k = "favorito" if r["pareja_pts"] > r["rival_pts"] else "underdog" if r["pareja_pts"] < r["rival_pts"] else "igualado"
            _add(b[k], g)
            rivales_pts.append(r["rival_pts"])
        casa = r["casa"]
        pf, pc = (r["res_local"], r["res_visitante"]) if casa else (r["res_visitante"], r["res_local"])
        if pf is not None and {pf, pc} == {3, 2}:
            _add(b["decisivo"], g)
        _add(por["posicion"][r["orden"]], g); _add(por["turno"][r["turno"]], g)
        _add(por["rival_equipo"][r["rival_equipo"]], g); _add(por["mes"][_mes(r["fecha"])], g)
        _add(por["temporada"][r["categoria_id"]], g)
        if r["pareja"]:
            _add(por["pareja"][r["pareja"]], g)
        if r["puntos"] is not None:
            hist_pts.append({"fecha": _fecha_key(r["fecha"]), "categoria_id": r["categoria_id"], "jornada": r["jornada"], "puntos": r["puntos"]})
    seq = [r["gano"] for r in rows]
    best_w, best_l, actual = _rachas(seq)
    J.update({k: {**v, "pct": _pct(v["pg"], v["pj"])} for k, v in b.items()})
    J.update({f"por_{k}": _finish(dict(sorted(v.items(), key=(lambda x: -x[1]["pj"]) if k in ("rival_equipo", "pareja") else None)))
              for k, v in por.items()})
    J.update(dict(cnt))
    J["racha_mejor"], J["racha_peor"], J["racha_actual"] = best_w, best_l, actual
    J["forma"] = "".join("G" if g else "P" for g in seq[-10:])
    J["ultimos5"] = _pct(sum(seq[-5:]), min(5, len(seq)))
    J["puntos_hist"] = hist_pts
    J["puntos_ult"] = hist_pts[-1]["puntos"] if hist_pts else None
    J["puntos_max"] = max((h["puntos"] for h in hist_pts), default=None)
    J["puntos_min"] = min((h["puntos"] for h in hist_pts), default=None)
    J["puntos_inicio"] = hist_pts[0]["puntos"] if hist_pts else None
    J["media_pts_rival"] = round(sum(rivales_pts) / len(rivales_pts)) if rivales_pts else None
    J["n_parejas"] = len(por["pareja"])
    mejores = [(k, v) for k, v in J["por_pareja"].items() if v["pj"] >= 3]
    J["mejor_pareja"] = max(mejores, key=lambda x: (x[1]["pct"], x[1]["pj"]))[0] if mejores else None
    J["peor_pareja"] = min(mejores, key=lambda x: (x[1]["pct"], -x[1]["pj"]))[0] if mejores else None
    # posición más habitual
    J["posicion_habitual"] = max(J["por_posicion"].items(), key=lambda x: x[1]["pj"])[0] if J["por_posicion"] else None
    # temporadas: puntos inicio/fin por temporada
    temp = {}
    for h in hist_pts:
        t = temp.setdefault(h["categoria_id"], {"inicio": h["puntos"], "fin": h["puntos"], "max": h["puntos"]})
        t["fin"] = h["puntos"]; t["max"] = max(t["max"], h["puntos"])
    for cid, v in J["por_temporada"].items():
        v.update(temp.get(cid, {}))
    J["partidos"] = rows
    return J


def ranking_jugadores(con, equipo_ids, cids=None, min_pj=1):
    """Tabla comparativa de todos los jugadores del equipo con las métricas avanzadas."""
    keys = sorted({r["key"] for r in partidos_jugador_rows(con, cids, equipo_ids)})
    out = []
    for k in keys:
        j = jugador_stats(con, k, equipo_ids, cids)
        if j and j["pj"] >= min_pj:
            j.pop("partidos", None)
            out.append(j)
    out.sort(key=lambda x: (-(x["puntos_ult"] or 0), -x["pct"], -x["pj"]))
    return out


# =====================================================================
# EXTRAS PARA GRÁFICOS Y COMPARATIVAS
# =====================================================================
def elo_ratings(con, cids=None, k=32, base=1000):
    """Rating Elo de parejas -> jugadores, calculado sobre TODOS los partidos descargados (equipo y rivales).

    Cada partido enfrenta a dos parejas; el rating de la pareja es la media de sus dos jugadores y la
    variación se reparte a partes iguales. Devuelve {key: {jugador, elo, pj, pg, equipo}} ordenado.
    """
    sql = """SELECT p.*, e.fecha, e.jornada, e.categoria_id, e.local AS eq_local, e.visitante AS eq_vis
             FROM partido p JOIN encuentro e ON e.id=p.encuentro_id WHERE e.detalle_ok=1 AND p.ganador IS NOT NULL"""
    args = []
    if cids:
        sql += f" AND e.categoria_id IN ({','.join('?'*len(cids))})"; args = list(cids)
    rows = q(con, sql, *args)
    rows.sort(key=lambda r: (_fecha_key(r["fecha"]), r["categoria_id"], r["jornada"], r["orden"]))
    R = {}

    def get(name, equipo):
        kk = norm(name)
        if kk not in R:
            R[kk] = {"jugador": name, "key": kk, "elo": base, "pj": 0, "pg": 0, "equipo": equipo, "hist": []}
        R[kk]["equipo"] = equipo
        return R[kk]

    for r in rows:
        L = [get(r["local1"], r["eq_local"]), get(r["local2"], r["eq_local"])]
        V = [get(r["vis1"], r["eq_vis"]), get(r["vis2"], r["eq_vis"])]
        if not (r["local1"] and r["local2"] and r["vis1"] and r["vis2"]):
            continue
        rl = (L[0]["elo"] + L[1]["elo"]) / 2; rv = (V[0]["elo"] + V[1]["elo"]) / 2
        el = 1 / (1 + 10 ** ((rv - rl) / 400))
        sl = 1 if r["ganador"] == "local" else 0
        d = k * (sl - el)
        for j in L:
            j["elo"] += d; j["pj"] += 1; j["pg"] += sl; j["hist"].append(round(j["elo"]))
        for j in V:
            j["elo"] -= d; j["pj"] += 1; j["pg"] += 1 - sl; j["hist"].append(round(j["elo"]))
    for j in R.values():
        j["elo"] = round(j["elo"])
    return dict(sorted(R.items(), key=lambda x: -x[1]["elo"]))


def series_equipo(con, equipo_ids, cids=None):
    """Series por jornada para gráficos: victorias acumuladas, puntos F/C por encuentro, marcador."""
    E = equipo_stats(con, equipo_ids, cids)["encuentros"]["encuentros"]
    out = defaultdict(list)
    acc = defaultdict(lambda: {"g": 0, "pf": 0, "pc": 0})
    for e in E:
        a = acc[e["categoria_id"]]
        a["g"] += e["gano"]; a["pf"] += e["pf"]; a["pc"] += e["pc"]
        out[e["categoria_id"]].append({"jornada": e["jornada"], "fecha": e["fecha"], "rival": e["rival"], "casa": e["casa"],
                                       "pf": e["pf"], "pc": e["pc"], "gano": e["gano"], "acum_g": a["g"],
                                       "acum_pf": a["pf"], "acum_pc": a["pc"], "encuentro_id": e["id"]})
    return dict(out)


def puntos_por_jornada(con, equipo_ids, cids=None):
    """Puntos '(N Ptos.)' de cada jugador del equipo en cada jornada -> {jugador: [{cid, jornada, fecha, puntos}]}."""
    rows = partidos_jugador_rows(con, cids, equipo_ids)
    out = defaultdict(list)
    for r in sorted(rows, key=lambda r: (_fecha_key(r["fecha"]), r["categoria_id"], r["jornada"])):
        if r["puntos"] is not None:
            out[r["jugador"]].append({"cid": r["categoria_id"], "jornada": r["jornada"], "fecha": _fecha_key(r["fecha"]), "puntos": r["puntos"]})
    return dict(out)


def distribucion_sets(con, equipo_ids, cids=None):
    """Cuántas veces se ha dado cada marcador de set (perspectiva propia), y por nivel del rival."""
    rows = partidos_jugador_rows(con, cids, equipo_ids)
    seen = set(); sets = Counter(); nivel = defaultdict(_bucket); juegos_dif = Counter()
    for r in rows:
        k = (r["encuentro_id"], r["orden"])
        if k in seen or not (r["gano"] or r["perdio"]):
            continue
        seen.add(k)
        for a, b in r["sets"]:
            sets[f"{a}-{b}"] += 1
        juegos_dif[r["jg"] - r["jp"]] += 1
        if r["pareja_pts"] is not None and r["rival_pts"] is not None:
            d = r["rival_pts"] - r["pareja_pts"]
            b = "rival +300 o más" if d >= 300 else "rival +100..299" if d >= 100 else "igualado (±100)" if d > -100 else "propio +100..299" if d > -300 else "propio +300 o más"
            _add(nivel[b], r["gano"])
    orden = ["rival +300 o más", "rival +100..299", "igualado (±100)", "propio +100..299", "propio +300 o más"]
    return {"sets": dict(sorted(sets.items(), key=lambda x: -x[1])),
            "nivel_rival": {k: {**nivel[k], "pct": _pct(nivel[k]["pg"], nivel[k]["pj"])} for k in orden if k in nivel},
            "dif_juegos": dict(sorted(juegos_dif.items()))}
