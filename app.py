from flask import Flask, render_template, redirect, jsonify, send_file, request, abort, url_for
import os
import subprocess
import requests
import mimetypes
import re
import json
import sqlite3
import hashlib
import random
import base64
import shutil
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
from datetime import datetime, timedelta
 
try:
    from pypresence import Presence
    DISCORD_CLIENT_ID = "1512299340398329907" 
    rpc = Presence(DISCORD_CLIENT_ID)
    rpc_connected = False
except ImportError:
    rpc = None
    rpc_connected = False

RPC_START_TIME = None
CURRENT_RPC_ANIME = None

ANIME_PATHS = [
    r"D:\Fajar\Anime\Watchlist",
    r"D:\Fajar\Anime\Onggoing",
]

app = Flask(__name__)

# Path Absolut agar aplikasi stabil saat dijalankan dari Tray/VBS
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

MOVIE_PATH = r"D:\Fajar\Anime\Watchlist\Movies"
VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
DISCORD_RPC_ENABLED = True

POSTER_CACHE = os.path.join(BASE_DIR, "cache", "posters")
BANNER_CACHE = os.path.join(BASE_DIR, "cache", "banners")
THUMBNAIL_CACHE = os.path.join(BASE_DIR, "cache", "thumbnails")
METADATA_CACHE = os.path.join(BASE_DIR, "cache", "metadata")
CHARACTER_CACHE = os.path.join(BASE_DIR, "cache", "characters")
EPISODE_CACHE = os.path.join(BASE_DIR, "cache", "episodes")
SUBTITLE_CACHE = os.path.join(BASE_DIR, "cache", "subtitles")
DB_PATH = os.path.join(BASE_DIR, "cache", "library.db")
WATCH_HISTORY_FILE = os.path.join(BASE_DIR, "cache", "watch_history.json")
WATCH_STATUS_FILE = os.path.join(BASE_DIR, "cache", "watch_status.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "cache", "settings.json")
WATCH_DATA_LOCK = threading.RLock()
SCHEDULE_CACHE_LOCK = threading.RLock()
SCHEDULE_CACHE = {
    "expires_at": 0,
    "airing_list": [],
    "error": None
}
SCHEDULE_CACHE_TTL_SECONDS = 300
STUDIO_PROJECT_CACHE_TTL_SECONDS = 86400
STUDIO_PROJECT_LIMIT = 80
THEME_PRESETS = {
    "dark-blue",
    "midnight-violet",
    "dark-orange",
    "amoled-black"
}

PROTECTED_CACHE_FILES = {
    os.path.abspath(DB_PATH),
    os.path.abspath(SETTINGS_FILE),
    os.path.abspath(WATCH_HISTORY_FILE),
    os.path.abspath(WATCH_STATUS_FILE),
    os.path.abspath(f"{WATCH_HISTORY_FILE}.bak"),
    os.path.abspath(f"{WATCH_STATUS_FILE}.bak"),
}

def get_default_settings():
    return {
        "watchlist_path": r"D:\Fajar\Anime\Watchlist",
        "ongoing_path": r"D:\Fajar\Anime\Onggoing",
        "movie_path": r"D:\Fajar\Anime\Watchlist\Movies",
        "vlc_path": r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        "discord_rpc_enabled": True,
        "theme_preset": "dark-blue"
    }

def load_settings():
    defaults = get_default_settings()

    if not os.path.exists(SETTINGS_FILE):
        return defaults

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return defaults

    if not isinstance(settings, dict):
        return defaults

    merged = defaults.copy()
    merged.update(settings)
    if merged.get("theme_preset") not in THEME_PRESETS:
        merged["theme_preset"] = defaults["theme_preset"]
    return merged

def save_settings(settings):
    os.makedirs(
        os.path.dirname(SETTINGS_FILE),
        exist_ok=True
    )

    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            settings,
            f,
            indent=4
        )

def normalize_bool_setting(value, default=False):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False

    if value is None:
        return default

    return default

def apply_settings(settings):
    global ANIME_PATHS, MOVIE_PATH, VLC_PATH, DISCORD_RPC_ENABLED

    defaults = get_default_settings()
    merged = defaults.copy()
    merged.update(settings or {})

    ANIME_PATHS = [
        merged["watchlist_path"],
        merged["ongoing_path"],
    ]
    MOVIE_PATH = merged["movie_path"]
    VLC_PATH = merged["vlc_path"]
    DISCORD_RPC_ENABLED = normalize_bool_setting(
        merged.get("discord_rpc_enabled"),
        defaults["discord_rpc_enabled"]
    )

def get_current_theme():
    theme = load_settings().get("theme_preset", "dark-blue")
    if theme not in THEME_PRESETS:
        return "dark-blue"
    return theme

@app.context_processor
def inject_theme():
    return {
        "current_theme": get_current_theme()
    }

def get_existing_anime_names():
    existing_names = set()

    for base_path in ANIME_PATHS:
        if not base_path or not os.path.isdir(base_path):
            continue

        try:
            for name in os.listdir(base_path):
                full_path = os.path.join(base_path, name)
                if os.path.isdir(full_path):
                    existing_names.add(name)
        except OSError:
            continue

    return existing_names

def safe_cache_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name or "")

def is_path_inside_cache(path):
    cache_root = os.path.abspath(os.path.join(BASE_DIR, "cache"))
    candidate = os.path.abspath(path)

    try:
        return os.path.commonpath([cache_root, candidate]) == cache_root
    except ValueError:
        return False

def is_protected_cache_file(path):
    candidate = os.path.abspath(path)
    if candidate in PROTECTED_CACHE_FILES:
        return True

    cache_root = os.path.abspath(os.path.join(BASE_DIR, "cache"))
    try:
        if os.path.commonpath([cache_root, candidate]) != cache_root:
            return False
    except ValueError:
        return False

    filename = os.path.basename(candidate)
    return (
        filename.startswith("watch_history.json.")
        or filename.startswith("watch_status.json.")
        or filename.startswith(".watch_history.json.tmp-")
        or filename.startswith(".watch_status.json.tmp-")
        or filename.startswith("library.db-")
    )

def cleanup_orphan_cache():
    summary = {
        "removed_files": 0,
        "removed_dirs": 0,
        "skipped": 0,
        "details": []
    }

    existing_anime_names = get_existing_anime_names()
    existing_safe_names = {
        safe_cache_name(name)
        for name in existing_anime_names
    }
    available_base_paths = [
        path
        for path in ANIME_PATHS
        if path and os.path.isdir(path)
    ]

    if not available_base_paths:
        summary["skipped"] += 1
        summary["details"].append(
            "Skipped cleanup because no configured anime folders are available."
        )
        return summary

    def remove_file_if_safe(path, label):
        if is_protected_cache_file(path):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped protected cache file: {label}")
            print(f"Cache cleanup skipped protected file: {path}")
            return

        if not is_path_inside_cache(path) or not os.path.isfile(path):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped unsafe file: {label}")
            return

        try:
            os.remove(path)
            summary["removed_files"] += 1
            summary["details"].append(f"Removed file: {label}")
        except OSError as e:
            summary["skipped"] += 1
            summary["details"].append(f"Skipped file {label}: {e}")

    def remove_dir_if_safe(path, label):
        if not is_path_inside_cache(path) or not os.path.isdir(path):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped unsafe folder: {label}")
            return

        try:
            shutil.rmtree(path)
            summary["removed_dirs"] += 1
            summary["details"].append(f"Removed folder: {label}")
        except OSError as e:
            summary["skipped"] += 1
            summary["details"].append(f"Skipped folder {label}: {e}")

    def clean_named_files(cache_dir, label):
        if not os.path.isdir(cache_dir):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped missing cache folder: {label}")
            return

        for filename in os.listdir(cache_dir):
            path = os.path.join(cache_dir, filename)
            if not os.path.isfile(path):
                summary["skipped"] += 1
                continue

            stem, _ = os.path.splitext(filename)
            if stem not in existing_safe_names:
                remove_file_if_safe(path, f"{label}/{filename}")

    def clean_named_dirs(cache_dir, label):
        if not os.path.isdir(cache_dir):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped missing cache folder: {label}")
            return

        for folder_name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, folder_name)
            if not os.path.isdir(path):
                summary["skipped"] += 1
                continue

            if folder_name not in existing_safe_names:
                remove_dir_if_safe(path, f"{label}/{folder_name}")

    def clean_episode_files():
        if not os.path.isdir(EPISODE_CACHE):
            summary["skipped"] += 1
            summary["details"].append("Skipped missing cache folder: episodes")
            return

        for filename in os.listdir(EPISODE_CACHE):
            path = os.path.join(EPISODE_CACHE, filename)
            if not os.path.isfile(path) or not filename.lower().endswith(".json"):
                summary["skipped"] += 1
                continue

            stem = os.path.splitext(filename)[0]
            matches_existing = any(
                stem == safe_name or stem.startswith(f"{safe_name}_")
                for safe_name in existing_safe_names
            )

            if not matches_existing:
                remove_file_if_safe(path, f"episodes/{filename}")

    clean_named_files(POSTER_CACHE, "posters")
    clean_named_files(BANNER_CACHE, "banners")
    clean_named_files(METADATA_CACHE, "metadata")
    clean_named_dirs(CHARACTER_CACHE, "characters")
    clean_named_dirs(SUBTITLE_CACHE, "subtitles")
    clean_episode_files()

    summary["skipped"] += 1
    summary["details"].append(
        "Skipped thumbnails because thumbnail cache files are hashed by video path."
    )
    summary["skipped"] += 1
    summary["details"].append(
        "Skipped protected watch/settings/database files and watch data backups."
    )

    return summary

def init_db():
    os.makedirs(
        POSTER_CACHE,
        exist_ok=True
    )
    os.makedirs(
        THUMBNAIL_CACHE,
        exist_ok=True
    )
    os.makedirs(
        METADATA_CACHE,
        exist_ok=True
    )
    os.makedirs(
        EPISODE_CACHE,
        exist_ok=True
    )
    os.makedirs(
        BANNER_CACHE,
        exist_ok=True
    )
    os.makedirs(
        SUBTITLE_CACHE,
        exist_ok=True
    )
    os.makedirs(
        CHARACTER_CACHE,
        exist_ok=True
    )

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anime_library (
                name TEXT PRIMARY KEY,
                episodes INTEGER,
                score REAL,
                genres TEXT,
                year INTEGER,
                season TEXT,
                status TEXT
            )
        """)

        # Sinkronisasi skema database: Tambahkan kolom jika menggunakan database versi lama
        cursor = conn.execute("PRAGMA table_info(anime_library)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'genres' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN genres TEXT")
        if 'year' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN year INTEGER")
        if 'season' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN season TEXT")
        if 'status' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN status TEXT")

init_db()
apply_settings(load_settings())

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv"
)

def safe_join_media_path(base_path, relative_path):

    if not base_path:
        return None

    base_path = os.path.abspath(base_path)

    candidate_path = os.path.abspath(
        os.path.normpath(
            os.path.join(
                base_path,
                relative_path
            )
        )
    )

    try:

        if os.path.commonpath(
            [
                base_path,
                candidate_path
            ]
        ) != base_path:

            return None

    except ValueError:

        return None

    return candidate_path

def clean_movie_title(filename):

    title = os.path.splitext(
        filename
    )[0]

    title = re.sub(
        r"\[.*?\]",
        "",
        title
    )

    title = re.sub(
        r"\b1080p\b",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bBD\b",
        "",
        title,
        flags=re.I
    )

    return " ".join(
        title.split()
    )

def get_anilist_poster(anime_name):

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    banner_path = os.path.join(
        BANNER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return poster_path

    # Gunakan info dari metadata cache jika tersedia untuk menghindari API call ganda
    info = get_cached_anilist_info(anime_name)
    if not info:
        return None

    try:
        poster_url = info.get("poster")
        banner_url = info.get("banner")

        if not poster_url:
            return None

        # Download Poster
        poster_image = requests.get(
            poster_url,
            timeout=15
        )

        with open(
            poster_path,
            "wb"
        ) as f:

            f.write(
                poster_image.content
            )

        if banner_url:

            banner_image = requests.get(
                banner_url,
                timeout=15
            )

            with open(
                banner_path,
                "wb"
            ) as f:

                f.write(
                    banner_image.content
                )

        return poster_path

    except Exception as e:

        print(
            "AniList Error:",
            anime_name,
            e
        )

        return None

def get_anilist_info(anime_name):

    query = """
    query ($search: String) {
      Media(
        search: $search,
        type: ANIME
      ) {
        title {
          romaji
          english
        }
        description
        episodes
        duration
        format
        season
        seasonYear
        status
        genres
        averageScore
        bannerImage
        coverImage {
          extraLarge
        }
        studios(
          isMain: true
        ) {
          nodes {
            name
          }
        }
        characters(sort: [ROLE, FAVOURITES_DESC], perPage: 6) {
          edges {
            role
            node {
              name { full }
              image { large }
            }
            voiceActors(language: JAPANESE) {
              name { full }
              image { large }
            }
          }
        }
        relations {
          edges {
            relationType
            node {
              title {
                romaji
                english
              }
              coverImage {
                extraLarge
              }
              type
              status
            }
          }
        }
        recommendations(sort: [RATING_DESC, ID_DESC], perPage: 10) {
          nodes {
            mediaRecommendation {
              title {
                romaji
                english
              }
              coverImage {
                extraLarge
              }
              type
              status
            }
          }
        }
      }
    }
    """

    try:

        response = requests.post(
            "https://graphql.anilist.co",
            json={
                "query": query,
                "variables": {
                    "search": anime_name
                }
            },
            timeout=20
        )

        data = response.json()

        media = data["data"]["Media"]

        if not media:
            return None

        studio = None

        # Akses nama studio secara aman
        studios = media.get("studios", {})
        nodes = studios.get("nodes", [])
        if nodes and nodes[0]:
            studio = nodes[0].get("name")

        chars_list = []
        for edge in media.get("characters", {}).get("edges", []):
            char_node = edge.get("node")
            if not char_node: continue
            va = edge.get("voiceActors", [])
            va_node = va[0] if va else None
            
            chars_list.append({
                "name": char_node["name"]["full"],
                "image_url": char_node["image"]["large"],
                "role": edge.get("role"),
                "va_name": va_node["name"]["full"] if va_node else None,
                "va_image_url": va_node["image"]["large"] if va_node else None
            })

        relations_list = []
        for edge in media.get("relations", {}).get("edges", []):
            rel_node = edge.get("node")
            if not rel_node or rel_node.get("type") != "ANIME":
                continue
            relations_list.append({
                "title": rel_node["title"]["english"] or rel_node["title"]["romaji"],
                "poster": rel_node["coverImage"]["extraLarge"],
                "type": edge.get("relationType").replace("_", " ").title(),
                "status": rel_node.get("status")
            })

        recommendations_list = []
        for node in media.get("recommendations", {}).get("nodes", []):
            rec_media = node.get("mediaRecommendation")
            if not rec_media or rec_media.get("type") != "ANIME":
                continue
            recommendations_list.append({
                "title": rec_media["title"]["english"] or rec_media["title"]["romaji"],
                "poster": rec_media["coverImage"]["extraLarge"],
                "type": rec_media.get("type").replace("_", " ").title(),
                "status": rec_media.get("status")
            })

        return {
            "title": media["title"]["english"] or media["title"]["romaji"],
            "description": media["description"],
            "episodes": media["episodes"],
            "duration": media["duration"],
            "format": media["format"],
            "season": media["season"],
            "year": media["seasonYear"],
            "status": media["status"],
            "studio": studio,
            "genres": media["genres"],
            "score": media["averageScore"],
            "poster": media["coverImage"]["extraLarge"],
            "banner": media["bannerImage"],
            "characters": chars_list,
            "relations": relations_list,
            "recommendations": recommendations_list
        }

    except Exception as e:

        print(
            "AniList Error:",
            anime_name,
            e
        )

        return None
    
def get_thumbnail(video_path):

    print("\n====================")
    print("MEMBUAT THUMBNAIL")
    print(video_path)
    print("====================\n")

    filename = hashlib.md5(
        video_path.encode("utf-8")
    ).hexdigest()

    thumbnail_path = os.path.join(
        THUMBNAIL_CACHE,
        filename + ".jpg"
    )

    if os.path.exists(
        thumbnail_path
    ):
        print(
            "Thumbnail already exists:",
            thumbnail_path
        )

        return thumbnail_path

    try:

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "00:00:10",
                "-i",
                video_path,
                "-frames:v",
                "1",
                thumbnail_path
            ],
            capture_output=True,
            text=True
        )

        print(
            "FFmpeg return code:",
            result.returncode
        )

        if result.stderr:
            print(
                "\nFFmpeg stderr:\n",
                result.stderr
            )

        if os.path.exists(
            thumbnail_path
        ):

            print(
                "Thumbnail created successfully:"
            )

            print(
                thumbnail_path
            )

            return thumbnail_path

        print(
            "Thumbnail was not created"
        )

    except Exception as e:

        print(
            "Thumbnail gagal:",
            e
        )

    return None

def get_video_resolution(video_path):

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                video_path
            ],
            capture_output=True,
            text=True
        )

        print(result.stdout)

        data = json.loads(result.stdout)

        for stream in data["streams"]:

            if stream["codec_type"] == "video":

                print("HEIGHT =", stream["height"])

                return f'{stream["height"]}p'

    except Exception as e:

        print("Resolution Error:", e)

    return ""

def get_episode_cache(
    anime_name,
    video_path,
    season_name=None
):

    cache_name = anime_name

    if season_name:

        cache_name += (
            "_" +
            season_name
        )

    cache_name = (
        cache_name
        .replace("\\", "_")
        .replace("/", "_")
        .replace(":", "")
    )

    cache_file = os.path.join(
        EPISODE_CACHE,
        f"{cache_name}.json"
    )

    cache_data = {}

    if os.path.exists(
        cache_file
    ):

        try:

            with open(
                cache_file,
                "r",
                encoding="utf-8"
            ) as f:

                cache_data = json.load(
                    f
                )

        except:

            cache_data = {}

    filename = os.path.basename(
        video_path
    )

    if filename in cache_data:

        return cache_data[
            filename
        ]

    duration = get_video_duration(
        video_path
    )

    resolution = get_video_resolution(
        video_path
    )

    cache_data[
        filename
    ] = {

        "duration":
            duration,

        "resolution":
            resolution

    }

    with open(
        cache_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            cache_data,
            f,
            ensure_ascii=False,
            indent=4
        )

    return cache_data[
        filename
    ]

def get_episode_number(filename):
    # Mencari angka yang biasanya muncul setelah spasi, tanda hubung, atau di akhir (episode)
    # Dan mengabaikan angka yang diikuti 'p' atau 'k' (resolusi seperti 1080p atau 4k)
    
    # Pola: cari angka yang didahului pembatas dan bukan bagian dari resolusi
    match = re.search(r'(?:^|[\s\-_])(\d+)(?![pk])(?:[\s\-_]|$)', filename, re.I)
    
    if not match:
        # Fallback ke pencarian angka pertama jika pola khusus tidak ditemukan
        match = re.search(r'(\d+)', filename)

    if match:
        return int(match.group(1))

    return 0

def get_video_duration(video_path):

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                video_path
            ],
            capture_output=True,
            text=True
        )

        data = json.loads(
            result.stdout
        )

        seconds = int(
            float(
                data["format"]["duration"]
            )
        )

        minutes = seconds // 60

        return f"{minutes} min"

    except:
        return ""

def find_anime_path(anime_name):

    for base_path in ANIME_PATHS:

        anime_path = os.path.join(
            base_path,
            anime_name
        )

        if os.path.isdir(anime_path):
            return anime_path

    return None

def find_media_path(library_name):

    if library_name == "Movies":
        if MOVIE_PATH and os.path.isdir(MOVIE_PATH):
            return MOVIE_PATH

        return None

    return find_anime_path(library_name)

def get_season_anilist_info(anime_name, season_name):

    search_name = season_name

    if anime_name.lower() not in season_name.lower():

        search_name = f"{anime_name} {season_name}"

    info = get_cached_anilist_info(
        search_name
    )

    if info:

        return info

    return get_cached_anilist_info(
        anime_name
    )

def get_airing_schedule():
    local_tz = datetime.now().astimezone().tzinfo
    now = datetime.now(local_tz)
    start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=1)
    start_of_day = int(start_dt.timestamp())
    end_of_day = int(end_dt.timestamp())

    query = """
    query ($start: Int, $end: Int, $page: Int) {
      Page(page: $page, perPage: 50) {
        pageInfo {
          total
          currentPage
          lastPage
          hasNextPage
          perPage
        }
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
          airingAt
          episode
          media {
            title {
              romaji
              english
            }
            coverImage {
              extraLarge
            }
            format
          }
        }
      }
    }
    """

    schedules = []
    page = 1

    try:
        while True:
            response = requests.post(
                "https://graphql.anilist.co",
                json={
                    "query": query,
                    "variables": {
                        "start": start_of_day,
                        "end": end_of_day,
                        "page": page
                    }
                },
                timeout=10
            )

            print(
                "Schedule API status:",
                response.status_code,
                "page:",
                page,
                "window:",
                start_dt.isoformat(),
                "to",
                end_dt.isoformat(),
                "rate_remaining:",
                response.headers.get("X-RateLimit-Remaining")
            )

            try:
                data = response.json()
            except ValueError as e:
                print("Schedule API JSON Error:", e)
                return [], "AniList returned an invalid JSON response."

            if response.status_code >= 400:
                print("Schedule API HTTP Error JSON:", data)
                return [], f"AniList schedule request failed with HTTP {response.status_code}."

            errors = data.get("errors")
            if errors:
                print("Schedule API GraphQL Errors:", errors)
                return [], "AniList returned GraphQL errors for the schedule request."

            page_data = data.get("data", {}).get("Page")
            if not page_data:
                print("Schedule API Missing Page Data:", data)
                return [], "AniList schedule response did not include page data."

            page_info = page_data.get("pageInfo") or {}
            page_schedules = page_data.get("airingSchedules") or []
            schedules.extend(page_schedules)

            print(
                "Schedule API page summary:",
                page_info,
                "items:",
                len(page_schedules)
            )

            if not page_info.get("hasNextPage"):
                break

            page += 1

        return schedules, None

    except requests.RequestException as e:
        print(f"Schedule API Request Error: {e}")
        return [], "Unable to reach AniList schedule API."

def get_cached_airing_schedule():
    now_monotonic = time.time()

    with SCHEDULE_CACHE_LOCK:
        if SCHEDULE_CACHE["expires_at"] > now_monotonic:
            return list(SCHEDULE_CACHE["airing_list"]), SCHEDULE_CACHE["error"]

    airing_list, schedule_error = get_airing_schedule()

    with SCHEDULE_CACHE_LOCK:
        SCHEDULE_CACHE["airing_list"] = airing_list
        SCHEDULE_CACHE["error"] = schedule_error
        SCHEDULE_CACHE["expires_at"] = time.time() + SCHEDULE_CACHE_TTL_SECONDS

    return list(airing_list), schedule_error

def build_schedule_items(airing_list, local_tz, now_ts):
    existing_anime_names = get_existing_anime_names()
    processed = []

    for item in airing_list:
        media = item.get("media") or {}
        title_data = media.get("title") or {}
        title = title_data.get("english") or title_data.get("romaji")

        if not title or not item.get("airingAt"):
            continue

        airing_dt = datetime.fromtimestamp(item["airingAt"], local_tz)
        airing_time = airing_dt.strftime("%H:%M")

        if item["airingAt"] <= now_ts < item["airingAt"] + 1800:
            airing_status = "Airing now"
        elif item["airingAt"] > now_ts:
            airing_status = "Upcoming"
        else:
            airing_status = "Aired"

        cover_image = media.get("coverImage") or {}

        processed.append({
            "title": title,
            "poster": cover_image.get("extraLarge") or url_for("static", filename="arcana.jpg"),
            "episode": item.get("episode"),
            "time": airing_time,
            "airing_at": item["airingAt"],
            "airing_iso": airing_dt.isoformat(),
            "format": media.get("format"),
            "status": airing_status,
            "detail_url": url_for("anime_detail", anime_name=title) if title in existing_anime_names else None
        })

    return processed

def format_schedule_alert_countdown(seconds):
    total_minutes = max(0, int((seconds + 59) // 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours and minutes:
        return f"Starts in {hours}h {minutes}m"

    if hours:
        return f"Starts in {hours}h"

    return f"Starts in {minutes}m"

def get_schedule_alert_payload(processed, now_ts, now_iso, timezone_offset_minutes):
    current_items = [
        item for item in processed
        if item["airing_at"] <= now_ts < item["airing_at"] + 1800
    ]
    upcoming_items = [
        item for item in processed
        if item["airing_at"] > now_ts
    ]
    alert_items = current_items + upcoming_items
    badge_count = len(current_items) + len([
        item for item in upcoming_items
        if item["airing_at"] <= now_ts + 7200
    ])

    payload_items = []
    for item in alert_items:
        if item in current_items:
            status_mode = "live"
            status_label = "LIVE NOW"
        else:
            status_mode = "upcoming"
            status_label = format_schedule_alert_countdown(item["airing_at"] - now_ts)

        payload_items.append({
            "title": item["title"],
            "poster": item["poster"],
            "episode": item["episode"],
            "time": item["time"],
            "airing_at": item["airing_at"],
            "airing_iso": item["airing_iso"],
            "format": item["format"],
            "detail_url": item["detail_url"],
            "status_mode": status_mode,
            "status_label": status_label
        })

    return {
        "items": payload_items,
        "badge_count": badge_count,
        "summary": f"{len(current_items)} airing now · {len(upcoming_items)} upcoming",
        "now_iso": now_iso,
        "timezone_offset_minutes": timezone_offset_minutes
    }

def update_discord_rpc(anime_name, episode_num, time_str=None):
    global rpc_connected, RPC_START_TIME, CURRENT_RPC_ANIME
    if not DISCORD_RPC_ENABLED:
        return

    if rpc is None:
        return
    
    try:
        # Reset timer jika menonton anime yang berbeda
        if CURRENT_RPC_ANIME != anime_name:
            RPC_START_TIME = time.time()
            CURRENT_RPC_ANIME = anime_name

        if not rpc_connected:
            rpc.connect()
            rpc_connected = True

        state_text = f"Episode {episode_num:02d}"
        if time_str:
            state_text += f" ({time_str})"

        rpc.update(
            details=anime_name,
            state=state_text,
            large_image="anzu_logo", # Dikembalikan agar status lebih stabil muncul di Discord
            buttons=[{"label": "Open Anzu Anime", "url": "http://animearchive.local:5000"}]
        )
    except Exception as e:
        print(f"Discord RPC Error: {e}")
        rpc_connected = False

def clear_discord_rpc():
    """Menghapus status Discord Rich Presence."""
    global rpc_connected, RPC_START_TIME, CURRENT_RPC_ANIME
    if not DISCORD_RPC_ENABLED:
        return

    if rpc is not None and rpc_connected:
        try:
            rpc.clear()
            RPC_START_TIME = None
            CURRENT_RPC_ANIME = None
            print("Discord RPC cleared")
        except Exception as e:
            print(f"Discord RPC Clear Error: {e}")
            # Jika gagal (misal Discord tertutup), set koneksi ke False
            rpc_connected = False

def get_cached_anilist_info(
    anime_name
):

    cache_file = os.path.join(
        METADATA_CACHE,
        f"{anime_name}.json"
    )
    info = None

    # 1. Coba muat dari cache metadata
    if os.path.exists(
        cache_file
    ):
        try:
            with open(
                cache_file,
                "r",
                encoding="utf-8"
            ) as f:
                info = json.load(f)
                # Jika metadata ditemukan tapi tidak punya informasi karakter atau relations, paksa ambil ulang
                if "characters" not in info or "relations" not in info or "recommendations" not in info:
                    info = None
        except:
            info = None

    # 2. Jika tidak ada di cache atau data tidak lengkap, ambil dari AniList API
    if not info:
        info = get_anilist_info(anime_name)
        if info:
            with open(
                cache_file,
                "w",
                encoding="utf-8"
            ) as f:
                json.dump(
                    info,
                    f,
                    ensure_ascii=False,
                    indent=4
                )

    # 3. Proses gambar karakter dan poster relations (Harus dijalankan meskipun metadata sudah ada di cache)
    if info:
        # Download Gambar Karakter & Seiyuu
        if "characters" in info:
            safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
            anime_char_dir = os.path.join(CHARACTER_CACHE, safe_anime)
            
            # Pastikan folder karakter ada (terutama jika baru dihapus)
            os.makedirs(anime_char_dir, exist_ok=True)
            
            for char in info["characters"]:
                # Gambar Karakter
                if char.get("image_url"):
                    clean_name = re.sub(r'[<>:"/\\|?*]', '_', char['name'])
                    fname = f"{clean_name}_char.jpg"
                    fpath = os.path.join(anime_char_dir, fname)
                    # Unduh jika file belum ada
                    if not os.path.exists(fpath):
                        try:
                            r = requests.get(char["image_url"], timeout=15)
                            if r.status_code == 200:
                                with open(fpath, "wb") as f_img:
                                    f_img.write(r.content)
                        except: pass
                    char["image_local"] = fname
                
                # Gambar Seiyuu
                if char.get("va_image_url") and char.get("va_name"):
                    clean_va = re.sub(r'[<>:"/\\|?*]', '_', char['va_name'])
                    fname = f"{clean_va}_va.jpg"
                    fpath = os.path.join(anime_char_dir, fname)
                    # Unduh jika file belum ada
                    if not os.path.exists(fpath):
                        try:
                            r = requests.get(char["va_image_url"], timeout=15)
                            if r.status_code == 200:
                                with open(fpath, "wb") as f_img:
                                    f_img.write(r.content)
                        except: pass
                    char["va_image_local"] = fname

        # Download Poster Relations ke Cache
        if "relations" in info:
            for rel in info["relations"]:
                rel_title = rel.get("title")
                rel_poster_url = rel.get("poster")
                if rel_title and rel_poster_url and rel_poster_url.startswith("http"):
                    safe_rel_title = re.sub(r'[<>:"/\\|?*]', '_', rel_title)
                    rel_poster_path = os.path.join(POSTER_CACHE, f"{safe_rel_title}.jpg")
                    if not os.path.exists(rel_poster_path):
                        try:
                            r = requests.get(rel_poster_url, timeout=15)
                            if r.status_code == 200:
                                with open(rel_poster_path, "wb") as f:
                                    f.write(r.content)
                        except: pass

        # Download Poster Recommendations ke Cache
        if "recommendations" in info:
            for rec in info["recommendations"]:
                rec_title = rec.get("title")
                rec_poster_url = rec.get("poster")
                if rec_title and rec_poster_url and rec_poster_url.startswith("http"):
                    safe_rec_title = re.sub(r'[<>:"/\\|?*]', '_', rec_title)
                    rec_poster_path = os.path.join(POSTER_CACHE, f"{safe_rec_title}.jpg")
                    if not os.path.exists(rec_poster_path):
                        try:
                            r = requests.get(rec_poster_url, timeout=15)
                            if r.status_code == 200:
                                with open(rec_poster_path, "wb") as f:
                                    f.write(r.content)
                        except: pass

    return info

def get_subtitle_vtt_path(anime_name, episode_path):
    # Membuat nama folder dan file yang aman untuk sistem file Windows
    safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    safe_episode = re.sub(r'[<>:"|?*]', '_', episode_path).replace('/', '_').replace('\\', '_')
    
    vtt_filename = f"{safe_episode}.vtt"
    return os.path.join(SUBTITLE_CACHE, safe_anime, vtt_filename)

def generate_subtitle_vtt(video_path, vtt_path):
    print(f"Creating subtitle: {video_path}")
    
    # Pastikan sub-direktori anime di dalam cache tersedia
    os.makedirs(os.path.dirname(vtt_path), exist_ok=True)
    
    try:
        # Ekstraksi subtitle stream pertama (0:s:0) langsung ke format WebVTT menggunakan FFmpeg
        result = subprocess.run([
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-map", "0:s:0",
            vtt_path
        ], capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(vtt_path):
            # Pembersihan konten VTT dari tag SSA/ASS dan drawing commands
            try:
                with open(vtt_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                cleaned_lines = ["WEBVTT\n"]
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if "-->" in line:
                        timestamp = line
                        cue_text = []
                        i += 1
                        # Ambil semua baris teks milik cue ini
                        while i < len(lines) and lines[i].strip() != "" and "-->" not in lines[i]:
                            text_line = lines[i].strip()
                            # 1. Hapus tag SSA/ASS seperti {\an7\pos...}
                            text_line = re.sub(r'\{.*?\}', '', text_line)
                            # 2. Hapus drawing commands (koordinat seperti m 0 0 l ...)
                            # Kita hapus tag HTML sementara untuk validasi konten murni
                            plain_text = re.sub(r'<[^>]*>', '', text_line).strip()
                            # Deteksi instruksi gambar ASS (m, l, c, b, p dan angka)
                            is_drawing = bool(re.match(r'^[mlcbp\s\d.-]+$', plain_text, re.I)) and any(c.isdigit() for c in plain_text)
                            
                            if not is_drawing and plain_text != "":
                                cue_text.append(text_line)
                            i += 1
                        
                        # Simpan cue hanya jika ada teks bersih yang tersisa
                        if cue_text:
                            cleaned_lines.append("\n" + timestamp + "\n")
                            cleaned_lines.append("\n".join(cue_text) + "\n")
                    else:
                        i += 1

                with open(vtt_path, "w", encoding="utf-8") as f:
                    f.writelines(cleaned_lines)
            except Exception as clean_err:
                print(f"Warning: unable to clean subtitle: {clean_err}")

            print(f"Subtitle created and cleaned successfully: {vtt_path}")
            return True
        else:
            print(f"Subtitle was not created (subtitle stream may be unavailable): {video_path}")
            return False
    except Exception as e:
        print(f"Error while creating subtitle: {e}")
        return False

def get_watch_backup_file(path):
    return f"{path}.bak"

def get_corrupt_watch_file(path):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    corrupt_path = f"{path}.corrupt-{timestamp}"
    index = 1

    while os.path.exists(corrupt_path):
        corrupt_path = f"{path}.corrupt-{timestamp}-{index}"
        index += 1

    return corrupt_path

def read_json_dict_file(path, label):
    if not os.path.exists(path):
        return None, "missing"

    try:
        if os.path.getsize(path) == 0:
            return None, "empty"

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    except OSError as e:
        return None, f"OS error: {e}"

    if not isinstance(data, dict):
        return None, "JSON root is not an object"

    return data, None

def atomic_write_json_file(path, data, label):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    tmp_path = os.path.join(
        os.path.dirname(path),
        f".{os.path.basename(path)}.tmp-{os.getpid()}-{threading.get_ident()}"
    )

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        print(f"{label} saved atomically: {path}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def preserve_corrupt_watch_file(path, label, reason):
    if not os.path.exists(path):
        return

    corrupt_path = get_corrupt_watch_file(path)

    try:
        os.replace(path, corrupt_path)
        print(f"{label} preserved as corrupt file: {corrupt_path} ({reason})")
    except OSError as e:
        print(f"{label} could not preserve corrupt file {path}: {e}")

def load_watch_json_file(path, label):
    with WATCH_DATA_LOCK:
        data, error = read_json_dict_file(path, label)

        if error is None:
            return data

        backup_path = get_watch_backup_file(path)

        if error == "missing":
            print(f"{label} file missing, checking backup: {backup_path}")
        else:
            print(f"{label} file could not be loaded ({error}), checking backup.")
            preserve_corrupt_watch_file(path, label, error)

        backup_data, backup_error = read_json_dict_file(backup_path, f"{label} backup")

        if backup_error is None:
            print(f"{label} recovered from backup: {backup_path}")
            try:
                atomic_write_json_file(path, backup_data, label)
            except OSError as e:
                print(f"{label} recovery loaded backup but could not restore main file: {e}")
            return backup_data

        print(f"{label} backup unavailable or invalid ({backup_error}); using empty data.")
        return {}

def save_watch_json_file(path, data, label):
    if not isinstance(data, dict):
        raise ValueError(f"{label} data must be a dictionary")

    with WATCH_DATA_LOCK:
        atomic_write_json_file(path, data, label)

        backup_path = get_watch_backup_file(path)
        try:
            atomic_write_json_file(backup_path, data, f"{label} backup")
        except OSError as e:
            print(f"{label} saved, but backup write failed: {e}")

def save_watch_history(history):
    save_watch_json_file(WATCH_HISTORY_FILE, history, "Watch history")

MOVIE_HISTORY_PREFIX = "movie::"

def get_watch_history_key(anime_name, episode):
    if anime_name == "Movies":
        return f"{MOVIE_HISTORY_PREFIX}{episode}"

    return anime_name

def get_watch_history_display_name(anime_name, episode):
    if anime_name == "Movies":
        return clean_movie_title(episode)

    return anime_name

def get_continue_progress_percent(data):
    try:
        current_seconds = float(data.get("last_seconds", 0) or 0)
        duration = float(data.get("duration", 0) or 0)
    except (TypeError, ValueError):
        return 0

    if duration <= 0 or duration != duration or current_seconds != current_seconds:
        return 0

    progress = (current_seconds / duration) * 100
    return max(0, min(100, round(progress)))

def update_watch_history(
    history_key,
    episode,
    episode_num,
    time_str=None,
    last_seconds=0,
    duration=0,
    media_name=None,
    display_name=None
):
    with WATCH_DATA_LOCK:
        history = load_history_data()

        history[history_key] = {
            "episode": episode,
            "episode_num": episode_num,
            "updated_at": datetime.now().isoformat(),
            "time_str": time_str,
            "last_seconds": last_seconds,
            "duration": duration,
            "media_name": media_name or history_key,
            "display_name": display_name or history_key
        }

        save_watch_history(history)

def load_watch_status():
    return load_watch_json_file(WATCH_STATUS_FILE, "Watch status")

def save_watch_status(status_data):
    save_watch_json_file(WATCH_STATUS_FILE, status_data, "Watch status")

def get_episode_watch_status(anime_name, episode):
    status_data = load_watch_status()

    return (
        status_data
        .get(anime_name, {})
        .get(episode, {})
    )

def update_episode_watch_status(anime_name, episode, data):
    with WATCH_DATA_LOCK:
        status_data = load_watch_status()
        anime_status = status_data.setdefault(anime_name, {})
        episode_status = anime_status.setdefault(episode, {
            "watched": False,
            "progress": 0,
            "duration": 0
        })

        episode_status.setdefault("watched", False)
        episode_status.setdefault("progress", 0)
        episode_status.setdefault("duration", 0)
        episode_status.update(data or {})
        episode_status["updated_at"] = datetime.now().isoformat()

        save_watch_status(status_data)
        return episode_status

def mark_episode_watched(anime_name, episode):
    return update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": True,
            "progress": 100
        }
    )

def mark_episode_unwatched(anime_name, episode):
    return update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": False,
            "progress": 0
        }
    )

def sync_anime_to_db(anime_name):
    """Memindai satu folder anime dan memperbarui database SQLite.
       Fungsi ini dipanggil oleh background scanner."""
    anime_path = find_anime_path(anime_name)
    if not anime_path:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL") # Optimasi konkurensi
            conn.execute("DELETE FROM anime_library WHERE name = ?", (anime_name,))
        return

    episode_count = 0
    for root, dirs, files in os.walk(anime_path):
        for file in files:
            if file.lower().endswith(VIDEO_EXTENSIONS):
                episode_count += 1

    info = get_cached_anilist_info(anime_name)
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO anime_library (name, episodes, score, genres, year, season, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            anime_name,
            episode_count,
            info.get("score") if info else None,
            json.dumps(info.get("genres")) if info else None,
            info.get("year") if info else None,
            info.get("season") if info else None,
            info.get("status") if info else None
        ))

def sync_all_library():
    """Pemindaian penuh seluruh folder anime di latar belakang."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Syncing library...")
    found_data = []
    found_names = []
    for base_path in ANIME_PATHS:
        if not os.path.exists(base_path):
            continue
        for name in os.listdir(base_path):
            full_path = os.path.join(base_path, name)
            if os.path.isdir(full_path):
                found_names.append(name)
                
                # Hitung episode
                episode_count = 0
                for root, dirs, files in os.walk(full_path):
                    for file in files:
                        if file.lower().endswith(VIDEO_EXTENSIONS):
                            episode_count += 1
                
                # Cek cache metadata dulu tanpa sleep
                cache_file = os.path.join(METADATA_CACHE, f"{name}.json")
                if not os.path.exists(cache_file):
                    info = get_cached_anilist_info(name)
                    time.sleep(0.7) # Delay hanya jika hit API
                else:
                    info = get_cached_anilist_info(name)

                found_data.append((
                    name,
                    episode_count,
                    info.get("score") if info else None,
                    json.dumps(info.get("genres")) if info else None,
                    info.get("year") if info else None,
                    info.get("season") if info else None,
                    info.get("status") if info else None
                ))
    
    # Update database secara batch dalam satu koneksi
    with sqlite3.connect(DB_PATH) as conn:
        # Update/Insert data yang ditemukan
        if found_data:
            conn.executemany("""
                INSERT OR REPLACE INTO anime_library (name, episodes, score, genres, year, season, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, found_data)

        # Hapus entri di DB jika foldernya sudah tidak ada di disk
        if found_names:
            placeholders = ','.join(['?'] * len(found_names))
            conn.execute(f"DELETE FROM anime_library WHERE name NOT IN ({placeholders})", found_names)
        else:
            conn.execute("DELETE FROM anime_library")
            
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Sync complete. Detected {len(found_names)} anime.")

@app.route("/settings")
def settings_page():
    settings = load_settings()

    return render_template(
        "settings.html",
        settings=settings,
        saved=request.args.get("saved") == "1",
        cache_cleaned=request.args.get("cache_cleaned") == "1",
        cache_removed_files=request.args.get("removed_files", "0"),
        cache_removed_dirs=request.args.get("removed_dirs", "0"),
        cache_skipped=request.args.get("skipped", "0")
    )

@app.route("/settings", methods=["POST"])
def update_settings():
    settings = {
        "watchlist_path": request.form.get("watchlist_path", "").strip(),
        "ongoing_path": request.form.get("ongoing_path", "").strip(),
        "movie_path": request.form.get("movie_path", "").strip(),
        "vlc_path": request.form.get("vlc_path", "").strip(),
        "discord_rpc_enabled": "discord_rpc_enabled" in request.form,
        "theme_preset": request.form.get("theme_preset", "dark-blue").strip()
    }

    if settings["theme_preset"] not in THEME_PRESETS:
        settings["theme_preset"] = "dark-blue"

    save_settings(settings)
    apply_settings(settings)

    return redirect("/settings?saved=1")

@app.route("/settings/cleanup-cache", methods=["POST"])
def cleanup_cache_settings():
    summary = cleanup_orphan_cache()

    return redirect(
        "/settings?cache_cleaned=1"
        f"&removed_files={summary['removed_files']}"
        f"&removed_dirs={summary['removed_dirs']}"
        f"&skipped={summary['skipped']}"
    )

def pick_windows_path(picker_type):
    root = None

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()

        if picker_type == "file":
            selected_path = filedialog.askopenfilename(
                parent=root,
                filetypes=[
                    ("Executable files", "*.exe"),
                    ("All files", "*.*")
                ]
            )
        else:
            selected_path = filedialog.askdirectory(parent=root)

        return jsonify({"path": selected_path or ""})

    except Exception as e:
        return jsonify({
            "path": "",
            "error": str(e)
        })

    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

@app.route("/settings/pick-folder")
def pick_settings_folder():
    return pick_windows_path("folder")

@app.route("/settings/pick-file")
def pick_settings_file():
    return pick_windows_path("file")

def get_anime():
    """Mengambil daftar anime dari database SQLite (Instan)."""
    anime_list = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM anime_library ORDER BY name COLLATE NOCASE")
            for row in cursor:
                item = dict(row)
                anime_list.append({
                    "name": item["name"],
                    "episodes": item["episodes"],
                    "score": item["score"],
                    "year": item["year"],
                    "season": item["season"],
                    "status": item["status"]
                })
    except Exception as e:
            print(f"Database Query Error: {e}")
    return anime_list

def normalize_studio_name(studio_name):
    return " ".join((studio_name or "").casefold().split())

def normalize_anime_match_name(value):
    normalized = (value or "").casefold()
    normalized = re.sub(r"[\s:;,_'\"`´‘’“”()\[\]{}.!?\\/|+-]+", " ", normalized)
    return " ".join(normalized.split())

def get_cached_metadata_only(anime_name):
    cache_file = os.path.join(
        METADATA_CACHE,
        f"{anime_name}.json"
    )

    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None

def get_studio_project_cache_file(studio_name):
    return os.path.join(
        METADATA_CACHE,
        f"studio_projects_{safe_cache_name(studio_name)}.json"
    )

def read_studio_project_cache(studio_name, allow_stale=False):
    cache_file = get_studio_project_cache_file(studio_name)

    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(payload, dict):
        return None

    fetched_at = payload.get("fetched_at", 0)
    is_fresh = (time.time() - fetched_at) < STUDIO_PROJECT_CACHE_TTL_SECONDS

    if allow_stale or is_fresh:
        return payload.get("data")

    return None

def write_studio_project_cache(studio_name, data):
    os.makedirs(METADATA_CACHE, exist_ok=True)
    cache_file = get_studio_project_cache_file(studio_name)

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "fetched_at": time.time(),
                    "data": data
                },
                f,
                ensure_ascii=False,
                indent=4
            )
    except OSError as e:
        print(f"Studio project cache write failed for {studio_name}: {e}")

def fetch_anilist_studio_projects(studio_name, max_projects=STUDIO_PROJECT_LIMIT):
    cached = read_studio_project_cache(studio_name)
    if cached:
        return cached, None

    query = """
    query ($search: String, $page: Int, $perPage: Int) {
      Studio(search: $search) {
        id
        name
        isAnimationStudio
        media(isMain: true, sort: POPULARITY_DESC, page: $page, perPage: $perPage) {
          pageInfo {
            total
            currentPage
            lastPage
            hasNextPage
            perPage
          }
          nodes {
            id
            title {
              romaji
              english
              native
            }
            coverImage {
              extraLarge
            }
            format
            status
            season
            seasonYear
            episodes
            averageScore
            popularity
            description
          }
        }
      }
    }
    """

    projects = []
    studio_info = None
    page = 1
    per_page = min(50, max_projects)

    try:
        while len(projects) < max_projects:
            response = requests.post(
                "https://graphql.anilist.co",
                json={
                    "query": query,
                    "variables": {
                        "search": studio_name,
                        "page": page,
                        "perPage": min(per_page, max_projects - len(projects))
                    }
                },
                timeout=15
            )

            try:
                data = response.json()
            except ValueError:
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "AniList returned an invalid studio response."

            if response.status_code >= 400:
                print("Studio AniList HTTP Error:", response.status_code, data)
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, f"AniList studio request failed with HTTP {response.status_code}."

            errors = data.get("errors")
            if errors:
                print("Studio AniList GraphQL Errors:", errors)
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "AniList returned GraphQL errors for the studio request."

            studio_data = data.get("data", {}).get("Studio")
            if not studio_data:
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "Studio was not found on AniList."

            if studio_info is None:
                studio_info = {
                    "id": studio_data.get("id"),
                    "name": studio_data.get("name"),
                    "isAnimationStudio": studio_data.get("isAnimationStudio")
                }

            media_data = studio_data.get("media") or {}
            page_info = media_data.get("pageInfo") or {}
            projects.extend(media_data.get("nodes") or [])

            if not page_info.get("hasNextPage") or page >= page_info.get("lastPage", page):
                break

            page += 1

        payload = {
            "studio_info": studio_info,
            "projects": projects[:max_projects]
        }
        write_studio_project_cache(studio_name, payload)
        return payload, None

    except requests.RequestException as e:
        print(f"Studio AniList Request Error: {e}")
        stale = read_studio_project_cache(studio_name, allow_stale=True)
        return stale, "Unable to reach AniList studio API."

def build_local_anime_match_index(local_anime):
    match_index = {}

    for anime in local_anime:
        anime_name = anime.get("name")
        if not anime_name:
            continue

        names = {anime_name}
        info = get_cached_metadata_only(anime_name) or {}
        cached_title = info.get("title")
        if cached_title:
            names.add(cached_title)

        for name in names:
            normalized = normalize_anime_match_name(name)
            if normalized:
                match_index.setdefault(normalized, anime)

    return match_index

def get_project_title_options(project):
    title = project.get("title") or {}
    return [
        title.get("english"),
        title.get("romaji"),
        title.get("native")
    ]

def build_studio_project_item(project, local_match=None):
    title_options = get_project_title_options(project)
    display_title = next((title for title in title_options if title), "Untitled")
    title = project.get("title") or {}

    item = {
        "id": project.get("id"),
        "name": display_title,
        "title_romaji": title.get("romaji"),
        "title_english": title.get("english"),
        "title_native": title.get("native"),
        "poster": (project.get("coverImage") or {}).get("extraLarge") or url_for("static", filename="arcana.jpg"),
        "format": project.get("format"),
        "status": project.get("status"),
        "season": project.get("season"),
        "year": project.get("seasonYear"),
        "episodes": project.get("episodes"),
        "score": project.get("averageScore"),
        "popularity": project.get("popularity"),
        "description": project.get("description"),
        "in_library": local_match is not None,
        "local_anime_name": local_match.get("name") if local_match else None,
        "local_detail_url": url_for("anime_detail", anime_name=local_match.get("name")) if local_match else None
    }

    if item["in_library"]:
        item["name"] = item["local_anime_name"]
        item["poster"] = url_for("poster", anime_name=item["local_anime_name"])
        item["score"] = local_match.get("score") or item["score"]
        item["year"] = local_match.get("year") or item["year"]
        item["season"] = local_match.get("season") or item["season"]
        item["episodes"] = local_match.get("episodes") or item["episodes"]
        item["status"] = local_match.get("status") or item["status"]

    return item

def build_local_studio_fallback_projects(studio_name, local_anime, allow_fetch_missing=True):
    requested_studio = normalize_studio_name(studio_name)
    library_projects = []

    for anime in local_anime:
        anime_name = anime.get("name")
        if not anime_name:
            continue

        info = get_cached_metadata_only(anime_name)
        if info is None and allow_fetch_missing:
            info = get_cached_anilist_info(anime_name)

        anime_studio = (info or {}).get("studio")
        if normalize_studio_name(anime_studio) != requested_studio:
            continue

        library_projects.append({
            "id": None,
            "name": anime_name,
            "title_romaji": None,
            "title_english": anime_name,
            "title_native": None,
            "poster": url_for("poster", anime_name=anime_name),
            "format": (info or {}).get("format"),
            "status": anime.get("status"),
            "season": anime.get("season"),
            "year": anime.get("year"),
            "episodes": anime.get("episodes"),
            "score": anime.get("score"),
            "popularity": None,
            "description": (info or {}).get("description"),
            "in_library": True,
            "local_anime_name": anime_name,
            "local_detail_url": url_for("anime_detail", anime_name=anime_name)
        })

    return library_projects

@app.route("/about")
def about():
    return render_template("project-overview-v2.html")

@app.route("/schedule")
def schedule():
    local_tz = datetime.now().astimezone().tzinfo
    now_dt = datetime.now(local_tz)
    now_ts = int(now_dt.timestamp())
    timezone_offset_minutes = int(now_dt.utcoffset().total_seconds() // 60)
    airing_list, schedule_error = get_cached_airing_schedule()
    processed = build_schedule_items(airing_list, local_tz, now_ts)

    current_items = [
        item for item in processed
        if item["airing_at"] <= now_ts < item["airing_at"] + 1800
    ]
    upcoming_items = [
        item for item in processed
        if item["airing_at"] > now_ts
    ]
    schedule_focus = None

    if current_items:
        schedule_focus = dict(current_items[0])
        schedule_focus["focus_mode"] = "live"
        schedule_focus["focus_badge"] = "LIVE NOW"
        schedule_focus["focus_status"] = "Airing now"
        schedule_focus["more_count"] = max(0, len(current_items) - 1)
    elif upcoming_items:
        schedule_focus = dict(upcoming_items[0])
        schedule_focus["focus_mode"] = "next"
        schedule_focus["focus_badge"] = "NEXT UP"
        schedule_focus["focus_status"] = "Upcoming"
        schedule_focus["more_count"] = 0
    
    return render_template(
        "schedule.html",
        schedule=processed,
        schedule_focus=schedule_focus,
        schedule_error=schedule_error,
        now_ts=now_ts,
        now_iso=now_dt.isoformat(),
        timezone_offset_minutes=timezone_offset_minutes,
        today=now_dt.strftime("%A, %d %B %Y")
    )

@app.route("/api/schedule-alerts")
def schedule_alerts():
    local_tz = datetime.now().astimezone().tzinfo
    now_dt = datetime.now(local_tz)
    now_ts = int(now_dt.timestamp())
    timezone_offset_minutes = int(now_dt.utcoffset().total_seconds() // 60)
    airing_list, schedule_error = get_cached_airing_schedule()

    if schedule_error:
        return jsonify({
            "ok": False,
            "error": schedule_error,
            "items": [],
            "badge_count": 0,
            "summary": "Schedule unavailable",
            "now_iso": now_dt.isoformat(),
            "timezone_offset_minutes": timezone_offset_minutes
        }), 502

    processed = build_schedule_items(airing_list, local_tz, now_ts)
    payload = get_schedule_alert_payload(
        processed,
        now_ts,
        now_dt.isoformat(),
        timezone_offset_minutes
    )
    payload["ok"] = True
    return jsonify(payload)

@app.route("/studio/<path:studio_name>")
def studio_page(studio_name):
    local_anime = get_anime()
    local_match_index = build_local_anime_match_index(local_anime)
    studio_payload, studio_error = fetch_anilist_studio_projects(studio_name)
    studio_info = None
    studio_projects = []
    fallback_used = False

    if studio_payload:
        studio_info = studio_payload.get("studio_info")

        for project in studio_payload.get("projects", []):
            local_match = None

            for title in get_project_title_options(project):
                normalized_title = normalize_anime_match_name(title)
                if normalized_title and normalized_title in local_match_index:
                    local_match = local_match_index[normalized_title]
                    break

            studio_projects.append(
                build_studio_project_item(
                    project,
                    local_match
                )
            )

    if not studio_payload:
        fallback_used = True
        studio_projects = build_local_studio_fallback_projects(
            studio_name,
            local_anime,
            allow_fetch_missing=True
        )

    library_projects = [
        project for project in studio_projects
        if project.get("in_library")
    ]

    # Compute additional profile statistics
    from collections import Counter
    years = [p.get("year") for p in studio_projects if p.get("year") and p.get("year") != 0]
    earliest_year = min(years) if years else None
    newest_year = max(years) if years else None

    formats = [p.get("format") for p in studio_projects if p.get("format")]
    format_counts = Counter(formats)
    clean_doms = []
    for fmt, count in format_counts.most_common(2):
        fmt_upper = fmt.upper()
        if fmt_upper in ("TV", "OVA", "ONA"):
            clean_doms.append(fmt_upper)
        else:
            clean_doms.append(fmt_upper.title())
    dominant_formats_str = ", ".join(clean_doms) if clean_doms else "N/A"

    # Group projects by year
    by_year = {}
    for p in studio_projects:
        year = p.get("year")
        y_key = year if (year and year != 0) else 0
        by_year.setdefault(y_key, []).append(p)

    # Sort years descending, keeping TBA (0) at the end
    sorted_years = sorted([yk for yk in by_year.keys() if yk > 0], reverse=True)
    if 0 in by_year:
        sorted_years.append(0)

    projects_by_year = [(yk, by_year[yk]) for yk in sorted_years]

    return render_template(
        "studio.html",
        studio_name=studio_name,
        studio_info=studio_info,
        studio_projects=studio_projects,
        projects_by_year=projects_by_year,
        library_projects=library_projects,
        total_projects=len(studio_projects),
        total_in_library=len(library_projects),
        studio_error=studio_error,
        fallback_used=fallback_used,
        studio_anime=library_projects,
        total_anime=len(library_projects),
        earliest_year=earliest_year,
        newest_year=newest_year,
        dominant_formats=dominant_formats_str
    )

@app.route("/movies")
def movies():

    movie_path = MOVIE_PATH

    movies = []

    if not os.path.isdir(
        movie_path
    ):

        return render_template(
            "movies.html",
            movies=movies
        )

    for file in os.listdir(movie_path):

        if file.lower().endswith(
            VIDEO_EXTENSIONS
        ):
            
            clean_title = clean_movie_title(file)

            movie_info = get_cached_anilist_info(
            clean_title
            )

            movies.append({

                "title": clean_title,

                "file": file,

                "poster":
                    f"/poster/{clean_title}",

                "score":
                    movie_info.get(
                        "score"
                    ) if movie_info else None,

                "year":
                    movie_info.get(
                        "year"
                    ) if movie_info else None,

                "description":
                    movie_info.get(
                        "description"
                    ) if movie_info else None
            })

    return render_template(
        "movies.html",
        movies=movies
    )

@app.route("/movie/<path:filename>")
def movie_detail_page(filename):

    video_path = safe_join_media_path(
        MOVIE_PATH,
        filename
    )

    if (
        not video_path
        or
        not os.path.isfile(video_path)
        or
        not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return "Movie not found", 404

    clean_title = clean_movie_title(filename)
    anime_info = get_cached_anilist_info(clean_title)

    # Ambil durasi dan resolusi
    episode_info = get_episode_cache("Movies", video_path)

    # Buat list episode buatan (hanya 1 item)
    episodes = [{
        "file": filename,
        "episode": 1,
        "thumbnail": f"/thumbnail/Movies/{filename}",
        "duration": episode_info["duration"],
        "resolution": episode_info["resolution"]
    }]

    return render_template(
        "anime.html",
        anime_name=clean_title,      # Digunakan untuk metadata/poster
        folder_name="Movies",       # Digunakan untuk mencari file di disk
        episodes=episodes,
        anime_info=anime_info
    )

@app.route("/")
def index():

    status_filter = request.args.get(
        "status",
        "ALL"
    )

    anime_list = get_anime()

    all_count = len(anime_list)

    releasing_count = len([
        anime
        for anime in anime_list
        if anime.get("status") == "RELEASING"
    ])

    finished_count = len([
        anime
        for anime in anime_list
        if anime.get("status") == "FINISHED"
    ])

    featured_slides = []
    if anime_list:
        # Ambil maksimal 5 anime acak untuk slider
        slider_candidates = random.sample(
            anime_list, 
            min(len(anime_list), 5)
        )
        for item in slider_candidates:
            info = get_cached_anilist_info(item["name"])
            featured_slides.append({
                "anime": item,
                "info": info
            })

    # Helper function to get history could be used here
    history = load_history_data()
    
    continue_watching = []
    for history_key, data in history.items():
        if not isinstance(data, dict):
            continue

        episode = data.get("episode")
        if not episode:
            continue

        is_movie = (
            history_key.startswith(MOVIE_HISTORY_PREFIX)
            or history_key == "Movies"
            or data.get("media_name") == "Movies"
        )

        media_name = "Movies" if is_movie else data.get("media_name", history_key)
        display_name = (
            data.get("display_name")
            or get_watch_history_display_name(media_name, episode)
        )

        continue_watching.append({
            "name": media_name,
            "display_name": display_name,
            "is_movie": is_movie,
            "episode": episode,
            "episode_num": data.get("episode_num"),
            "time_str": data.get("time_str"), # Ambil time_str dari history
            "updated_at": data.get("updated_at", ""),
            "image_url": url_for(
                "thumbnail",
                anime_name="Movies",
                episode=episode
            ) if is_movie else url_for(
                "banner",
                anime_name=media_name
            ),
            "progress_percent": get_continue_progress_percent(data)
        })
    
    continue_watching.sort(key=lambda x: x["updated_at"], reverse=True)
    continue_watching = continue_watching[:6]

    return render_template(
        "index.html",
        anime_list=anime_list,
        featured_slides=featured_slides,
        continue_watching=continue_watching,
        status_filter=status_filter,

        all_count=all_count,
        releasing_count=releasing_count,
        finished_count=finished_count
    )


@app.route("/poster/<path:anime_name>")
def poster(anime_name):

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return send_file(
            poster_path
        )

    poster_path = get_anilist_poster(
        anime_name
    )

    if poster_path:
        return send_file(
            poster_path
        )

    return "", 404

@app.route("/banner/<anime_name>")
def banner(anime_name):

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    banner_path = os.path.join(
        BANNER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        banner_path
    ):
        return send_file(
            banner_path
        )

    get_anilist_poster(
        anime_name
    )

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    if os.path.exists(
        banner_path
    ):
        return send_file(
            banner_path
        )

    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return send_file(
            poster_path
        )

    return "", 404

@app.route("/thumbnail/<anime_name>/<path:episode>")
def thumbnail(
    anime_name,
    episode
):

    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return "", 404

    episode_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not episode_path
        or
        not os.path.isfile(episode_path)
        or
        not episode_path.lower().endswith(VIDEO_EXTENSIONS)
    ):

        return "", 404

    thumbnail_path = get_thumbnail(
        episode_path
    )

    if thumbnail_path:
        return send_file(
            thumbnail_path
        )

    return "", 404

@app.route("/anime/<anime_name>")
def anime_detail(anime_name):

    anime_path = find_anime_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found"

    has_season = False

    for item in os.listdir(
        anime_path
    ):

        item_path = os.path.join(
            anime_path,
            item
        )

        if os.path.isdir(
            item_path
        ):

            has_season = True
            break

    if has_season:

        return redirect(
            f"/anime/{anime_name}/seasons"
        )

    episodes = []

    video_files = []

    for file in os.listdir(
        anime_path
    ):

        if file.lower().endswith(
            VIDEO_EXTENSIONS
        ):

            video_files.append(
                file
            )

    video_files.sort(
        key=get_episode_number
    )

    for index, file in enumerate(
        video_files,
        start=1
    ):

        video_path = os.path.join(
            anime_path,
            file
        )

        episode_info = get_episode_cache(
            anime_name,
            video_path
        )

        watch_status = get_episode_watch_status(
            anime_name,
            file
        )

        episodes.append({

            "file": file,

            "episode": index,

            "thumbnail":
                f"/thumbnail/{anime_name}/{file}",

            "duration":
                episode_info[
                    "duration"
                ],

            "resolution":
                episode_info[
                    "resolution"
                ],

            "watched":
                watch_status.get(
                    "watched",
                    False
                ),

            "progress":
                watch_status.get(
                    "progress",
                    0
                )

        })

    anime_info = get_cached_anilist_info(
        anime_name
    )

    print("ANIME INFO =", anime_info)

    return render_template(
        "anime.html",
        anime_name=anime_name,
        folder_name=anime_name,
        episodes=episodes,
        anime_info=anime_info
    )


@app.route(
    "/player/<anime_name>/<path:episode>"
)
def player(
    anime_name,
    episode
):

    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found"

    current_video_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not current_video_path
        or
        not os.path.isfile(current_video_path)
        or
        not current_video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):

        abort(404)

    episode_dir = os.path.dirname(
        current_video_path
    )

    try:

        season_name = os.path.relpath(
            episode_dir,
            anime_path
        )

    except ValueError:

        abort(404)

    if season_name == ".":

        season_name = None

    video_files = []

    for file in os.listdir(
        episode_dir
    ):

        if file.lower().endswith(
            VIDEO_EXTENSIONS
        ):

            video_files.append(
                file
            )

    video_files.sort(
        key=get_episode_number
    )

    episodes = []

    for index, file in enumerate(
        video_files,
        start=1
    ):

        video_path = os.path.join(
            episode_dir,
            file
        )

        relative_file = file

        if season_name:

            relative_file = os.path.join(
                season_name,
                file
            )

        relative_url = relative_file.replace(
            os.sep,
            "/"
        )

        episode_info = get_episode_cache(
            anime_name,
            video_path,
            season_name
        )

        watch_status = get_episode_watch_status(
            anime_name,
            relative_url
        )

        episodes.append({

            "file": relative_url,

            "episode": index,

            "thumbnail":
                f"/thumbnail/{anime_name}/{relative_url}",

            "duration":
                episode_info[
                    "duration"
                ],

            "resolution":
                episode_info[
                    "resolution"
                ],

            "watched":
                watch_status.get(
                    "watched",
                    False
                ),

            "progress":
                watch_status.get(
                    "progress",
                    0
                )

        })

    current_index = 0

    current_file = os.path.basename(
        current_video_path
    )

    for i, file in enumerate(
        video_files
    ):

        if file == current_file:

            current_index = i
            break

    previous_episode = None
    next_episode = None

    if current_index > 0:

        previous_episode = (
            video_files[
                current_index - 1
            ]
        )

        if season_name:

            previous_episode = os.path.join(
                season_name,
                previous_episode
            ).replace(
                os.sep,
                "/"
            )

    if current_index < len(video_files) - 1:

        next_episode = (
            video_files[
                current_index + 1
            ]
        )

        if season_name:

            next_episode = os.path.join(
                season_name,
                next_episode
            ).replace(
                os.sep,
                "/"
            )

    if anime_name == "Movies":
        back_url = url_for(
            "movie_detail_page",
            filename=episode
        )
    else:
        back_url = url_for(
            "anime_detail",
            anime_name=anime_name
        )

    if season_name and anime_name != "Movies":

        season_url = season_name.replace(
            os.sep,
            "/"
        )

        back_url = url_for(
            "season_detail",
            anime_name=anime_name,
            season_name=season_url
        )

    watch_history_key = get_watch_history_key(
        anime_name,
        episode
    )
    watch_display_name = get_watch_history_display_name(
        anime_name,
        episode
    )

    # Ambil waktu tonton terakhir untuk fitur resume
    history = load_history_data()
    resume_time = 0
    h_data = history.get(watch_history_key)
    if not h_data and anime_name == "Movies":
        legacy_data = history.get("Movies")
        if legacy_data and legacy_data.get("episode") == episode:
            h_data = legacy_data

    if h_data:
        # Hanya resume jika episode yang dibuka sama dengan yang terakhir ditonton
        if h_data.get("episode") == episode:
            resume_time = h_data.get("last_seconds", 0)

    return render_template(

        "player.html",

        anime_name=anime_name,

        episode=episode,

        season_name=season_name,

        back_url=back_url,

        current_episode=
            current_index + 1,

        total_episodes=
            len(video_files),

        previous_episode=
            previous_episode,

        next_episode=
            next_episode,
            
        resume_time=resume_time,

        watch_history_key=watch_history_key,

        watch_display_name=watch_display_name,
        
        episodes=episodes

    )

@app.route(
    "/stream/<anime_name>/<path:episode>"
)
def stream_video(
    anime_name,
    episode
):

    anime_path = find_media_path(
        anime_name
    )

    video_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not video_path
        or
        not os.path.isfile(video_path)
        or
        not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        print(f"DEBUG: Video path invalid or not found: {video_path}")
        abort(404)

    # Kembali menggunakan send_file untuk mendukung Range Requests (Seeking & Duration)
    # Kita set mimetype secara manual untuk mengelabui browser agar mencoba memutar .mkv sebagai mp4
    mime_type = "video/mp4"
    if not video_path.lower().endswith('.mkv'):
        mime_type = mimetypes.guess_type(video_path)[0] or "video/mp4"

    return send_file(
        video_path,
        mimetype=mime_type,
        conditional=True  # Penting: Mengaktifkan dukungan navigasi/seeking
    )

@app.route("/character_img/<anime_name>/<filename>")
def character_img(anime_name, filename):
    safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    img_path = os.path.join(CHARACTER_CACHE, safe_anime, filename)
    if os.path.exists(img_path):
        return send_file(img_path)
    abort(404)

@app.route("/subtitle/<anime_name>/<path:episode>")
def get_subtitle(anime_name, episode):
    anime_path = find_media_path(anime_name)
    if not anime_path:
        abort(404)
        
    video_path = safe_join_media_path(anime_path, episode)
    if not video_path or not os.path.isfile(video_path):
        abort(404)
        
    vtt_path = get_subtitle_vtt_path(anime_name, episode)
    
    # 1. Cek apakah cache VTT sudah ada
    if os.path.exists(vtt_path):
        print(f"Subtitle cache ditemukan: {vtt_path}")
        return send_file(vtt_path, mimetype="text/vtt")
        
    # 2. Jika tidak ada, buat menggunakan FFmpeg
    if generate_subtitle_vtt(video_path, vtt_path):
        return send_file(vtt_path, mimetype="text/vtt")
        
    # 3. Jika gagal/tidak ada subtitle, abaikan (404 tidak akan menghentikan video)
    abort(404)

@app.route("/anime/<anime_name>/seasons")
def season_list(anime_name):

    anime_path = find_anime_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found"

    seasons = []

    for item in os.listdir(anime_path):

        season_path = os.path.join(
            anime_path,
            item
        )

        if os.path.isdir(
            season_path
        ):

            episode_count = 0

            for file in os.listdir(
                season_path
            ):

                if file.lower().endswith(
                    VIDEO_EXTENSIONS
                ):

                    episode_count += 1

            info = get_season_anilist_info(
                anime_name,
                item
            )

            seasons.append({

                "name": item,

                "title":
                    info.get("title")
                    if info else item,

                "year":
                    info.get("year")
                    if info else None,

                "season":
                    info.get("season")
                    if info else None,

                "score":
                    info.get("score")
                    if info else None,

                "status":
                    info.get("status")
                    if info else None,

                "episodes": episode_count
            })

    seasons.sort(
        key=lambda x: get_episode_number(
            x["name"]
        )
    )

    return render_template(
        "seasons.html",
        anime_name=anime_name,
        seasons=seasons
    )

@app.route("/anime/<anime_name>/<season_name>")
def season_detail(
    anime_name,
    season_name
):

    anime_path = find_anime_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found"

    season_path = safe_join_media_path(
        anime_path,
        season_name
    )

    if (
        not season_path
        or
        not os.path.isdir(
        season_path
        )
    ):
        return "Season not found"

    episodes = []

    video_files = []

    for file in os.listdir(
        season_path
    ):

        if file.lower().endswith(
            VIDEO_EXTENSIONS
        ):

            video_files.append(
                file
            )

    video_files.sort(
        key=get_episode_number
    )

    for index, file in enumerate(
        video_files,
        start=1
    ):

        video_path = os.path.join(
            season_path,
            file
        )

        episode_info = get_episode_cache(
            anime_name,
            video_path,
            season_name
        )

        relative_file = os.path.join(
            season_name,
            file
        ).replace(
            os.sep,
            "/"
        )

        watch_status = get_episode_watch_status(
            anime_name,
            relative_file
        )

        episodes.append({

            "file": relative_file,

            "episode": index,

            "thumbnail":
                f"/thumbnail/{anime_name}/{relative_file}",

            "duration":
                episode_info[
                    "duration"
                ],

            "resolution":
                episode_info[
                    "resolution"
                ],

            "watched":
                watch_status.get(
                    "watched",
                    False
                ),

            "progress":
                watch_status.get(
                    "progress",
                    0
                )
        })

    anime_info = get_season_anilist_info(
        anime_name,
        season_name
    )

    return render_template(
        "anime.html",
        anime_name=anime_name,
        season_name=season_name,
        folder_name=anime_name,
        episodes=episodes,
        anime_info=anime_info
    )

@app.route("/play/<anime_name>/<path:episode>")
def play_episode(anime_name, episode):

    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return jsonify({
            "status": "anime_not_found"
        })

    episode_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not episode_path
        or
        not os.path.isfile(episode_path)
        or
        not episode_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return jsonify({
            "status": "episode_not_found"
        })

    try:

        subprocess.Popen([
            VLC_PATH,
            episode_path
        ])

        # Update history for VLC
        episode_num = get_episode_number(os.path.basename(episode))
        update_watch_history(
            get_watch_history_key(anime_name, episode),
            episode,
            episode_num,
            media_name=anime_name,
            display_name=get_watch_history_display_name(anime_name, episode)
        )
        update_discord_rpc(anime_name, episode_num)

        return jsonify({
            "status": "playing"
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        })

@app.route("/screenshot", methods=["POST"])
def save_screenshot():
    data = request.get_json()
    img_data = data.get("image")
    if not img_data:
        return jsonify({"status": "error", "message": "No image data"}), 400

    try:
        # Menghapus header data URL (data:image/png;base64,)
        header, encoded = img_data.split(",", 1)
        binary_data = base64.b64decode(encoded)

        now = datetime.now()
        # Format milidetik 3 digit
        ms = str(now.microsecond // 1000).zfill(3)
        filename = now.strftime("vlcsnap-%Y-%m-%d-%Hh%Mm%Ss") + ms + ".png"
        
        # Menggunakan folder Pictures user yang sedang aktif agar lebih dinamis
        save_path = os.path.join(os.path.expanduser("~"), "Pictures")
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        
        full_path = os.path.join(save_path, filename)
        with open(full_path, "wb") as f:
            f.write(binary_data)
        
        return jsonify({"status": "success", "path": full_path})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/update_progress", methods=["POST"])
def update_progress():
    data = request.get_json()
    anime_name = data.get("anime_name")
    episode = data.get("episode")
    episode_num = int(data.get("episode_num", 0))
    time_str = data.get("time_str")
    last_seconds = data.get("last_seconds", 0)
    duration = data.get("duration", 0)
    
    if anime_name and episode:
        update_watch_history(
            get_watch_history_key(anime_name, episode),
            episode,
            episode_num,
            time_str,
            last_seconds,
            duration,
            media_name=anime_name,
            display_name=get_watch_history_display_name(anime_name, episode)
        )
        update_discord_rpc(anime_name, episode_num, time_str)
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

def get_watch_status_payload():
    data = request.get_json(silent=True) or {}
    anime_name = data.get("anime_name")
    episode = data.get("episode")

    if not anime_name or not episode:
        return None, None, data

    return anime_name, episode, data

@app.route("/api/watch-status/mark-watched", methods=["POST"])
def api_mark_episode_watched():
    anime_name, episode, _ = get_watch_status_payload()

    if not anime_name or not episode:
        return jsonify({
            "ok": False,
            "error": "Missing anime_name or episode"
        }), 400

    mark_episode_watched(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": True
    })

@app.route("/api/watch-status/mark-unwatched", methods=["POST"])
def api_mark_episode_unwatched():
    anime_name, episode, _ = get_watch_status_payload()

    if not anime_name or not episode:
        return jsonify({
            "ok": False,
            "error": "Missing anime_name or episode"
        }), 400

    mark_episode_unwatched(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": False
    })

@app.route("/api/watch-status/progress", methods=["POST"])
def api_update_watch_status_progress():
    anime_name, episode, data = get_watch_status_payload()

    if not anime_name or not episode:
        return jsonify({
            "ok": False,
            "error": "Missing anime_name or episode"
        }), 400

    current_status = get_episode_watch_status(anime_name, episode)

    try:
        progress = float(data.get("progress", 0))
    except (TypeError, ValueError):
        progress = 0

    try:
        duration = float(data.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0

    try:
        current_seconds = float(data.get("current_seconds", 0))
    except (TypeError, ValueError):
        current_seconds = 0

    progress = max(0, min(100, progress))
    watched = True if progress >= 90 else bool(current_status.get("watched", False))

    status = update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": watched,
            "progress": progress,
            "duration": duration,
            "current_seconds": current_seconds
        }
    )

    return jsonify({
        "ok": True,
        "watched": status.get("watched", False),
        "progress": status.get("progress", 0)
    })

@app.route("/clear_rpc", methods=["POST"])
def clear_rpc_route():
    """Endpoint untuk menghapus status Discord secara manual."""
    clear_discord_rpc()
    return jsonify({"status": "success"})

@app.route('/favicon.ico')
def favicon():
    return send_file(
        os.path.join(app.root_path, 'static', 'arcana.jpg'),
        mimetype='image/jpeg'
    )

def load_history_data():
    return load_watch_json_file(WATCH_HISTORY_FILE, "Watch history")

class LibraryHandler(FileSystemEventHandler):
    def process_event(self, event_path):
        for base in ANIME_PATHS:
            if event_path.startswith(base):
                try:
                    relative = os.path.relpath(event_path, base)
                    parts = relative.split(os.sep)
                    if parts and parts[0] != "." and parts[0] != "":
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Watchdog event for anime: {parts[0]}. Triggering sync_anime_to_db.")
                        sync_anime_to_db(parts[0])
                        return # Found the anime, no need to check other base paths
                except ValueError: # path is not in base
                    continue
        # If no specific anime was found, or if it was a top-level directory change/deletion
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Watchdog event for {event_path} did not resolve to a specific anime. Triggering full sync.")
        sync_all_library()

    def on_created(self, event): self.process_event(event.src_path)
    def on_deleted(self, event): self.process_event(event.src_path)
    def on_moved(self, event):
        self.process_event(event.src_path) # Old path might be a deletion
        self.process_event(event.dest_path) # New path might be a creation/modification
    def on_modified(self, event):
        # Proses jika ada perubahan pada folder atau file video
        if event.is_directory:
            self.process_event(event.src_path)
        elif any(event.src_path.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
            self.process_event(event.src_path)

def start_scanner():
    """Menjalankan sinkronisasi awal dan memulai observer watchdog."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Performing initial full library sync...")
    sync_all_library() # Initial full sync
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Initial full library sync complete.")

    observer = Observer()
    handler = LibraryHandler()
    for path in ANIME_PATHS:
        if os.path.exists(path):
            observer.schedule(handler, path, recursive=True)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Monitoring folder: {path}")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Warning: Configured ANIME_PATH does not exist: {path}")
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

def periodic_sync_task(interval_seconds=900): # Sync every 15 minutes
    """Melakukan sinkronisasi penuh secara berkala sebagai pengaman."""
    while True:
        time.sleep(interval_seconds)
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Performing periodic full library sync...")
            sync_all_library()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Periodic full library sync complete.")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Periodic sync failed: {e}")

if __name__ == "__main__":
    # Start background tasks if app.py is run directly
    # init_db() is called when app.py is imported, so no need to call it here again.
    scanner_thread = threading.Thread(target=start_scanner, daemon=True)
    scanner_thread.start()

    periodic_sync_thread = threading.Thread(target=periodic_sync_task, daemon=True)
    periodic_sync_thread.start()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
