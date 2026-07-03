import json
import os
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import psycopg
from flask import jsonify, redirect, request, send_from_directory, session, Flask
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent


def load_env():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

DB_NAME = os.environ.get("DB_NAME", "NigaChu")
PORT = int(os.environ.get("PORT", "3000"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-change-me")
ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


def db_config(database=None):
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "user": os.environ.get("DB_USER", "postgres"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "dbname": database or DB_NAME,
    }


def connect(database=None):
    if DATABASE_URL and database is None:
        return psycopg.connect(DATABASE_URL)
    return psycopg.connect(**db_config(database))


def ensure_database():
    if not DATABASE_URL:
        with connect("postgres") as conn:
            conn.autocommit = True
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (DB_NAME,),
            ).fetchone()
            if not exists:
                safe_name = DB_NAME.replace('"', '""')
                conn.execute(f'CREATE DATABASE "{safe_name}" WITH ENCODING \'UTF8\'')

    sql = (BASE_DIR / "database.sql").read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(sql)


ensure_database()

app = Flask(__name__)
app.secret_key = SESSION_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
)


def seed_admin_user():
    if not ADMIN_USER or not ADMIN_PASSWORD:
        return
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = %s", (ADMIN_USER,)).fetchone()
        if exists:
            conn.execute("UPDATE users SET role = 'admin', active = TRUE WHERE username = %s", (ADMIN_USER,))
            return
        conn.execute(
            """
            INSERT INTO users (username, password_hash, display_name, active)
            VALUES (%s, %s, %s, TRUE)
            """,
            (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD), ADMIN_USER),
        )
        conn.execute("UPDATE users SET role = 'admin' WHERE username = %s", (ADMIN_USER,))


seed_admin_user()


def is_logged_in():
    return bool(session.get("user"))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autorizado"}), 401
            return redirect("/login")
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            return jsonify({"error": "No autorizado"}), 401
        if session.get("user", {}).get("role") != "admin":
            return jsonify({"error": "Solo administradores"}), 403
        return fn(*args, **kwargs)

    return wrapper


def fetch_json(url, headers=None):
    req = Request(url, headers=headers or {"User-Agent": "ElArchivo/1.0"})
    try:
        with urlopen(req, timeout=8) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def tmdb_headers():
    if not TMDB_API_KEY:
        return None
    return {
        "Authorization": f"Bearer {TMDB_API_KEY}",
        "accept": "application/json",
        "User-Agent": "ElArchivo/1.0",
    }


def tmdb_url(path, params=None):
    query = urlencode(params or {})
    return f"https://api.themoviedb.org/3{path}" + (f"?{query}" if query else "")


def tmdb_image(path):
    return f"{TMDB_IMAGE_BASE}{path}" if path else ""


def compact_providers(data):
    if not isinstance(data, dict):
        return []
    results = data.get("results") or {}
    region = results.get("PA") or results.get("US") or {}
    providers = []
    for group in ("flatrate", "rent", "buy"):
        for provider in region.get(group) or []:
            name = provider.get("provider_name")
            if name and name not in providers:
                providers.append(name)
    return providers[:5]


def tvmaze_results(query, category):
    if category not in {"series", "drama"}:
        return []
    search = fetch_json(f"https://api.tvmaze.com/search/shows?q={quote(query)}")
    if not isinstance(search, list):
        return []

    results = []
    for hit in search[:5]:
        show = hit.get("show") or {}
        show_id = show.get("id")
        if not show_id:
            continue
        episodes = fetch_json(f"https://api.tvmaze.com/shows/{show_id}/episodes")
        seasons = {}
        if isinstance(episodes, list):
            for episode in episodes:
                season = episode.get("season")
                number = episode.get("number")
                if season and number and season > 0:
                    seasons[season] = max(seasons.get(season, 0), number)
        season_rows = [
            {"number": number, "total": total}
            for number, total in sorted(seasons.items())
            if total
        ]
        results.append(
            {
                "source": "tvmaze",
                "sourceLabel": "TVmaze",
                "id": show_id,
                "category": category,
                "title": show.get("name") or "",
                "year": (show.get("premiered") or "")[:4],
                "image": ((show.get("image") or {}).get("original") or (show.get("image") or {}).get("medium") or ""),
                "link": show.get("url") or "",
                "summary": show.get("summary") or "",
                "seasons": season_rows,
                "total": sum(row["total"] for row in season_rows) or None,
                "providers": [],
            }
        )
    return results


def tmdb_results(query, category):
    headers = tmdb_headers()
    if not headers or category not in {"series", "pelicula", "drama"}:
        return []

    media = "movie" if category == "pelicula" else "tv"
    search = fetch_json(
        tmdb_url(
            f"/search/{media}",
            {"query": query, "language": "es-ES", "include_adult": "false", "page": 1},
        ),
        headers,
    )
    if not isinstance(search, dict):
        return []

    results = []
    for item in (search.get("results") or [])[:5]:
        media_id = item.get("id")
        if not media_id:
            continue
        details = fetch_json(tmdb_url(f"/{media}/{media_id}", {"language": "es-ES"}), headers) or {}
        providers = fetch_json(tmdb_url(f"/{media}/{media_id}/watch/providers"), headers) or {}
        if media == "movie":
            title = details.get("title") or item.get("title") or ""
            year = (details.get("release_date") or item.get("release_date") or "")[:4]
            seasons = []
            total = None
        else:
            title = details.get("name") or item.get("name") or ""
            year = (details.get("first_air_date") or item.get("first_air_date") or "")[:4]
            seasons = [
                {"number": season.get("season_number"), "total": season.get("episode_count") or 0}
                for season in details.get("seasons") or []
                if season.get("season_number") and season.get("episode_count")
            ]
            total = sum(row["total"] for row in seasons) or None

        results.append(
            {
                "source": "tmdb",
                "sourceLabel": "TMDb",
                "id": media_id,
                "category": category,
                "title": title,
                "year": year,
                "image": tmdb_image(details.get("poster_path") or item.get("poster_path")),
                "link": f"https://www.themoviedb.org/{media}/{media_id}",
                "summary": details.get("overview") or item.get("overview") or "",
                "seasons": seasons,
                "total": total,
                "providers": compact_providers(providers),
            }
        )
    return results


def jikan_results(query, category):
    if category not in {"anime", "lectura"}:
        return []
    kind = "anime" if category == "anime" else "manga"
    search = fetch_json(
        f"https://api.jikan.moe/v4/{kind}?{urlencode({'q': query, 'limit': 5, 'sfw': 'true'})}"
    )
    if not isinstance(search, dict):
        return []

    results = []
    for item in search.get("data") or []:
        total = item.get("episodes") if kind == "anime" else item.get("chapters")
        title = item.get("title_spanish") or item.get("title") or ""
        year = item.get("year") or ((item.get("published") or {}).get("from") or "")[:4]
        results.append(
            {
                "source": "jikan",
                "sourceLabel": "Jikan",
                "id": item.get("mal_id"),
                "category": category,
                "title": title,
                "year": str(year or ""),
                "image": (((item.get("images") or {}).get("jpg") or {}).get("large_image_url") or ""),
                "link": item.get("url") or "",
                "summary": item.get("synopsis") or "",
                "seasons": [{"number": 1, "total": total}] if total else [],
                "total": total,
                "providers": [],
            }
        )
    return results


def localized_text(values):
    if not isinstance(values, dict):
        return ""
    for lang in ("es-la", "es", "en", "ja-ro", "ko-ro", "zh-ro"):
        if values.get(lang):
            return values[lang]
    return next((value for value in values.values() if value), "")


def mangadex_total_chapters(manga_id):
    data = fetch_json(f"https://api.mangadex.org/manga/{manga_id}/aggregate")
    if not isinstance(data, dict):
        return None
    chapters = set()
    for volume in (data.get("volumes") or {}).values():
        for chapter in (volume.get("chapters") or {}).keys():
            if chapter:
                chapters.add(chapter)
    return len(chapters) or None


def mangadex_cover(manga_id, relationships):
    for rel in relationships or []:
        if rel.get("type") != "cover_art":
            continue
        file_name = (rel.get("attributes") or {}).get("fileName")
        if file_name:
            return f"https://uploads.mangadex.org/covers/{manga_id}/{file_name}.512.jpg"
    return ""


def mangadex_results(query, category):
    if category != "lectura":
        return []
    params = [
        ("title", query),
        ("limit", 5),
        ("includes[]", "cover_art"),
        ("contentRating[]", "safe"),
        ("contentRating[]", "suggestive"),
        ("order[relevance]", "desc"),
    ]
    search = fetch_json(f"https://api.mangadex.org/manga?{urlencode(params)}")
    if not isinstance(search, dict):
        return []

    results = []
    for item in search.get("data") or []:
        manga_id = item.get("id")
        attrs = item.get("attributes") or {}
        if not manga_id:
            continue
        total = mangadex_total_chapters(manga_id)
        title = localized_text(attrs.get("title")) or "Sin titulo"
        results.append(
            {
                "source": "mangadex",
                "sourceLabel": "MangaDex",
                "id": manga_id,
                "category": category,
                "title": title,
                "year": str(attrs.get("year") or ""),
                "image": mangadex_cover(manga_id, item.get("relationships")),
                "link": f"https://mangadex.org/title/{manga_id}",
                "summary": localized_text(attrs.get("description")),
                "seasons": [{"number": 1, "total": total}] if total else [],
                "total": total,
                "providers": [],
            }
        )
    return results


@app.get("/api/metadata/search")
@login_required
def metadata_search():
    query = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "series").strip()
    if len(query) < 2:
        return jsonify({"results": [], "message": "Escribe al menos 2 caracteres"})

    results = []
    results.extend(tmdb_results(query, category))
    if not results:
        results.extend(tvmaze_results(query, category))
    if category in {"anime", "lectura"}:
        if category == "lectura":
            results.extend(mangadex_results(query, category))
        results.extend(jikan_results(query, category))

    seen = set()
    unique = []
    for item in results:
        key = (item.get("source"), item.get("id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return jsonify({"results": unique[:6], "hasTmdb": bool(TMDB_API_KEY)})


@app.get("/style.css")
def stylesheet():
    return send_from_directory(BASE_DIR, "style.css")


@app.get("/app.js")
def app_script():
    return send_from_directory(BASE_DIR, "app.js")


@app.get("/login")
def login_page():
    if is_logged_in():
        return redirect("/")
    return send_from_directory(BASE_DIR, "login.html")


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Usuario y contrasena requeridos"}), 400

    with connect() as conn:
        row = conn.execute(
            """
            SELECT username, password_hash, display_name, role
            FROM users
            WHERE username = %s AND active = TRUE
            """,
            (username,),
        ).fetchone()

    if not row or not check_password_hash(row[1], password):
        return jsonify({"error": "Usuario o contrasena incorrectos"}), 401

    session.clear()
    session["user"] = {"username": row[0], "displayName": row[2] or row[0], "role": row[3] or "user"}
    return jsonify({"success": True, "user": session["user"]})


@app.post("/api/logout")
@login_required
def logout():
    session.clear()
    return jsonify({"success": True})


@app.get("/api/session")
def session_state():
    return jsonify({"authenticated": is_logged_in(), "user": session.get("user")})


@app.get("/api/users")
@admin_required
def get_users():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, active, created_at
            FROM users
            ORDER BY active DESC, username ASC
            """
        ).fetchall()
    return jsonify(
        [
            {
                "id": row[0],
                "username": row[1],
                "displayName": row[2] or row[1],
                "role": row[3] or "user",
                "active": row[4],
                "createdAt": row[5].isoformat() if row[5] else None,
            }
            for row in rows
        ]
    )


@app.post("/api/users")
@admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("displayName") or username).strip()
    role = data.get("role") if data.get("role") in {"admin", "user"} else "user"
    if len(username) < 3 or len(password) < 6:
        return jsonify({"error": "Usuario minimo 3 caracteres y contrasena minimo 6"}), 400

    with connect() as conn:
        with conn.transaction():
            exists = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, display_name = %s, role = %s, active = TRUE
                    WHERE username = %s
                    """,
                    (generate_password_hash(password), display_name, role, username),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, display_name, role, active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    """,
                    (username, generate_password_hash(password), display_name, role),
                )
    return jsonify({"success": True})


@app.delete("/api/users/<int:user_id>")
@admin_required
def disable_user(user_id):
    current_username = session.get("user", {}).get("username")
    with connect() as conn:
        row = conn.execute("SELECT username FROM users WHERE id = %s", (user_id,)).fetchone()
        if not row:
            return jsonify({"error": "Usuario no encontrado"}), 404
        if row[0] == current_username:
            return jsonify({"error": "No puedes desactivar tu propio usuario"}), 400
        conn.execute("UPDATE users SET active = FALSE WHERE id = %s", (user_id,))
    return jsonify({"success": True})


@app.get("/")
@login_required
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/catalog")
@login_required
def get_catalog():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, image, link, category, subtype, status, who,
                   seasons, volumes, created_at, updated_at
            FROM catalog
            ORDER BY updated_at DESC
            """
        ).fetchall()

    catalog = []
    for row in rows:
        catalog.append(
            {
                "id": row[0],
                "title": row[1],
                "image": row[2],
                "link": row[3],
                "category": row[4],
                "subtype": row[5],
                "status": row[6],
                "who": row[7],
                "seasons": row[8] or [],
                "volumes": row[9] or [],
                "created_at": row[10].isoformat() if row[10] else None,
                "updated_at": row[11].isoformat() if row[11] else None,
            }
        )
    return jsonify(catalog)


@app.post("/api/catalog")
@login_required
def save_catalog():
    catalog = request.get_json(silent=True)
    if not isinstance(catalog, list):
        return jsonify({"error": "El catalogo debe ser una lista"}), 400

    with connect() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM catalog")
            for item in catalog:
                conn.execute(
                    """
                    INSERT INTO catalog
                      (id, title, image, link, category, subtype, status, who, seasons, volumes, updated_at)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, COALESCE(to_timestamp(%s / 1000.0), CURRENT_TIMESTAMP))
                    """,
                    (
                        item.get("id"),
                        item.get("title"),
                        item.get("image") or None,
                        item.get("link") or None,
                        item.get("category"),
                        item.get("subtype") or None,
                        item.get("status") or "pendiente",
                        item.get("who") or "",
                        json.dumps(item.get("seasons")) if item.get("seasons") is not None else None,
                        json.dumps(item.get("volumes")) if item.get("volumes") is not None else None,
                        item.get("updatedAt"),
                    ),
                )

    return jsonify({"success": True, "message": "Catalogo guardado"})


@app.get("/api/covers")
@login_required
def get_covers():
    with connect() as conn:
        rows = conn.execute("SELECT category, image_url FROM covers").fetchall()
    return jsonify({category: image_url for category, image_url in rows})


@app.post("/api/covers")
@login_required
def save_covers():
    covers = request.get_json(silent=True) or {}
    if not isinstance(covers, dict):
        return jsonify({"error": "Las portadas deben ser un objeto"}), 400

    with connect() as conn:
        with conn.transaction():
            for category, image_url in covers.items():
                conn.execute(
                    """
                    INSERT INTO covers (category, image_url, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (category)
                    DO UPDATE SET image_url = EXCLUDED.image_url, updated_at = CURRENT_TIMESTAMP
                    """,
                    (category, image_url),
                )

    return jsonify({"success": True, "message": "Portadas guardadas"})


if __name__ == "__main__":
    print(f"Conexion a PostgreSQL establecida. Base de datos lista: {DB_NAME}")
    print(f"Servidor corriendo en http://localhost:{PORT}")
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=PORT)
