"""API JSON + servidor de la web Angular. Arranque: uvicorn fmp.api:app --host 0.0.0.0 --port 8000"""
import logging
import os
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import advanced, auth, lineup, stats
from .config import FMP_TEAM, ROOT, norm, same_team
from .db import connect, tx

log = logging.getLogger("fmp.api")
app = FastAPI(title="fmp API", docs_url=None, redoc_url=None, openapi_url=None)
cors = [o.strip() for o in os.getenv("FMP_CORS", "").split(",") if o.strip()]
if cors:
    app.add_middleware(CORSMiddleware, allow_origins=cors, allow_methods=["*"], allow_headers=["*"])
_refresh = {"running": False, "msg": "", "at": None}
DIST = Path(os.getenv("FMP_DIST", str(ROOT.parent / "fmp" / "dist" / "frontend" / "browser")))


# ---------------- auth ----------------
class Login(BaseModel):
    email: str
    password: str


def current_user(request: Request) -> dict:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "No autenticado")
    u = auth.verify_token(h[7:].strip())
    if not u:
        raise HTTPException(401, "Sesión no válida o caducada")
    return u


@app.post("/api/auth/login")
def login(body: Login):
    u = auth.authenticate(body.email, body.password)
    if not u:
        raise HTTPException(401, "Email o contraseña incorrectos")
    return {"token": auth.create_token(u), "user": u}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


class Passwd(BaseModel):
    actual: str
    nueva: str


@app.post("/api/me/password")
def change_password(body: Passwd, user=Depends(current_user)):
    if not auth.authenticate(user["email"], body.actual):
        raise HTTPException(400, "La contraseña actual no es correcta")
    if len(body.nueva) < 8:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 8 caracteres")
    auth.add_user(user["email"], body.nueva, user.get("nombre"))
    return {"ok": True}


# ---------------- administración de usuarios ----------------
def admin_user(user=Depends(current_user)) -> dict:
    if not user.get("admin"):
        raise HTTPException(403, "Solo para administradores")
    return user


class NuevoUsuario(BaseModel):
    email: str
    nombre: str = ""
    password: str | None = None
    admin: bool = False


class CambioUsuario(BaseModel):
    nombre: str | None = None
    activo: bool | None = None
    admin: bool | None = None
    reset_password: bool = False
    password: str | None = None


@app.get("/api/admin/usuarios")
def admin_listar(user=Depends(admin_user)):
    return auth.list_users()


@app.post("/api/admin/usuarios")
def admin_crear(body: NuevoUsuario, user=Depends(admin_user)):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Email no válido")
    con = connect()
    try:
        if con.execute("SELECT 1 FROM usuario WHERE email=?", (email,)).fetchone():
            raise HTTPException(400, "Ya existe un usuario con ese email")
    finally:
        con.close()
    if body.password and len(body.password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
    pw = auth.add_user(email, body.password, body.nombre or None, body.admin)
    return {"email": email, "password": pw, "generada": not body.password}


@app.post("/api/admin/usuarios/{email}")
def admin_cambiar(email: str, body: CambioUsuario, user=Depends(admin_user)):
    email = email.strip().lower()
    res = {"email": email}
    with tx() as con:
        u = con.execute("SELECT * FROM usuario WHERE email=?", (email,)).fetchone()
        if not u:
            raise HTTPException(404, "Usuario no encontrado")
        if email == user["email"] and (body.activo is False or body.admin is False):
            raise HTTPException(400, "No puedes desactivarte ni quitarte el permiso de administrador a ti mismo")
        if body.nombre is not None:
            con.execute("UPDATE usuario SET nombre=? WHERE email=?", (body.nombre, email))
        if body.activo is not None:
            con.execute("UPDATE usuario SET activo=? WHERE email=?", (1 if body.activo else 0, email))
        if body.admin is not None:
            con.execute("UPDATE usuario SET admin=? WHERE email=?", (1 if body.admin else 0, email))
    if body.reset_password or body.password:
        if body.password and len(body.password) < 8:
            raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
        res["password"] = auth.add_user(email, body.password)
    return res


@app.delete("/api/admin/usuarios/{email}")
def admin_borrar(email: str, user=Depends(admin_user)):
    email = email.strip().lower()
    if email == user["email"]:
        raise HTTPException(400, "No puedes borrar tu propio usuario")
    with tx() as con:
        con.execute("DELETE FROM usuario WHERE email=?", (email,))
    return {"ok": True}


# ---------------- helpers ----------------
def _con():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def _temp(con, cid):
    t = next((x for x in stats.temporadas(con) if x["id"] == cid), None)
    if not t:
        raise HTTPException(404, f"No hay datos del equipo para la liga {cid}")
    return t


def _ids(con, cid=None):
    return [t["equipo_id"] for t in stats.temporadas(con) if not cid or t["id"] == cid]


# ---------------- datos ----------------
@app.get("/api/temporadas")
def temporadas(con=Depends(_con), user=Depends(current_user)):
    return {"equipo": FMP_TEAM, "temporadas": stats.temporadas(con), "refresh": _refresh}


@app.get("/api/dashboard")
def dashboard(cid: int | None = None, con=Depends(_con), user=Depends(current_user)):
    temps = stats.temporadas(con)
    if not temps:
        raise HTTPException(404, "Sin datos: ejecuta el scraping")
    t = next((x for x in temps if x["id"] == cid), temps[0])
    eq = stats.equipo_seguido(con, t["id"])
    ids_all = [x["equipo_id"] for x in temps]
    E = advanced.equipo_stats(con, [eq["id"]], [t["id"]])
    # forma de los jugadores: últimos 5 partidos (todas las temporadas)
    ranking = advanced.ranking_jugadores(con, ids_all)
    return {
        "temporada": t, "equipo": eq,
        "clasificacion": stats.clasificacion(con, t["grupo_id"]),
        "encuentros": stats.encuentros(con, t["grupo_id"], eq["id"]),
        "plantilla": lineup.puntos_jugadores(con, t["id"], eq["id"]),
        "historial": stats.historial_equipo(con, None),
        "resumen": {k: v for k, v in E["encuentros"].items() if k in ("pj", "pg", "pp", "pct", "casa", "fuera", "forma", "racha_actual")},
        "partidos": {k: v for k, v in E["partidos"].items() if k in ("pj", "pg", "pct", "por_posicion")},
        "series": advanced.series_equipo(con, ids_all).get(t["id"], []),
        "series_todas": advanced.series_equipo(con, ids_all),
        "jugadores_forma": [{"jugador": j["jugador"], "key": j["key"], "forma": j["forma"], "ultimos5": j["ultimos5"],
                             "pct": j["pct"], "pj": j["pj"], "puntos_ult": j["puntos_ult"], "racha_actual": j["racha_actual"]} for j in ranking],
    }


@app.get("/api/temporada/{cid}")
def temporada(cid: int, con=Depends(_con), user=Depends(current_user)):
    t = _temp(con, cid)
    eq = stats.equipo_seguido(con, cid)
    return {"temporada": t, "equipo": eq, "clasificacion": stats.clasificacion(con, t["grupo_id"]),
            "encuentros": stats.encuentros(con, t["grupo_id"], eq["id"]),
            "todos_encuentros": stats.encuentros(con, t["grupo_id"]),
            "plantilla": stats.plantilla(con, eq["id"]),
            "jugadores": stats.resumen_jugadores(con, [eq["id"]], [cid]),
            "parejas": stats.parejas(con, [eq["id"]], [cid])}


@app.get("/api/encuentro/{eid}")
def encuentro(eid: int, con=Depends(_con), user=Depends(current_user)):
    e = stats.encuentro(con, eid)
    if not e:
        raise HTTPException(404, "Encuentro no encontrado")
    e["propio_local"] = bool(con.execute("SELECT 1 FROM equipo WHERE id=? AND seguido=1", (e["local_id"],)).fetchone())
    e["propio_visitante"] = bool(con.execute("SELECT 1 FROM equipo WHERE id=? AND seguido=1", (e["visitante_id"],)).fetchone())
    return e


@app.get("/api/equipo")
def equipo(cid: int | None = None, con=Depends(_con), user=Depends(current_user)):
    ids = _ids(con, cid)
    cids = [cid] if cid else None
    E = advanced.equipo_stats(con, ids, cids)
    E["series"] = advanced.series_equipo(con, ids, cids)
    E["distribucion"] = advanced.distribucion_sets(con, ids, cids)
    E["puntos_jornada"] = advanced.puntos_por_jornada(con, ids, cids)
    E["temporadas"] = {t["id"]: t for t in stats.temporadas(con)}
    # Elo de los jugadores del equipo (calculado con todos los partidos del grupo, todas las temporadas)
    elo = advanced.elo_ratings(con)
    E["elo"] = [{"jugador": j["jugador"], "key": norm(j["jugador"]), "elo": e["elo"], "hist": e["hist"], "pj": e["pj"], "pg": e["pg"],
                 "max": max(e["hist"]) if e["hist"] else e["elo"], "min": min(e["hist"]) if e["hist"] else e["elo"]}
                for j in E["jugadores"] if (e := elo.get(norm(j["jugador"])))]
    E["elo"].sort(key=lambda x: -x["elo"])
    return E


@app.get("/api/jugadores")
def jugadores(cid: int | None = None, con=Depends(_con), user=Depends(current_user)):
    ids = _ids(con)
    cids = [cid] if cid else None
    elo = advanced.elo_ratings(con)
    js = advanced.ranking_jugadores(con, ids, cids)
    for j in js:
        j["elo"] = elo.get(j["key"], {}).get("elo")
    return {"jugadores": js, "parejas": stats.parejas(con, ids, cids)}


@app.get("/api/jugador/{key}")
def jugador(key: str, con=Depends(_con), user=Depends(current_user)):
    ids = _ids(con)
    j = advanced.jugador_stats(con, key, ids)
    if not j:
        raise HTTPException(404, "Jugador sin partidos en las actas del equipo")
    elo = advanced.elo_ratings(con).get(key)
    aj = con.execute("SELECT * FROM jugador_ajuste WHERE jugador=?", (key,)).fetchone()
    j["elo"] = elo["elo"] if elo else None
    j["elo_hist"] = elo["hist"] if elo else []
    j["ajuste"] = dict(aj) if aj else None
    j["temporadas"] = {t["id"]: t for t in stats.temporadas(con)}
    return j


class Ajuste(BaseModel):
    puntos_manual: int | None = None
    activo: int = 1
    nota: str = ""


@app.post("/api/jugador/{key}/ajuste")
def jugador_ajuste(key: str, body: Ajuste, user=Depends(current_user)):
    with tx() as con:
        con.execute("INSERT OR REPLACE INTO jugador_ajuste(jugador,puntos_manual,activo,nota) VALUES (?,?,?,?)",
                    (key, body.puntos_manual, body.activo, body.nota))
    return {"ok": True}


@app.get("/api/rivales/{cid}")
def rivales(cid: int, con=Depends(_con), user=Depends(current_user)):
    t = _temp(con, cid)
    eq = stats.equipo_seguido(con, cid)
    elo = advanced.elo_ratings(con)
    rv = stats.rivales(con, t["grupo_id"], eq["id"])
    hist_propio = advanced.equipo_stats(con, _ids(con))["encuentros"]["encuentros"]

    def elo_de_equipo(nombre):
        """Jugadores cuyo último equipo en las actas es este (sirve aunque la plantilla no esté publicada)."""
        js = [{"jugador": v["jugador"], "key": k, "elo": v["elo"], "pj": v["pj"], "pg": v["pg"], "hist": v["hist"]}
              for k, v in elo.items() if same_team(v["equipo"], nombre)]
        js.sort(key=lambda x: -x["elo"])
        return js

    def resumen_elo(js):
        top = [j["elo"] for j in js[:8]]
        return {"medio": round(sum(top) / len(top)) if top else None, "max": max((j["elo"] for j in js), default=None),
                "min": min((j["elo"] for j in js), default=None), "n": len(js)}

    for r in rv:
        for j in r["plantilla"]:
            e = elo.get(norm(j["nombre_completo"]))
            j["elo"] = e["elo"] if e else None
            j["elo_pj"] = e["pj"] if e else 0
        r["elo_jugadores"] = elo_de_equipo(r["nombre"])
        # si no hay plantilla, completamos con los jugadores vistos en actas de ese equipo
        r["elo_resumen"] = resumen_elo(r["elo_jugadores"])
        r["elo_medio"] = r["elo_resumen"]["medio"]
        h = [x for x in hist_propio if same_team(x["rival"], r["nombre"])]
        r["h2h"] = {"pj": len(h), "pg": sum(1 for x in h if x["gano"]), "encuentros": h}
    propio = elo_de_equipo(eq["nombre"])
    nombres_grupo = [r["nombre"] for r in rv] + [eq["nombre"]]
    elo_grupo = [v for v in elo.values() if v["pj"] >= 3 and any(same_team(v["equipo"], n) for n in nombres_grupo)]
    return {"temporada": t, "equipo": eq, "rivales": rv,
            "propio": {"nombre": eq["nombre"], "elo_jugadores": propio, "elo_resumen": resumen_elo(propio)},
            "elo_grupo": elo_grupo[:60]}


@app.get("/api/alineacion/{cid}/{jornada}")
def alineacion(cid: int, jornada: int, modo: str = "fuerza", con=Depends(_con), user=Depends(current_user)):
    t = _temp(con, cid)
    eq = stats.equipo_seguido(con, cid)
    jug = lineup.puntos_jugadores(con, cid, eq["id"])
    disp = lineup.disponibilidad(con, cid, jornada)
    elo = advanced.elo_ratings(con)
    for j in jug:
        d = disp.get(j["key"])
        j["disponible"] = bool(d["disponible"]) if d else None
        j["nota"] = d["nota"] if d else ""
        j["elo"] = elo.get(j["key"], {}).get("elo")
    encs = stats.encuentros(con, t["grupo_id"], eq["id"])
    enc = next((e for e in encs if e["jornada"] == jornada), None)
    jornadas = sorted({e["jornada"] for e in stats.encuentros(con, t["grupo_id"])}) or list(range(1, 23))
    guardada = [dict(r) for r in con.execute("SELECT * FROM alineacion WHERE categoria_id=? AND jornada=? ORDER BY orden", (cid, jornada))]
    propuesta, suplentes = lineup.proponer(jug, modo)
    return {"temporada": t, "equipo": eq, "jornada": jornada, "jornadas": jornadas, "jugadores": jug, "encuentro": enc,
            "propuesta": propuesta, "suplentes": suplentes, "modo": modo, "avisos": lineup.validar(propuesta), "guardada": guardada}


class Disponibilidad(BaseModel):
    items: list[dict]  # [{key, disponible: true|false|null, nota}]


@app.post("/api/alineacion/{cid}/{jornada}/disponibilidad")
def set_disponibilidad(cid: int, jornada: int, body: Disponibilidad, user=Depends(current_user)):
    with tx() as con:
        for it in body.items:
            if it.get("disponible") is None:
                con.execute("DELETE FROM disponibilidad WHERE categoria_id=? AND jornada=? AND jugador=?", (cid, jornada, it["key"]))
            else:
                con.execute("INSERT OR REPLACE INTO disponibilidad(categoria_id,jornada,jugador,disponible,nota) VALUES (?,?,?,?,?)",
                            (cid, jornada, it["key"], 1 if it["disponible"] else 0, it.get("nota", "")))
    return {"ok": True}


class Alineacion(BaseModel):
    parejas: list[dict]  # [{orden, turno, jugador1, jugador2}]


@app.post("/api/alineacion/{cid}/{jornada}/guardar")
def guardar_alineacion(cid: int, jornada: int, body: Alineacion, user=Depends(current_user)):
    with tx() as con:
        con.execute("DELETE FROM alineacion WHERE categoria_id=? AND jornada=?", (cid, jornada))
        for p in body.parejas:
            if p.get("jugador1") or p.get("jugador2"):
                con.execute("INSERT INTO alineacion(categoria_id,jornada,orden,turno,jugador1,jugador2) VALUES (?,?,?,?,?,?)",
                            (cid, jornada, p["orden"], p.get("turno") or (1 if p["orden"] <= 3 else 2), p.get("jugador1", ""), p.get("jugador2", "")))
    return {"ok": True}


# ---------------- actualización ----------------
def _do_refresh():
    from datetime import datetime
    from .scrape import main as scrape_main
    _refresh.update(running=True, msg="Actualizando desde fmpadel.com…")
    try:
        scrape_main(["refresh"])
        _refresh["msg"] = "Actualizado"
    except Exception as e:  # noqa: BLE001
        log.exception("refresh"); _refresh["msg"] = f"Error: {e}"
    finally:
        _refresh.update(running=False, at=datetime.now().isoformat(timespec="seconds"))


@app.post("/api/refresh")
def refresh(user=Depends(current_user)):
    if not _refresh["running"]:
        threading.Thread(target=_do_refresh, daemon=True).start()
    return _refresh


@app.get("/api/refresh")
def refresh_status(user=Depends(current_user)):
    return _refresh


@app.exception_handler(HTTPException)
async def http_exc(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ---------------- web Angular ----------------
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
