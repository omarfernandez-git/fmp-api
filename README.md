# fmp-api · datos y estadísticas de la liga FMP para MV Padel Cercedilla A

Backend en Python (FastAPI + SQLite) que recopila de https://www.fmpadel.com los calendarios, actas,
plantillas y clasificaciones de la liga de veteranos, calcula estadísticas de equipo y jugadores y ofrece
una API JSON con autenticación para la web Angular del repositorio `fmp`.

## Puesta en marcha

```bash
git clone https://github.com/omarfernandez-git/fmp-api.git && cd fmp-api
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # rellena FMP_USER/FMP_PASS (perfil delegado, opcional) y ajusta FMP_CORS
.venv/bin/python -m fmp.scrape team       # descarga todas las temporadas del equipo (tarda unos minutos la primera vez)
.venv/bin/python -m fmp.auth add tu@email # crea el usuario de acceso (muestra una contraseña generada)
./run.sh                                  # API en http://127.0.0.1:8000 (+ web si ../fmp está compilado)
```

Otros comandos:

```bash
python -m fmp.scrape refresh              # vuelve a bajar la temporada actual (nuevas jornadas); también desde la web
python -m fmp.scrape discover             # lista las ligas (idCategoria) que existen en la FMP
python -m fmp.scrape grupo 135 619        # baja un grupo concreto
python -m fmp.scrape rivales 142          # baja los grupos de las 2 temporadas anteriores donde jugaron los rivales actuales (para su Elo)
python -m fmp.private                     # sincroniza plantilla oficial y puntos del simulador (área privada de delegado)
python -m fmp.auth passwd tu@email nueva  # cambia una contraseña · list · disable · enable
```

## Seguridad

- Acceso con email y contraseña; contraseñas con bcrypt, sesiones con JWT (HS256) de 72 h firmadas con
  `FMP_SECRET` (se genera sola en `.env` la primera vez).
- Todos los endpoints salvo `/api/auth/login` exigen `Authorization: Bearer <token>`.
- En producción ponlo detrás de nginx con TLS (`deploy/nginx.conf`) y deja `FMP_CORS` vacío.
- Las credenciales de la FMP y el secreto viven solo en `.env` (ignorado por git).

## Despliegue en apache02 (mv.greensysit.net)

API en `/opt/fmp` (uvicorn en 127.0.0.1:8000, systemd) y web Angular estática en `/var/www/html/fmp` servida por
Apache, que hace de proxy de `/api`. Pasos en el README del repositorio `fmp` (sección "Despliegue") y ficheros en
`deploy/`: `apache-mv.greensysit.net.conf`, `fmp.service`, `cron.txt` y `deploy.sh` (compila y sube por rsync).

Actualizar tras un cambio: `deploy/deploy.sh apache02` desde este repositorio (con `../fmp` al lado).

## Qué se descarga

| Página fmpadel.com | Tabla SQLite | Contenido |
|---|---|---|
| `ligas_calendario.aspx?idCategoria=` | `categoria`, `grupo`, `encuentro` | ligas, divisiones y encuentros por jornada |
| `ligas_detalleResultadoT3.aspx?idResultado=` | `partido` | los 5 partidos de cada encuentro: jugadores, puntos "(N Ptos.)", sets, ganador, turno |
| `ligas_clasificacion.aspx` | `clasificacion` | clasificación de cada grupo |
| `ligas_verEquipos.aspx?idEquipo=` | `equipo`, `plantilla` | ficha del equipo, sede, delegado y plantilla |
| `ligas_gestionEquipos.aspx` (privada) | `plantilla_privada` | plantilla oficial con licencias, fecha de nacimiento y estado |
| `ligas_simulaActaT3.aspx` (privada) | `ranking_oficial` | puntos oficiales de ranking por jugador (cuando la liga está activa) |

Se descargan todos los encuentros y plantillas del grupo (no solo los nuestros) para tener datos de rivales, y con
`scrape rivales` también los grupos anteriores de los rivales de la temporada actual. Los nombres de equipo se comparan de
forma difusa entre temporadas (`config.same_team`: 'A LA PAR PADEL Y TENIS FUENCARRAL B' = 'FUENCARRAL ALAPAR B').
Tablas propias de la app: `usuario`, `disponibilidad`, `alineacion`, `jugador_ajuste`.

Temporadas del equipo (liga Veteranos):

| Temporada | idCategoria | Grupo | idGrupo | idEquipo |
|---|---|---|---|---|
| 2023/24 | 124 | Quinta División B | 534 | 5154 (excluido con `FMP_EXCLUIR_EQUIPOS`: aún no era el equipo A) |
| 2024/25 | 128 | Tercera División C | 575 | 6028 |
| 2025/26 | 135 | Tercera División C | 619 | 6704 |
| 2026/27 | 142 | Tercera División A | 665 | 6977 |

## API

`POST /api/auth/login` · `GET /api/me` · `POST /api/me/password` · `GET /api/temporadas` · `GET /api/dashboard` ·
`GET /api/temporada/{cid}` · `GET /api/encuentro/{id}` · `GET /api/equipo?cid=` · `GET /api/jugadores?cid=` ·
`GET /api/jugador/{key}` · `POST /api/jugador/{key}/ajuste` · `GET /api/rivales/{cid}` ·
`GET /api/alineacion/{cid}/{jornada}?modo=` · `POST …/disponibilidad` · `POST …/guardar` · `POST|GET /api/refresh`

## Estadísticas

- Equipo: encuentros y partidos en casa/fuera, marcadores, rachas y forma, encuentros 3-2 y qué pareja dio el punto
  decisivo, por posición y turno, partidos a 3 sets, ganando/perdiendo el primer set, remontadas, tie-breaks, 6-0,
  como favorito o no según los puntos del acta, contra rivales de más o menos nivel, contra cada rival, por mes,
  participación y parejas, series por jornada para gráficos.
- Jugador: todo lo anterior a nivel individual más evolución de puntos FMP, rating Elo (calculado con todos los
  partidos del grupo), compañeros (mejor y peor), rendimiento contra cada equipo, por temporada y forma reciente.
- Alineaciones: puntos por jugador (oficiales > manual > último acta > plantilla), disponibilidad por jornada y
  propuesta de 5 parejas en 2 turnos (3+2) ordenadas por suma de puntos, que es la regla que valida el simulador
  de la FMP (su JavaScript comprueba exactamente eso).

## Estructura

```
fmp/config.py    .env, normalización de nombres, comparación de equipos
fmp/client.py    sesión HTTP, postbacks ASP.NET, caché de HTML
fmp/parsers.py   parsers de calendario, clasificación, acta y ficha de equipo
fmp/db.py        esquema SQLite
fmp/scrape.py    CLI de descarga
fmp/private.py   área privada (plantilla oficial, puntos del simulador)
fmp/stats.py     consultas básicas
fmp/advanced.py  estadísticas avanzadas, Elo y series
fmp/lineup.py    propuesta de alineaciones
fmp/auth.py      usuarios, bcrypt, JWT y CLI
fmp/api.py       API FastAPI + servidor de la web
data/            base de datos y caché (no se suben a git)
deploy/          systemd, nginx, cron
```
