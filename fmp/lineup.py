"""Ayuda para las alineaciones: disponibilidad + puntos -> propuesta de parejas.

Regla que se asume (configurable): los encuentros son de 5 parejas en 2 turnos (3+2) y dentro
de cada turno las parejas van ordenadas de mayor a menor suma de puntos.
"""

from .config import norm
from .stats import plantilla, resumen_jugadores

TURNOS = (3, 2)  # parejas por turno


def puntos_jugadores(con, cid, equipo_id):
    """Lista de jugadores de la plantilla actual con su mejor estimación de puntos.

    Prioridad: ajuste manual > último valor '(N Ptos.)' visto en un acta > columna puntos de la plantilla.
    """
    roster = plantilla(con, equipo_id)
    origen_plantilla = None
    if not roster:  # plantilla aún no publicada: usa la última temporada con plantilla del equipo seguido
        for r in con.execute("SELECT id, categoria_id FROM equipo WHERE seguido=1 AND id<>? ORDER BY categoria_id DESC", (equipo_id,)):
            roster = plantilla(con, r["id"])
            if roster:
                origen_plantilla = r["categoria_id"]
                break
    hist = {r["key"]: r for r in resumen_jugadores(con, None)}  # todos los equipos: coge también otras temporadas
    ajustes = {norm(r["jugador"]): dict(r) for r in con.execute("SELECT * FROM jugador_ajuste")}
    try:
        oficial = {r["jugador"]: r["puntos"] for r in con.execute("SELECT jugador, puntos FROM ranking_oficial")}
    except Exception:  # tabla aún no creada
        oficial = {}
    out = []
    for j in roster:
        k = norm(j["nombre_completo"])
        h = hist.get(k)
        aj = ajustes.get(k, {})
        pts_acta = h["puntos_ult"] if h else None
        pts_of = oficial.get(k)
        if aj.get("puntos_manual") is not None:
            pts, origen = aj["puntos_manual"], "manual"
        elif pts_of is not None:
            pts, origen = pts_of, "oficial"
        elif pts_acta is not None:
            pts, origen = pts_acta, "acta"
        else:
            pts, origen = j["puntos"], "plantilla"
        out.append({
            "jugador": j["nombre_completo"], "key": k, "puntos": pts or 0,
            "origen": origen, "puntos_oficial": pts_of,
            "puntos_acta": pts_acta, "puntos_plantilla": j["puntos"],
            "pj": h["pj"] if h else 0, "pg": h["pg"] if h else 0, "pct": h["pct"] if h else 0,
            "activo": aj.get("activo", 1) if aj else 1, "parejas": h["parejas"][:3] if h else [],
            "plantilla_de": origen_plantilla,
        })
    out.sort(key=lambda x: (-x["puntos"], -x["pct"]))
    return out


def disponibilidad(con, cid, jornada):
    return {norm(r["jugador"]): dict(r) for r in
            con.execute("SELECT * FROM disponibilidad WHERE categoria_id=? AND jornada=?", (cid, jornada))}


def proponer(jugadores, modo="fuerza", n_parejas=5):
    """Propone parejas a partir de los jugadores disponibles ordenados por puntos.

    modo 'fuerza': 1-2, 3-4, 5-6 ... (parejas homogéneas, la mejor pareja lo más fuerte posible)
    modo 'equilibrado': 1-10, 2-9 ... (parejas compensadas)
    modo 'quimica': prioriza parejas que ya han jugado juntas con buen porcentaje
    """
    disp = [j for j in jugadores if j.get("disponible") and j.get("activo", 1)]
    disp.sort(key=lambda x: -x["puntos"])
    titulares = disp[: n_parejas * 2]
    parejas = []
    if modo == "equilibrado":
        while len(titulares) >= 2:
            parejas.append((titulares.pop(0), titulares.pop(-1)))
    elif modo == "quimica":
        restantes = list(titulares)
        # empareja por afinidad conocida (más partidos juntos, mejor pct), si no, por fuerza
        while len(restantes) >= 2:
            a = restantes.pop(0)
            mejor = None
            for cand in restantes:
                for p in a.get("parejas", []):
                    if norm(p["pareja"]) == cand["key"] and p["pj"] >= 2 and p["pct"] >= 50:
                        score = p["pj"] * p["pct"]
                        if mejor is None or score > mejor[0]:
                            mejor = (score, cand)
            b = mejor[1] if mejor else restantes[0]
            restantes.remove(b)
            parejas.append((a, b))
    else:
        while len(titulares) >= 2:
            parejas.append((titulares.pop(0), titulares.pop(0)))
    # ordena por suma de puntos y reparte en turnos respetando el orden descendente dentro de cada turno
    parejas.sort(key=lambda p: -(p[0]["puntos"] + p[1]["puntos"]))
    out, i = [], 0
    for turno, n in enumerate(TURNOS, start=1):
        for _ in range(n):
            if i < len(parejas):
                a, b = parejas[i]; i += 1
                out.append({"orden": i, "turno": turno, "j1": a, "j2": b, "suma": a["puntos"] + b["puntos"]})
    suplentes = disp[n_parejas * 2:]
    return out, suplentes


def validar(alineacion):
    """Avisos si el orden no respeta la suma de puntos descendente dentro de cada turno."""
    avisos = []
    por_turno = {}
    for p in alineacion:
        por_turno.setdefault(p["turno"], []).append(p)
    for t, ps in por_turno.items():
        sumas = [p["suma"] for p in ps]
        if sumas != sorted(sumas, reverse=True):
            avisos.append(f"Turno {t}: las parejas no van en orden descendente de puntos ({sumas}).")
    return avisos
