import threading
import webbrowser
import sys
import os
import time
from pystray import Icon, Menu, MenuItem # type: ignore
from PIL import Image
from app import app, sync_all_library, start_scanner, periodic_sync_task

APP_HOST = "127.0.0.1"
APP_PORT = 5000
APP_URL = f"http://animearchive.local:{APP_PORT}"

def run_flask():
    """Menjalankan server Flask di background thread."""
    app.run(
        host=APP_HOST,
        port=APP_PORT,
        debug=False,
        use_reloader=False
    )

def open_anime_archive(icon, item):
    """Membuka dashboard Anzu Anime di browser."""
    webbrowser.open(APP_URL)

def refresh_library(icon, item):
    """Memicu sinkronisasi library secara manual dari Tray."""
    threading.Thread(target=sync_all_library, daemon=True).start()
    print("Manual library sync started.")

def quit_app(icon, item):
    """Menghentikan tray icon dan menutup aplikasi."""
    icon.stop()
    sys.exit()

if __name__ == "__main__":
    # 1. Jalankan Flask dalam daemon thread
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )
    flask_thread.start()

    # 1b. Jalankan Background Scanner
    scanner_thread = threading.Thread(
        target=start_scanner, # Now imported from app.py
        daemon=True
    )
    scanner_thread.start()

    # 1c. Jalankan Sinkronisasi Berkala (Fallback)
    sync_thread = threading.Thread(
        target=periodic_sync_task, # Now imported from app.py
        daemon=True
    )
    sync_thread.start()

    # 2. Load icon tray dari file logo asli
    logo_path = os.path.join(os.path.dirname(__file__), "static", "arcana.jpg")
    image = Image.open(logo_path)
    # Image akan otomatis di-resize oleh pystray jika diperlukan, namun kita bisa memastikan ukurannya

    # 3. Inisialisasi dan jalankan System Tray
    icon = Icon(
        "AnzuAnime",
        image,
        "Anzu Anime Server",
        menu=Menu(
            MenuItem("Open Dashboard", open_anime_archive, default=True),
            MenuItem("Refresh Library", refresh_library),
            MenuItem("Exit", quit_app)
        )
    )
    icon.run()
