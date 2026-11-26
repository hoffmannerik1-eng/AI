import json
import math
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
import subprocess

API = "https://api.magichour.ai/v1"
MODEL = "ltx-2.5"
RESOLUTION = "480p"
CREDITS_PER_SECOND = 24

class FreeCreditsUnavailable(RuntimeError):
    """Kein ausreichendes kostenloses Guthaben; lokale Animation verwenden."""

def _request(path, api_key, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        if exc.code == 402 or "insufficient_credits" in detail:
            raise FreeCreditsUnavailable("Magic Hour hat nicht genug kostenlose Credits. Es wurde keine kostenpflichtige Generierung gestartet.") from exc
        raise RuntimeError("Magic Hour API HTTP " + str(exc.code) + ": " + detail) from exc

def _wait_for_video(project_id, api_key):
    for _ in range(48):
        project = _request("/video-projects/" + project_id, api_key)
        status = project.get("status")
        if status == "complete":
            downloads = project.get("downloads") or []
            if not downloads or not downloads[0].get("url"):
                raise RuntimeError("Magic Hour hat keinen Video-Download zurückgegeben.")
            return downloads[0]["url"]
        if status in ("error", "canceled"):
            raise RuntimeError("Magic Hour-Generierung fehlgeschlagen: " + json.dumps(project.get("error"), ensure_ascii=False)[:500])
        time.sleep(5)
    raise TimeoutError("Magic Hour-Generierung dauerte länger als vier Minuten.")

def generate_video(data, output_dir, destination, clip_duration):
    api_key = os.getenv("MAGIC_HOUR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MAGIC_HOUR_API_KEY fehlt.")
    duration = max(5, min(12, int(math.ceil(float(clip_duration)))))
    scenes = data.get("scenes") or []
    if len(scenes) != 5:
        raise RuntimeError("Für Magic Hour werden genau fünf Szenen benötigt.")

    # Vor dem Erzeugen Kontostufe und Guthaben prüfen. Nur Free-Tier + vorhandenes
    # Guthaben verwenden; niemals kostenpflichtige Modelle oder Auflösungen anfordern.
    account = _request("/account", api_key)
    if account.get("tier") != "free":
        raise RuntimeError("Magic Hour ist nicht im Free-Tier. Aus Sicherheitsgründen wird keine Video-API verwendet.")
    needed = duration * CREDITS_PER_SECOND * len(scenes)
    available = int(account.get("credits", 0))
    print("[VIDEO] Magic Hour Free-Tier: " + str(available) + " Credits verfügbar; " + str(needed) + " benötigt.", flush=True)
    if available < needed:
        raise FreeCreditsUnavailable(
            "Nicht genug Magic Hour-Free-Credits: " + str(available) +
            " vorhanden, " + str(needed) + " für fünf Szenen benötigt."
        )

    folder = Path(output_dir) / "magic_hour_clips"
    folder.mkdir(parents=True, exist_ok=True)
    normalized = []
    shot_directions = [
        "OPENING ESTABLISHING SHOT: begin high above the town, then smoothly descend toward the main square; reveal the episode's central event.",
        "GROUND-LEVEL TRACKING SHOT: move along a street between the existing buildings; show people, vehicles and the specific scene action.",
        "CLOSE DETAIL SHOT: focus on the exact object or activity described; use a gentle cinematic push-in and visible environmental movement.",
        "DYNAMIC ORBIT SHOT: make a smooth partial orbit around the location involved in this scene; clearly show what happens.",
        "CLOSING WIDE SHOT: pull back to a beautiful elevated view of the whole town; end with a memorable visual."
    ]
    building_list = ", ".join(
        str(b.get("name", "building")) + " (type " + str(b.get("type", "house")) + ")"
        for b in data.get("buildings", [])[:12]
    )

    for index, scene in enumerate(scenes, start=1):
        prompt = (
            "Create one original cinematic vertical 9:16 video shot for episode "
            + str(data.get("episode", 1)) + " of a continuing fictional German miniature-city series. "
            "CITY: " + str(data.get("city_name", "Neustadt")) +
            ". Keep architecture, street layout, materials and warm cinematic color palette consistent. "
            "CURRENT LANDMARKS: " + building_list + ". "
            "EPISODE: " + str(data.get("title", "")) + ". "
            "SCENE " + str(index) + " ACTION: " + str(scene) + ". "
            "CAMERA: " + shot_directions[index-1] + " "
            "Show this exact action clearly. Detailed realistic miniature sets, natural motion, "
            "cinematic depth of field, smooth professional camera work and believable lighting. "
            "Do not add buildings or alter the town unless the scene explicitly describes a viewer-requested construction. "
            "No text, subtitles, labels, logos or watermarks."
        )[:1800]

        project = _request("/text-to-video", api_key, {
            "end_seconds": duration,
            "aspect_ratio": "9:16",
            "model": MODEL,
            "resolution": RESOLUTION,
            "audio": False,
            "style": {"prompt": prompt},
            "name": "AI ERIK Stadt Folge " + str(data.get("episode", 1)) + " Szene " + str(index),
        })
        project_id = project.get("id")
        if not project_id:
            raise RuntimeError("Magic Hour hat keine Projekt-ID zurückgegeben: " + json.dumps(project)[:400])
        print("[VIDEO] Magic Hour-Szene " + str(index) + "/5 gestartet.", flush=True)
        url = _wait_for_video(project_id, api_key)
        raw = folder / ("scene_" + str(index) + ".mp4")
        request = urllib.request.Request(url, headers={"User-Agent": "AI-ERIK-Stadt-Autopilot"})
        with urllib.request.urlopen(request, timeout=120) as response, raw.open("wb") as target:
            target.write(response.read())

        normalized_path = folder / ("normalized_" + str(index) + ".mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-movflags", "+faststart", str(normalized_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        normalized.append(normalized_path)

    concat_file = folder / "concat.txt"
    concat_file.write_text("".join("file '" + str(p.resolve()) + "'\\n" for p in normalized), encoding="utf-8")
    combined = Path(destination)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", "-movflags", "+faststart", str(combined)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    return combined
