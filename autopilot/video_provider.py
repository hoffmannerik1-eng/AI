import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
import subprocess

API = "https://api.dev.runwayml.com/v1"
VERSION = "2024-11-06"
RATES = {"grok_imagine_1_5_lite": 3, "wan3": 10, "gen4.5": 12, "grok_imagine_1_5": 16}

def estimate_cost(model, duration, clips):
    if model not in RATES:
        raise RuntimeError("Für dieses Runway-Modell ist kein sicherer Kostenwert hinterlegt.")
    return round(RATES[model] * duration * clips / 100, 2)

def _request(path, api_key, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, headers={
        "Authorization": "Bearer " + api_key,
        "X-Runway-Version": VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, method="GET" if payload is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError("Runway API HTTP " + str(exc.code) + ": " + detail) from exc

def _wait_for_task(task_id, api_key):
    for _ in range(90):
        task = _request("/tasks/" + task_id, api_key)
        status = task.get("status")
        if status == "SUCCEEDED":
            outputs = task.get("output") or []
            if not outputs or not isinstance(outputs[0], str):
                raise RuntimeError("Runway hat kein Video zurückgegeben.")
            return outputs[0]
        if status in ("FAILED", "CANCELED"):
            raise RuntimeError("Runway-Generierung fehlgeschlagen: " + json.dumps(task, ensure_ascii=False)[:500])
        time.sleep(10)
    raise TimeoutError("Runway-Generierung dauerte länger als 15 Minuten.")

def generate_video(data, output_dir, destination):
    api_key = os.getenv("RUNWAY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RUNWAY_API_KEY fehlt.")
    model = os.getenv("RUNWAY_MODEL", "grok_imagine_1_5_lite").strip()
    if model not in RATES:
        raise RuntimeError("Nicht freigegebenes Video-Modell: " + model)
    scenes = data.get("scenes") or []
    if not scenes:
        raise RuntimeError("Keine Szenen für die Video-KI vorhanden.")
    folder = Path(output_dir) / "runway_clips"
    folder.mkdir(parents=True, exist_ok=True)
    normalized = []
    # Jeder Clip bekommt eine eigene Bildregie. So entstehen unterschiedliche Einstellungen
    # derselben fortlaufenden Stadt statt fünf nahezu gleicher Stadt-Totalen.
    shot_directions = [
        "OPENING ESTABLISHING SHOT: begin high above the town, then smoothly descend toward the main square; reveal the episode's central event.",
        "GROUND-LEVEL TRACKING SHOT: move along a street between the existing buildings; show people, vehicles and the specific scene action.",
        "CLOSE DETAIL SHOT: focus on the exact object or activity described; use a gentle cinematic push-in and visible environmental movement.",
        "DYNAMIC ORBIT SHOT: make a smooth partial orbit around the location involved in this scene; clearly show what changes or happens.",
        "CLOSING WIDE SHOT: pull back to a beautiful elevated view of the whole town at a different time-of-day mood; end with a memorable visual."
    ]
    normalized = []
    for index, scene in enumerate(scenes[:5], start=1):
        building_list = ", ".join(
            str(b.get("name", "building")) + " (type " + str(b.get("type", "house")) + ")"
            for b in data.get("buildings", [])[:12]
        )
        prompt = (
            "Generate ONE original 5-second cinematic video shot, vertical 9:16, for episode "
            + str(data.get("episode", 1)) + " of a continuing fictional German miniature-city series. "
            "This shot must visually depict ONLY this scene's action, not a generic montage. "
            "CITY IDENTITY: " + str(data.get("city_name", "Neustadt")) +
            ", a coherent miniature European town with the same architecture, street layout, "
            "materials and warm cinematic color palette throughout this episode. "
            "CURRENT CITY LANDMARKS: " + building_list + ". "
            "EPISODE STORY: " + str(data.get("title", "")) + ". "
            "SCENE " + str(index) + " OF 5 ACTION: " + str(scene) + ". "
            "CAMERA DIRECTION: " + shot_directions[index-1] + " "
            "Make the described action unmistakable and visually different from the other shots. "
            "Detailed realistic miniature sets, natural motion, cinematic depth of field, "
            "smooth professional camera work, rich but believable lighting. "
            "Do not add new buildings or change the town layout unless the scene explicitly describes "
            "a viewer-requested construction. No text, subtitles, labels, logos or watermarks."
        )[:1800]
        task = _request("/text_to_video", api_key, {
            "model": model, "promptText": prompt, "ratio": "720:1280", "duration": 5
        })
        task_id = task.get("id")
        if not task_id:
            raise RuntimeError("Runway hat keine Task-ID zurückgegeben: " + json.dumps(task)[:300])
        print("[VIDEO] Runway-Szene " + str(index) + "/" + str(min(5, len(scenes))) + " gestartet.", flush=True)
        url = _wait_for_task(task_id, api_key)
        raw = folder / ("scene_" + str(index) + ".mp4")
        req = urllib.request.Request(url, headers={"User-Agent": "AI-ERIK-Stadt-Autopilot"})
        with urllib.request.urlopen(req, timeout=120) as response, raw.open("wb") as target:
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
    concat_file.write_text("".join("file '" + str(p.resolve()) + "'\n" for p in normalized), encoding="utf-8")
    combined = Path(destination)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", "-movflags", "+faststart", str(combined)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    return combined
