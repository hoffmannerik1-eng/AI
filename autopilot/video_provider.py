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
    for index, scene in enumerate(scenes[:5], start=1):
        prompt = (
            "Create a polished vertical 9:16 cinematic video for a fictional German "
            "isometric 2.5D city-builder series. Keep a coherent visual identity: "
            "beautiful miniature European town, detailed streets, warm cinematic lighting, "
            "smooth camera movement, high visual quality. No text, captions, logos or watermarks. "
            "City: " + str(data.get("city_name", "Neustadt")) + ". Existing buildings: " +
            ", ".join(str(b.get("name", "building")) for b in data.get("buildings", [])[:12]) +
            ". Scene action: " + str(scene)
        )[:1000]
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
