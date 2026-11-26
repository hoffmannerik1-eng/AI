"""Agnes AI Video 2.5 Flash provider for AI ERIK.
Uses the current 2.5 async API. Flash is 720P and may be offered free only
as a temporary promotion; generation is explicitly opt-in.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com").rstrip("/")
MODEL = "agnes-video-2.5-flash"
CLIP_SECONDS = "5"


class AgnesGenerationDisabled(RuntimeError):
    pass


def _request(url, api_key, method="GET", payload=None, timeout=90):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": "Bearer " + api_key, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _video_url(status_data):
    metadata = status_data.get("metadata") or {}
    return (
        status_data.get("url")
        or status_data.get("video_url")
        or metadata.get("url")
        or metadata.get("video_url")
    )


def _wait_for_video(video_id, api_key, timeout_seconds=900):
    query = urllib.parse.urlencode({"video_id": video_id, "model_name": MODEL})
    url = BASE + "/agnesapi?" + query
    deadline = time.monotonic() + timeout_seconds
    delay = 15
    while time.monotonic() < deadline:
        try:
            result = _request(url, api_key)
        except urllib.error.HTTPError as exc:
            if exc.code in (408, 429, 500, 502, 503, 504):
                print("[AGNES] Statusabfrage vorübergehend nicht möglich; erneuter Versuch.", flush=True)
                time.sleep(delay)
                delay = min(45, delay + 5)
                continue
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError("Agnes-Statusabfrage fehlgeschlagen: HTTP " + str(exc.code) + " " + detail)
        status = str(result.get("status", "")).lower()
        if status == "completed":
            output_url = _video_url(result)
            if not output_url:
                raise RuntimeError("Agnes meldet completed, aber liefert keine Video-URL.")
            return output_url
        if status == "failed":
            raise RuntimeError("Agnes-Video fehlgeschlagen: " + str(result.get("error") or result.get("message") or result)[:500])
        print("[AGNES] Clip wartet: " + (status or "unbekannt"), flush=True)
        time.sleep(delay)
        delay = min(30, delay + 5)
    raise TimeoutError("Agnes-Video wurde innerhalb von 15 Minuten nicht fertig.")


def _download(url, target):
    req = urllib.request.Request(url, headers={"User-Agent": "AI-ERIK/1.0"})
    with urllib.request.urlopen(req, timeout=180) as response, open(target, "wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    if not target.exists() or target.stat().st_size < 10000:
        raise RuntimeError("Agnes-Download ist leer oder unvollständig.")


def generate_video(data, output_dir, destination, clip_duration=5):
    api_key = os.getenv("AGNES_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("AGNES_API_KEY fehlt.")
    if os.getenv("AGNES_ENABLE_GENERATION", "").strip().lower() != "true":
        raise AgnesGenerationDisabled(
            "Agnes-Video ist noch ausgeschaltet. Ein Elternteil muss die Nutzung "
            "und mögliche Kosten prüfen und AGNES_ENABLE_GENERATION=true freigeben."
        )

    scenes = data.get("scenes") or []
    if len(scenes) != 5:
        raise RuntimeError("Agnes benötigt genau fünf Szenen.")

    folder = Path(output_dir) / "agnes_clips"
    folder.mkdir(parents=True, exist_ok=True)
    normalized = []
    characters = data.get("characters", [])
    style = str(data.get("visual_style", "Roblox story short: blocky low-poly avatars, smooth slightly plastic surfaces, vertical 9:16"))
    for index, scene in enumerate(scenes, start=1):
        prompt = (
            "Create a fully animated vertical 9:16 Roblox story-short scene. "
            "MANDATORY STYLE: classic Roblox-like block characters with a cuboid head, torso, arms and legs; simple block joints; smooth surfaces with a subtle plastic look; simple textures; clean low-poly 3D geometry. "
            "Characters must perform readable human-like actions with Roblox animation: walk, run, turn, sit, reach, grab, point, react and interact with each other and objects as the scene requires. Use expressive but simple Roblox faces. "
            "The location, buildings, props, weather, time of day and lighting MUST be chosen from the actual story and this shot; do not default to a suburban street, bicycle or any example setting unless explicitly required by the story. "
            "Show the actual event happening, not a character merely posing. Preserve the exact same character appearance, proportions, hair and clothing across all five clips. "
            "NO subtitles, NO captions, NO on-screen words, NO letters, NO signs with readable text, NO title cards, NO logos, NO watermarks. No live action, no photorealism, no other visual style, no slideshow. "
            "Use a suitable third-person camera that follows or frames the action; vary framing naturally when the story changes. "
            "VISUAL STYLE: " + style + ". CHARACTER DESIGNS: " + json.dumps(characters, ensure_ascii=False) +
            ". EPISODE: " + str(data.get("title", "")) +
            ". STORY CONTEXT: " + str(data.get("story", "")) +
            ". THIS SHOT: " + str(scene) +
            ". Animate the described action continuously with visible body movement and interaction."
        )
        payload = {
            "model": MODEL,
            "prompt": prompt[:3500],
            "mode": "text",
            "seconds": CLIP_SECONDS,
            "size": "720P",
            "aspect_ratio": "9:16",
            "n": 1,
        }
        print("[AGNES] Starte Cartoon-Clip " + str(index) + "/5.", flush=True)
        # Agnes kann bei hoher Auslastung mit video_queue_full/503 antworten.
        # Dann mit wachsendem Abstand erneut versuchen; keine Ersatz-KI verwenden.
        created = None
        for attempt in range(1, 7):
            try:
                created = _request(BASE + "/v1/videos", api_key, method="POST", payload=payload, timeout=120)
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:700]
                if exc.code == 401:
                    raise RuntimeError("Agnes hat den API-Schlüssel abgelehnt (401). GitHub Secret AGNES_API_KEY prüfen.")
                if exc.code == 503 and attempt < 6 and "video_queue_full" in detail:
                    wait = min(90, attempt * 20)
                    print("[AGNES] Warteschlange voll; neuer Versuch " + str(attempt) + "/5 in " + str(wait) + " Sekunden.", flush=True)
                    time.sleep(wait)
                    continue
                if exc.code in (402, 429, 503):
                    raise RuntimeError("Agnes hat die Anfrage nicht angenommen (HTTP " + str(exc.code) + "): " + detail)
                raise RuntimeError("Agnes-Videoanfrage fehlgeschlagen (HTTP " + str(exc.code) + "): " + detail)
        video_id = created.get("video_id") or created.get("id") or created.get("task_id")
        if not video_id:
            raise RuntimeError("Agnes lieferte keine video_id zurück: " + str(created)[:500])
        result_url = _wait_for_video(str(video_id), api_key)
        raw = folder / ("raw_" + str(index) + ".mp4")
        _download(result_url, raw)
        normalized_path = folder / ("clip_" + str(index) + ".mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw),
             "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p",
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
             "-movflags", "+faststart", str(normalized_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        normalized.append(normalized_path)
        print("[AGNES] Clip " + str(index) + "/5 fertig.", flush=True)

    concat_file = folder / "concat.txt"
    concat_file.write_text(
        "".join("file '" + str(path.resolve()) + "'\n" for path in normalized),
        encoding="utf-8",
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", "-movflags", "+faststart", str(destination)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    return Path(destination)
