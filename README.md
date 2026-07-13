# AniBase

AniBase turns your local anime folders into a private streaming library: browse posters, continue episodes, track progress, generate thumbnails/subtitles, and keep everything running quietly from a Windows tray app.

It is built for personal collections first. Your media stays on your machine, runtime data lives in your Windows user profile, and the web dashboard is served locally by the app.

## Preview

<div align="center">

<table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
<tr>
<td width="50%" style="padding: 12px; box-sizing: border-box;">

### Home
<img src="assets/screenshots/Home.jpg" alt="Home page" width="100%" style="border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">

</td>
<td width="50%" style="padding: 12px; box-sizing: border-box;">

### Anime Detail
<img src="assets/screenshots/Anime.png" alt="Anime detail page" width="100%" style="border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">

</td>
</tr>
<tr>
<td colspan="2" style="padding: 12px; box-sizing: border-box; text-align: center;">

### Player
<img src="assets/screenshots/Player.png" alt="Player" width="120%" style="border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">

</td>
</tr>
</table>

</div>

## Features

**Library**

* 📁 Scan local anime and movie folders.
* 🗂️ Organize titles into a browsable dashboard.
* ⏱️ Preserve watch history, watch status, and resume progress.
* 📥 Auto-import files into selected library folders.

**Metadata**

* 🔎 Fetch AniList metadata for posters, banners, genres, characters, studios, relations, and recommendations.
* 🧠 Cache poster, banner, metadata, character, seiyuu, thumbnail, subtitle, and episode data locally.
* 🏢 Show studio pages and airing schedules.

**Playback**

* ▶️ Stream episodes through the built-in web player.
* 💬 Support subtitles, generated VTT subtitle cache, thumbnails, auto next episode, and resume playback.
* 🎞️ Optionally open media in an external Media Player.

**Desktop**

* 🖥️ Modern PySide6 tray launcher.
* 🔒 Single-instance guard.
* 📴 Graceful shutdown for server, scanner, sync, watchdog, and auto-import workers.
* 🟣 Optional Discord Rich Presence.
* 🌐 LAN access controls and multiple theme presets.

## Requirements

* 🪟 Windows.
* 🌐 Internet connection for metadata.
* 🟣 Optional: Discord desktop app for Rich Presence.
* 🎬 Conditional: install **K-Lite Codec Pack Full** if a video opens as a
  blank screen or cannot be played in the browser player.

The Windows release already includes Python, FFmpeg, FFprobe, and all Python
dependencies. Python 3.12+ is only required when running from source or
building a release.

## Installation & Setup

Choose one path:

* 🚀 **Release** if you only want to use AniBase.
* 🛠️ **Developer** if you want to run from source and use the project-local cache.
* 📦 **Build Release** if you want to create `AniBase.exe`.

### Release

**Version:** [v2026.07.13]

**Step 1: Download or Unpack**

[**Download AniBase for Windows**](https://github.com/anzutm/anibase-local/releases/tag/v2026.07.13)

Download the Windows ZIP from the release assets, extract it, then run `AniBase.exe`.

**Step 2: Run the Application**

```text
AniBase.exe
```

**Step 3: Open the Dashboard**

Open the dashboard from the tray, or visit:

```text
http://127.0.0.1:5000/ or localhost:5000
```

Release builds store settings, database, watch history/status, cache, logs, and temp files in:

```text
%LOCALAPPDATA%\AniBase\
```

### Developer

The setup script creates `.venv`, installs all Python dependencies, and makes
sure FFmpeg and FFprobe are available:

```powershell
git clone https://github.com/anzutm/anibase-local.git
cd anibase-local
powershell -ExecutionPolicy Bypass -File .\setup_dev.ps1
```

If FFmpeg is installed during setup, close PowerShell, open a new PowerShell
window, then run `setup_dev.ps1` once more so the updated `PATH` is available.

Run AniBase from the project folder:

```powershell
.\.venv\Scripts\Activate.ps1
$env:ANIBASE_USE_PROJECT_RUNTIME="1"
pythonw tray_ui.py
```

With `ANIBASE_USE_PROJECT_RUNTIME` enabled, AniBase uses the data stored in
the repository instead of `%LOCALAPPDATA%\AniBase\`, including:

```text
cache\
logs\
temp\
```

Open the dashboard from the tray or visit:

```text
http://127.0.0.1:5000/ or localhost:5000
```

### Build Release

**Step 1: Prepare the Developer Environment**

Complete the **Developer** setup first. The build script uses the same virtual environment and the dependencies from `requirements.txt`.

**Step 2: Build the Release**

```powershell
python build.py --exe-only
```

**Step 3: Locate the Release Output**

The build target is `onedir` and `windowed`. The release folder is:

```text
releases\AniBase v<version>\
```

The build bundles `templates`, `static`, FFmpeg, FFprobe, their license,
`LICENSE`, and `THIRD_PARTY_NOTICES.md`. It also creates the ready-to-upload
`releases\AniBase-v<version>-win64.zip`. Runtime user data is not bundled.

**Step 4: Check Before GitHub Release**

Run the release checker before uploading the ZIP to GitHub:

```powershell
python release_check.py
```

It runs `py_compile`, the unit test suite, checks the latest release folder, verifies `AniBase.exe`, and blocks release folders that contain private runtime files.

## First Setup

On a fresh runtime directory, AniBase opens the setup flow. Add your folders and preferences:

* 📁 Library folder paths.
* 🎬 Movies folder path.
* 📥 Auto-import paths, if used.
* 🎞️ Media Player executable path, if used.
* 🟣 Discord Rich Presence, if used.
* 🎨 Theme preset preference.
* 🌐 LAN access preference.

Your anime and movie files are never included in this repository or release bundles.

## Playback Troubleshooting

If the player opens but the video is blank or does not play, install the
**K-Lite Codec Pack Full**, restart Windows, then try playing the video again.
This is only required for media formats or codecs that are not already
supported by the user's Windows/browser configuration.

## Notes

* 🏠 AniBase is intended for personal local-library use only.
* 🔐 Do not expose the Flask development server directly to the public internet.
* 🧳 Keep runtime data, cache, logs, and local media out of Git and release bundles.
* 🎬 Windows releases bundle FFmpeg and FFprobe; the external Media Player remains optional.
* 💾 Normal release usage stores data in `%LOCALAPPDATA%\AniBase\`.
* 🛠️ Developer runtime mode stores data in this repository's `cache/` and `logs/` folders.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

Third-party notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
