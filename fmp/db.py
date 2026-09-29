"""Esquema SQLite y helpers."""
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS categoria (
  id INTEGER PRIMARY KEY, nombre TEXT, temporada TEXT, scraped_at TEXT
);
CREATE TABLE IF NOT EXISTS grupo (
  id INTEGER PRIMARY KEY, categoria_id INTEGER REFERENCES categoria(id), nombre TEXT
);
CREATE TABLE IF NOT EXISTS equipo (
  id INTEGER PRIMARY KEY, categoria_id INTEGER, grupo_id INTEGER, nombre TEXT, club TEXT,
  sede TEXT, direccion TEXT, pistas INTEGER, delegado TEXT, delegado_aux TEXT, seguido INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clasificacion (
  grupo_id INTEGER, pos INTEGER, equipo TEXT, equipo_id INTEGER, pj INTEGER, pg INTEGER, pp INTEGER,
  sg INTEGER, sp INTEGER, pts INTEGER, sancion INTEGER, total INTEGER, scraped_at TEXT,
  PRIMARY KEY (grupo_id, equipo)
);
CREATE TABLE IF NOT EXISTS encuentro (
  id INTEGER PRIMARY KEY,           -- idResultado
  categoria_id INTEGER, grupo_id INTEGER, jornada INTEGER, fecha TEXT, club_org TEXT,
  local_id INTEGER, local TEXT, visitante_id INTEGER, visitante TEXT,
  res_local INTEGER, res_visitante INTEGER, tipo_turno TEXT, detalle_ok INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS partido (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  encuentro_id INTEGER REFERENCES encuentro(id), orden INTEGER, turno INTEGER,
  local1 TEXT, local1_pts INTEGER, local2 TEXT, local2_pts INTEGER,
  vis1 TEXT, vis1_pts INTEGER, vis2 TEXT, vis2_pts INTEGER,
  pareja_local_pts INTEGER, pareja_vis_pts INTEGER,
  s1l INTEGER, s1v INTEGER, s2l INTEGER, s2v INTEGER, s3l INTEGER, s3v INTEGER,
  ganador TEXT,
  UNIQUE (encuentro_id, orden)
);
CREATE TABLE IF NOT EXISTS plantilla (
  equipo_id INTEGER REFERENCES equipo(id), jugador_id INTEGER, orden INTEGER,
  nombre TEXT, apellido1 TEXT, apellido2 TEXT, nombre_completo TEXT,
  puntos INTEGER, pj INTEGER, pg INTEGER, pp INTEGER, sg INTEGER, sp INTEGER, color TEXT,
  PRIMARY KEY (equipo_id, nombre_completo)
);
-- Datos propios de la app (no vienen de la web)
CREATE TABLE IF NOT EXISTS disponibilidad (
  categoria_id INTEGER, jornada INTEGER, jugador TEXT, disponible INTEGER, nota TEXT,
  PRIMARY KEY (categoria_id, jornada, jugador)
);
CREATE TABLE IF NOT EXISTS alineacion (
  categoria_id INTEGER, jornada INTEGER, orden INTEGER, turno INTEGER, jugador1 TEXT, jugador2 TEXT,
  PRIMARY KEY (categoria_id, jornada, orden)
);
CREATE TABLE IF NOT EXISTS jugador_ajuste (
  jugador TEXT PRIMARY KEY, puntos_manual INTEGER, activo INTEGER DEFAULT 1, alias TEXT, nota TEXT
);
CREATE TABLE IF NOT EXISTS usuario (
  email TEXT PRIMARY KEY, password_hash TEXT NOT NULL, nombre TEXT, activo INTEGER DEFAULT 1,
  creado TEXT, ultimo_acceso TEXT
);
CREATE INDEX IF NOT EXISTS ix_enc_grupo ON encuentro(grupo_id, jornada);
CREATE INDEX IF NOT EXISTS ix_partido_enc ON partido(encuentro_id);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con):
    """Cambios de esquema sobre bases ya creadas."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(usuario)")}
    if "admin" not in cols:
        con.execute("ALTER TABLE usuario ADD COLUMN admin INTEGER DEFAULT 0")
        con.commit()


@contextmanager
def tx():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()
