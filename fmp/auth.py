"""Usuarios y sesiones (JWT HS256 + bcrypt).

CLI:
  python -m fmp.auth add <email> [contraseña]     # crea un usuario (si no se da contraseña, genera una y la muestra)
  python -m fmp.auth passwd <email> [contraseña]  # cambia la contraseña
  python -m fmp.auth list
  python -m fmp.auth disable <email> | enable <email>
"""
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import ROOT
from .db import connect, tx

TOKEN_HOURS = int(os.getenv("FMP_TOKEN_HOURS", "72"))


def secret() -> str:
    """Clave de firma. Si no existe en .env se genera y se persiste."""
    s = os.getenv("FMP_SECRET", "")
    if s:
        return s
    s = secrets.token_urlsafe(48)
    envf = ROOT / ".env"
    txt = envf.read_text() if envf.exists() else ""
    if "FMP_SECRET=" in txt:
        txt = txt.replace("FMP_SECRET=\n", f"FMP_SECRET={s}\n")
    else:
        txt += f"\nFMP_SECRET={s}\n"
    envf.write_text(txt)
    os.environ["FMP_SECRET"] = s
    return s


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode()


def check_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except ValueError:
        return False


def authenticate(email: str, pw: str):
    email = email.strip().lower()
    with tx() as con:
        u = con.execute("SELECT * FROM usuario WHERE email=? AND activo=1", (email,)).fetchone()
        if not u or not check_password(pw, u["password_hash"]):
            return None
        con.execute("UPDATE usuario SET ultimo_acceso=? WHERE email=?", (datetime.now().isoformat(timespec="seconds"), email))
        return {"email": u["email"], "nombre": u["nombre"]}


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user["email"], "nombre": user.get("nombre"), "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)},
                      secret(), algorithm="HS256")


def verify_token(token: str):
    try:
        data = jwt.decode(token, secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    con = connect()
    try:
        u = con.execute("SELECT email, nombre FROM usuario WHERE email=? AND activo=1", (data["sub"],)).fetchone()
        return dict(u) if u else None
    finally:
        con.close()


def add_user(email: str, pw: str | None = None, nombre: str | None = None) -> str:
    email = email.strip().lower()
    pw = pw or secrets.token_urlsafe(12)
    with tx() as con:
        con.execute("""INSERT INTO usuario(email,password_hash,nombre,activo,creado) VALUES (?,?,?,1,?)
                       ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash, activo=1""",
                    (email, hash_password(pw), nombre or email.split("@")[0], datetime.now().isoformat(timespec="seconds")))
    return pw


def main(argv):
    if not argv:
        print(__doc__); return
    cmd, args = argv[0], argv[1:]
    if cmd in ("add", "passwd"):
        pw = add_user(args[0], args[1] if len(args) > 1 else None)
        print(f"Usuario {args[0].lower()} listo. Contraseña: {pw}")
    elif cmd == "list":
        con = connect()
        for u in con.execute("SELECT email,nombre,activo,creado,ultimo_acceso FROM usuario"):
            print(dict(u))
    elif cmd in ("disable", "enable"):
        with tx() as con:
            con.execute("UPDATE usuario SET activo=? WHERE email=?", (1 if cmd == "enable" else 0, args[0].lower()))
        print("ok")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
