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


def fetch_json(url, headers=None, payload=None):
    data = None
    request_headers = headers or {"User-Agent": "ElArchivo/1.0"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ElArchivo/1.0",
            **request_headers,
        }
    req = Request(url, data=data, headers=request_headers)
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


def clean_html(value):
    if not value:
        return ""
    text = str(value)
    for old, new in (
        ("<br>", "\n"),
        ("<br/>", "\n"),
        ("<br />", "\n"),
        ("</p>", "\n"),
        ("&quot;", '"'),
        ("&#039;", "'"),
        ("&amp;", "&"),
    ):
        text = text.replace(old, new)
    while "<" in text and ">" in text:
        start = text.find("<")
        end = text.find(">", start)
        if end == -1:
            break
        text = text[:start] + text[end + 1 :]
    return " ".join(text.split())


def normalized_title(value):
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def reading_subtype(country):
    return {"KR": "manhwa", "CN": "manhua", "TW": "manhua"}.get(country, "manga")


def source_rank(source):
    return {
        "anilist": 0,
        "tmdb": 1,
        "tvmaze": 2,
        "episodate": 3,
        "mangadex": 4,
        "jikan": 5,
        "wikidata": 6,
    }.get(source, 9)


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


def episodate_seasons(show_ref):
    if not show_ref:
        return [], {}
    details = fetch_json(f"https://www.episodate.com/api/show-details?q={quote(str(show_ref))}")
    tv_show = (details or {}).get("tvShow") if isinstance(details, dict) else {}
    seasons = {}
    for episode in (tv_show or {}).get("episodes") or []:
        try:
            season = int(episode.get("season") or 0)
            number = int(episode.get("episode") or episode.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if season > 0 and number > 0:
            seasons[season] = max(seasons.get(season, 0), number)
    season_rows = [
        {"number": number, "total": total}
        for number, total in sorted(seasons.items())
        if total
    ]
    return season_rows, tv_show or {}


def episodate_link(show):
    permalink = (show or {}).get("permalink") or ""
    if not permalink:
        return (show or {}).get("url") or ""
    if permalink.startswith("http"):
        return permalink
    permalink = permalink.strip("/")
    if permalink.startswith("tv-show/"):
        return f"https://www.episodate.com/{permalink}"
    return f"https://www.episodate.com/tv-show/{permalink}"


def episodate_result_from_show(show, category, source_label="EpisoDate"):
    show_ref = show.get("id") or show.get("permalink")
    seasons, details = episodate_seasons(show_ref)
    merged = {**show, **details}
    title = merged.get("name") or merged.get("title") or ""
    start_date = merged.get("start_date") or merged.get("startDate") or ""
    image = (
        merged.get("image_thumbnail")
        or merged.get("image_thumbnail_path")
        or merged.get("image_path")
        or merged.get("image")
        or ""
    )
    return {
        "source": "episodate",
        "sourceLabel": source_label,
        "id": show_ref,
        "category": category,
        "title": title,
        "year": str(start_date)[:4],
        "image": image,
        "link": episodate_link(merged),
        "summary": clean_html(merged.get("description") or ""),
        "seasons": seasons,
        "total": sum(row["total"] for row in seasons) or None,
        "providers": [],
        "score": 0,
        "format": merged.get("network") or merged.get("country") or "",
        "status": merged.get("status") or "",
    }


def episodate_results(query, category):
    if category not in {"series", "drama"}:
        return []
    data = fetch_json(f"https://www.episodate.com/api/search?{urlencode({'q': query, 'page': 1})}")
    if not isinstance(data, dict):
        return []
    return [
        episodate_result_from_show(show, category)
        for show in (data.get("tv_shows") or [])[:5]
        if show.get("name") or show.get("title")
    ]


def episodate_trending(category):
    if category not in {"series", "drama"}:
        return []
    data = fetch_json("https://www.episodate.com/api/most-popular?page=1")
    if not isinstance(data, dict):
        return []
    return [
        episodate_result_from_show(show, category, "EpisoDate populares")
        for show in (data.get("tv_shows") or [])[:8]
        if show.get("name") or show.get("title")
    ]


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


def tmdb_trending(category):
    headers = tmdb_headers()
    if not headers or category not in {"series", "pelicula", "drama"}:
        return []

    media = "movie" if category == "pelicula" else "tv"
    data = fetch_json(
        tmdb_url(f"/trending/{media}/week", {"language": "es-ES"}),
        headers,
    )
    if not isinstance(data, dict):
        return []

    results = []
    for item in (data.get("results") or [])[:8]:
        media_id = item.get("id")
        if not media_id:
            continue
        details = fetch_json(tmdb_url(f"/{media}/{media_id}", {"language": "es-ES"}), headers) or {}
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
                "sourceLabel": "TMDb tendencias",
                "id": media_id,
                "category": category,
                "title": title,
                "year": year,
                "image": tmdb_image(details.get("poster_path") or item.get("poster_path")),
                "link": f"https://www.themoviedb.org/{media}/{media_id}",
                "summary": details.get("overview") or item.get("overview") or "",
                "seasons": seasons,
                "total": total,
                "providers": [],
                "score": int((item.get("vote_average") or 0) * 10),
            }
        )
    return results


def sparql_text(value):
    return str(value or "").lower().replace("\\", "\\\\").replace('"', '\\"')


def wikidata_bindings(sparql):
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "ElArchivo/1.0 (personal media catalog)",
    }
    data = fetch_json(
        "https://query.wikidata.org/sparql?"
        + urlencode({"query": sparql, "format": "json"}),
        headers,
    )
    return (((data or {}).get("results") or {}).get("bindings") or [])


def binding_value(row, key):
    return ((row.get(key) or {}).get("value") or "").strip()


def title_match_score(title, query):
    title_key = normalized_title(title)
    query_key = normalized_title(query)
    if not title_key or not query_key:
        return 0
    if title_key == query_key:
        return 100
    if title_key == f"the{query_key}":
        return 98
    if title_key.startswith(query_key):
        return 90
    if title_key.startswith(f"the{query_key}"):
        return 88
    if query_key in title_key:
        return max(20, 80 - (len(title_key) - len(query_key)))
    return 0


def wikidata_claim_values(entity, prop):
    values = []
    for claim in ((entity.get("claims") or {}).get(prop) or []):
        value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
        if isinstance(value, dict):
            values.append(value.get("id") or value.get("time") or "")
        elif value:
            values.append(str(value))
    return [value for value in values if value]


def wikidata_entity_label(entity, fallback=""):
    labels = entity.get("labels") or {}
    return ((labels.get("es") or {}).get("value") or (labels.get("en") or {}).get("value") or fallback)


def wikidata_image_url(filename):
    if not filename:
        return ""
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}"


def wikidata_movie_result(row, source_label="Wikidata", query=""):
    item_url = binding_value(row, "item")
    qid = item_url.rsplit("/", 1)[-1] if item_url else binding_value(row, "qid")
    imdb = binding_value(row, "imdb")
    date = binding_value(row, "date")
    title = binding_value(row, "label")
    return {
        "source": "wikidata",
        "sourceLabel": source_label,
        "id": qid,
        "category": "pelicula",
        "title": title,
        "year": date[:4],
        "image": binding_value(row, "image"),
        "link": f"https://www.imdb.com/title/{imdb}/" if imdb else item_url,
        "summary": "",
        "seasons": [],
        "total": None,
        "providers": [],
        "score": title_match_score(title, query),
        "format": "film",
    }


def wikidata_movie_results(query):
    hits = []
    seen = set()
    for language in ("es", "en"):
        data = fetch_json(
            "https://www.wikidata.org/w/api.php?"
            + urlencode(
                {
                    "action": "wbsearchentities",
                    "format": "json",
                    "language": language,
                    "uselang": language,
                    "type": "item",
                    "limit": 12,
                    "search": query,
                }
            )
        )
        for hit in (data or {}).get("search") or []:
            qid = hit.get("id")
            if qid and qid not in seen:
                seen.add(qid)
                hits.append(hit)

    ids = [hit["id"] for hit in hits if hit.get("id")]
    if not ids:
        return []

    data = fetch_json(
        "https://www.wikidata.org/w/api.php?"
        + urlencode(
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(ids[:24]),
                "props": "claims|labels",
                "languages": "es|en",
            }
        )
    )
    entities = (data or {}).get("entities") or {}
    results = []
    fallback_labels = {hit.get("id"): hit.get("label") or "" for hit in hits}

    for qid in ids:
        entity = entities.get(qid) or {}
        if "Q11424" not in wikidata_claim_values(entity, "P31"):
            continue
        title = wikidata_entity_label(entity, fallback_labels.get(qid, ""))
        dates = wikidata_claim_values(entity, "P577")
        images = wikidata_claim_values(entity, "P18")
        imdb = next(iter(wikidata_claim_values(entity, "P345")), "")
        date = next(iter(dates), "").lstrip("+")
        results.append(
            {
                "source": "wikidata",
                "sourceLabel": "Wikidata",
                "id": qid,
                "category": "pelicula",
                "title": title,
                "year": date[:4],
                "image": wikidata_image_url(next(iter(images), "")),
                "link": f"https://www.imdb.com/title/{imdb}/" if imdb else f"https://www.wikidata.org/wiki/{qid}",
                "summary": "",
                "seasons": [],
                "total": None,
                "providers": [],
                "score": title_match_score(title, query),
                "format": "film",
            }
        )
    return results


def wikidata_recent_movies():
    sparql = """
    SELECT ?item ?label (MIN(?dateValue) AS ?date) (SAMPLE(?imageValue) AS ?image) (SAMPLE(?imdbValue) AS ?imdb) WHERE {
      ?item wdt:P31 wd:Q11424;
            rdfs:label ?label;
            wdt:P577 ?dateValue.
      FILTER(LANG(?label) = "en")
      FILTER(?dateValue >= "2024-01-01"^^xsd:dateTime)
      OPTIONAL { ?item wdt:P18 ?imageValue. }
      OPTIONAL { ?item wdt:P345 ?imdbValue. }
    }
    GROUP BY ?item ?label
    ORDER BY DESC(?date)
    LIMIT 12
    """
    return [
        wikidata_movie_result(row, "Wikidata recientes")
        for row in wikidata_bindings(sparql)
        if binding_value(row, "label")
    ]


def anilist_item_to_result(item, category, source_label="AniList"):
    title = ((item.get("title") or {}).get("english") or (item.get("title") or {}).get("romaji") or "")
    total = item.get("episodes") if category == "anime" else item.get("chapters")
    country = item.get("countryOfOrigin") or ""
    return {
        "source": "anilist",
        "sourceLabel": source_label,
        "id": item.get("id"),
        "category": category,
        "subtype": reading_subtype(country) if category == "lectura" else None,
        "title": title,
        "year": str(((item.get("startDate") or {}).get("year")) or ""),
        "image": ((item.get("coverImage") or {}).get("extraLarge") or (item.get("coverImage") or {}).get("large") or ""),
        "link": item.get("siteUrl") or "",
        "summary": clean_html(item.get("description") or ""),
        "seasons": [{"number": 1, "total": total}] if total else [],
        "total": total,
        "providers": [],
        "score": item.get("averageScore") or 0,
        "format": item.get("format") or "",
        "origin": country,
        "status": item.get("status") or "",
    }


def anilist_results(query, category):
    if category not in {"anime", "lectura"}:
        return []
    media_type = "ANIME" if category == "anime" else "MANGA"
    graphql = """
    query ($search: String, $type: MediaType) {
      Page(page: 1, perPage: 6) {
        media(search: $search, type: $type, isAdult: false, sort: SEARCH_MATCH) {
          id
          title { romaji english native }
          coverImage { extraLarge large }
          siteUrl
          description(asHtml: false)
          startDate { year }
          episodes
          chapters
          volumes
          format
          countryOfOrigin
          averageScore
          status
        }
      }
    }
    """
    data = fetch_json(
        "https://graphql.anilist.co",
        payload={"query": graphql, "variables": {"search": query, "type": media_type}},
    )
    media = (((data or {}).get("data") or {}).get("Page") or {}).get("media") or []
    return [anilist_item_to_result(item, category) for item in media]


def anilist_trending(category):
    if category not in {"anime", "lectura"}:
        return []
    media_type = "ANIME" if category == "anime" else "MANGA"
    graphql = """
    query ($type: MediaType) {
      Page(page: 1, perPage: 12) {
        media(type: $type, isAdult: false, sort: [TRENDING_DESC, POPULARITY_DESC]) {
          id
          title { romaji english native }
          coverImage { extraLarge large }
          siteUrl
          description(asHtml: false)
          startDate { year }
          episodes
          chapters
          volumes
          format
          countryOfOrigin
          averageScore
          status
        }
      }
    }
    """
    data = fetch_json(
        "https://graphql.anilist.co",
        payload={"query": graphql, "variables": {"type": media_type}},
    )
    media = (((data or {}).get("data") or {}).get("Page") or {}).get("media") or []
    return [anilist_item_to_result(item, category, "AniList tendencias") for item in media]


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
                "subtype": "manga" if category == "lectura" else None,
                "title": title,
                "year": str(year or ""),
                "image": (((item.get("images") or {}).get("jpg") or {}).get("large_image_url") or ""),
                "link": item.get("url") or "",
                "summary": item.get("synopsis") or "",
                "seasons": [{"number": 1, "total": total}] if total else [],
                "total": total,
                "providers": [],
                "score": item.get("score") or 0,
                "format": item.get("type") or "",
            }
        )
    return results


def jikan_trending(category):
    if category not in {"anime", "lectura"}:
        return []
    kind = "anime" if category == "anime" else "manga"
    data = fetch_json(f"https://api.jikan.moe/v4/top/{kind}?{urlencode({'limit': 8, 'sfw': 'true'})}")
    if not isinstance(data, dict):
        return []

    results = []
    for item in data.get("data") or []:
        total = item.get("episodes") if kind == "anime" else item.get("chapters")
        title = item.get("title_spanish") or item.get("title_english") or item.get("title") or ""
        year = item.get("year") or ((item.get("published") or {}).get("from") or "")[:4]
        results.append(
            {
                "source": "jikan",
                "sourceLabel": "Jikan top",
                "id": item.get("mal_id"),
                "category": category,
                "subtype": "manga" if category == "lectura" else None,
                "title": title,
                "year": str(year or ""),
                "image": (((item.get("images") or {}).get("jpg") or {}).get("large_image_url") or ""),
                "link": item.get("url") or "",
                "summary": item.get("synopsis") or "",
                "seasons": [{"number": 1, "total": total}] if total else [],
                "total": total,
                "providers": [],
                "score": item.get("score") or 0,
                "format": item.get("type") or "",
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
        original_language = attrs.get("originalLanguage") or ""
        results.append(
            {
                "source": "mangadex",
                "sourceLabel": "MangaDex",
                "id": manga_id,
                "category": category,
                "subtype": {"ko": "manhwa", "zh": "manhua", "zh-hk": "manhua"}.get(original_language, "manga"),
                "title": title,
                "year": str(attrs.get("year") or ""),
                "image": mangadex_cover(manga_id, item.get("relationships")),
                "link": f"https://mangadex.org/title/{manga_id}",
                "summary": localized_text(attrs.get("description")),
                "seasons": [{"number": 1, "total": total}] if total else [],
                "total": total,
                "providers": [],
                "score": 0,
                "format": attrs.get("publicationDemographic") or "",
            }
        )
    return results


def unique_results(results, limit=8):
    seen_ids = set()
    seen_titles = set()
    unique = []
    for item in sorted(results, key=lambda result: (source_rank(result.get("source")), -(result.get("score") or 0))):
        source_key = (item.get("source"), item.get("id"))
        title_key = normalized_title(item.get("title"))
        if source_key in seen_ids or (title_key and title_key in seen_titles):
            continue
        seen_ids.add(source_key)
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)
    return unique[:limit]


@app.get("/api/metadata/search")
@login_required
def metadata_search():
    query = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "series").strip()
    if len(query) < 2:
        return jsonify({"results": [], "message": "Escribe al menos 2 caracteres"})

    if category in {"anime", "lectura"}:
        results = anilist_results(query, category)
        if category == "lectura":
            results.extend(mangadex_results(query, category))
        results.extend(jikan_results(query, category))
    else:
        results = tmdb_results(query, category)
        if category in {"series", "drama"}:
            results.extend(tvmaze_results(query, category))
            results.extend(episodate_results(query, category))
        elif category == "pelicula":
            results.extend(wikidata_movie_results(query))

    sources = sorted({item.get("sourceLabel") for item in results if item.get("sourceLabel")})
    unique = unique_results(results, 8)
    return jsonify(
        {
            "results": unique,
            "hasTmdb": bool(TMDB_API_KEY),
            "sources": sources,
        }
    )


@app.get("/api/metadata/discover")
@login_required
def metadata_discover():
    category = (request.args.get("category") or "anime").strip()
    if category == "todo":
        category = "anime"

    if category in {"anime", "lectura"}:
        results = anilist_trending(category)
        results.extend(jikan_trending(category))
    elif category in {"series", "drama"}:
        results = tmdb_trending(category)
        results.extend(episodate_trending(category))
    elif category == "pelicula":
        results = tmdb_trending(category)
        if not results:
            results.extend(wikidata_recent_movies())
    else:
        results = tmdb_trending(category)

    sources = sorted({item.get("sourceLabel") for item in results if item.get("sourceLabel")})
    unique = unique_results(results, 10)
    return jsonify(
        {
            "results": unique,
            "hasTmdb": bool(TMDB_API_KEY),
            "sources": sources,
        }
    )


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


def calculate_badge(profile_slot):
    if not profile_slot:
        return None
    with connect() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM catalog
            WHERE who = %s AND category IN ('series', 'anime')
            """,
            (profile_slot,),
        ).fetchone()[0]
    
    if count >= 100:
        return {"level": "diamante", "count": count, "label": "💎 Diamante"}
    elif count >= 50:
        return {"level": "oro", "count": count, "label": "🥇 Oro"}
    elif count >= 25:
        return {"level": "plata", "count": count, "label": "🥈 Plata"}
    elif count >= 10:
        return {"level": "bronce", "count": count, "label": "🥉 Bronce"}
    else:
        return None


@app.get("/api/users")
@admin_required
def get_users():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, active, created_at, profile_slot, color
            FROM users
            ORDER BY active DESC, username ASC
            """
        ).fetchall()
    
    users_data = []
    for row in rows:
        badge = calculate_badge(row[6])
        users_data.append(
            {
                "id": row[0],
                "username": row[1],
                "displayName": row[2] or row[1],
                "role": row[3] or "user",
                "active": row[4],
                "createdAt": row[5].isoformat() if row[5] else None,
                "profileSlot": row[6],
                "color": row[7] or "#3b82f6",
                "badge": badge,
            }
        )
    return jsonify(users_data)


@app.get("/api/profiles")
@login_required
def get_profiles():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT username, display_name, profile_slot, color
            FROM users
            WHERE active = TRUE AND profile_slot IN ('P1', 'P2')
            """
        ).fetchall()
    
    profiles = []
    for row in rows:
        badge = calculate_badge(row[2])
        profiles.append(
            {
                "slot": row[2],
                "username": row[0],
                "displayName": row[1] or row[0],
                "color": row[3] or "#3b82f6",
                "badge": badge,
            }
        )
    return jsonify(profiles)


@app.post("/api/users")
@admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("displayName") or username).strip()
    role = data.get("role") if data.get("role") in {"admin", "user"} else "user"
    profile_slot = data.get("profileSlot") or None
    color = data.get("color") or "#3b82f6"
    if profile_slot not in {None, "", "P1", "P2"}:
        return jsonify({"error": "Perfil invalido"}), 400
    profile_slot = profile_slot or None
    if len(username) < 3 or len(password) < 6:
        return jsonify({"error": "Usuario minimo 3 caracteres y contrasena minimo 6"}), 400

    with connect() as conn:
        with conn.transaction():
            if profile_slot:
                taken = conn.execute(
                    """
                    SELECT display_name FROM users
                    WHERE active = TRUE AND profile_slot = %s AND username != %s
                    """,
                    (profile_slot, username),
                ).fetchone()
                if taken:
                    nombre = "Persona 1" if profile_slot == "P1" else "Persona 2"
                    return jsonify(
                        {"error": f"{nombre} ya esta asignada a {taken[0]}. Desactivala o elige otro perfil."}
                    ), 409

            exists = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, display_name = %s, role = %s, active = TRUE, profile_slot = %s, color = %s
                    WHERE username = %s
                    """,
                    (generate_password_hash(password), display_name, role, profile_slot, color, username),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, display_name, role, active, profile_slot, color)
                    VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                    """,
                    (username, generate_password_hash(password), display_name, role, profile_slot, color),
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


@app.post("/api/users/<int:user_id>/activate")
@admin_required
def activate_user(user_id):
    with connect() as conn:
        row = conn.execute("SELECT id, profile_slot FROM users WHERE id = %s", (user_id,)).fetchone()
        if not row:
            return jsonify({"error": "Usuario no encontrado"}), 404
        profile_slot = row[1]
        if profile_slot:
            taken = conn.execute(
                """
                SELECT display_name FROM users
                WHERE active = TRUE AND profile_slot = %s AND id != %s
                """,
                (profile_slot, user_id),
            ).fetchone()
            if taken:
                nombre = "Persona 1" if profile_slot == "P1" else "Persona 2"
                return jsonify(
                    {"error": f"{nombre} ya esta asignada a {taken[0]}. Cambia su perfil o desactivalo primero."}
                ), 409
        conn.execute("UPDATE users SET active = TRUE WHERE id = %s", (user_id,))
    return jsonify({"success": True})


@app.put("/api/users/<int:user_id>")
@admin_required
def update_user(user_id):
    data = request.get_json(silent=True) or {}
    display_name = (data.get("displayName") or "").strip()
    color = data.get("color") or "#3b82f6"
    profile_slot = data.get("profileSlot") or None
    
    if profile_slot not in {None, "", "P1", "P2"}:
        return jsonify({"error": "Perfil invalido"}), 400
    profile_slot = profile_slot or None
    
    with connect() as conn:
        row = conn.execute("SELECT id, username, profile_slot FROM users WHERE id = %s", (user_id,)).fetchone()
        if not row:
            return jsonify({"error": "Usuario no encontrado"}), 404
        
        current_slot = row[2]
        
        with conn.transaction():
            if profile_slot and profile_slot != current_slot:
                taken = conn.execute(
                    """
                    SELECT display_name FROM users
                    WHERE active = TRUE AND profile_slot = %s AND id != %s
                    """,
                    (profile_slot, user_id),
                ).fetchone()
                if taken:
                    nombre = "Persona 1" if profile_slot == "P1" else "Persona 2"
                    return jsonify(
                        {"error": f"{nombre} ya esta asignada a {taken[0]}. Desactivala o elige otro perfil."}
                    ), 409
            
            conn.execute(
                """
                UPDATE users
                SET display_name = %s, color = %s, profile_slot = %s
                WHERE id = %s
                """,
                (display_name or None, color, profile_slot, user_id),
            )
    
    return jsonify({"success": True})


@app.get("/")
@login_required
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/catalog")
@login_required
def get_catalog():
    return jsonify(read_catalog())


def read_catalog():
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
    return catalog


@app.post("/api/catalog")
@login_required
def save_catalog():
    catalog = request.get_json(silent=True)
    if not isinstance(catalog, list):
        return jsonify({"error": "El catalogo debe ser una lista"}), 400

    with connect() as conn:
        with conn.transaction():
            for item in catalog:
                conn.execute(
                    """
                    INSERT INTO catalog
                      (id, title, image, link, category, subtype, status, who, seasons, volumes, updated_at)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, COALESCE(to_timestamp(%s / 1000.0), CURRENT_TIMESTAMP))
                    ON CONFLICT (id) DO UPDATE SET
                      title = EXCLUDED.title,
                      image = EXCLUDED.image,
                      link = EXCLUDED.link,
                      category = EXCLUDED.category,
                      subtype = EXCLUDED.subtype,
                      status = EXCLUDED.status,
                      who = EXCLUDED.who,
                      seasons = EXCLUDED.seasons,
                      volumes = EXCLUDED.volumes,
                      updated_at = EXCLUDED.updated_at
                    WHERE catalog.updated_at <= EXCLUDED.updated_at
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

    return jsonify({"success": True, "message": "Catalogo guardado", "catalog": read_catalog()})


@app.delete("/api/catalog/<item_id>")
@login_required
def delete_catalog_item(item_id):
    with connect() as conn:
        conn.execute("DELETE FROM catalog WHERE id = %s", (item_id,))
    return jsonify({"success": True, "catalog": read_catalog()})


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
