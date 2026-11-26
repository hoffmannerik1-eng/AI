"""Magic Hour video generation using the official Python SDK.

Real generations are opt-in and limited to manually started workflow runs.
The default is local animation, so hourly scheduled runs cannot spend credits.
"""
import os
import subprocess
from pathlib import Path

MODEL = "ltx-2.5"
RESOLUTION = "480p"
CLIP_SECONDS = 5
ESTIMATED_CREDITS_PER_CLIP = 120


class FreeCreditsUnavailable(RuntimeError):
    """The API generation is disabled or the configured credit budget is too low."""


def _error_status(exc):
    response = getattr(exc, "response", None)
    return (
        getattr(exc, "status_code", None)
        or getattr(response, "status_code", None)
        or getattr(response, "status", None)
    )


def generate_video(data, output_dir, destination, clip_duration):
    api_key = os.getenv("MAGIC_HOUR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MAGIC_HOUR_API_KEY fehlt.")

    # Never generate automatically on the hourly schedule or without an
    # explicit, parent-managed credit budget.
    enabled = os.getenv("MAGIC_HOUR_ENABLE_GENERATION", "").strip().lower()
    if enabled != "true":
        raise FreeCreditsUnavailable(
            "Magic Hour ist standardmäßig ausgeschaltet. "
            "Es wird die lokale Animation verwendet."
        )

    scenes = data.get("scenes") or []
    if len(scenes) != 5:
        raise RuntimeError("Für Magic Hour werden genau fünf Szenen benötigt.")

    estimated_total = len(scenes) * ESTIMATED_CREDITS_PER_CLIP
    try:
        credit_budget = int(os.getenv("MAGIC_HOUR_MAX_CREDITS_PER_EPISODE", "0"))
    except ValueError:
        credit_budget = 0

    if credit_budget < estimated_total:
        raise FreeCreditsUnavailable(
            "Das konfigurierte Magic-Hour-Limit reicht nicht. "
            "Für fünf 5-Sekunden-Szenen mit LTX-2.5 werden laut Anbieter "
            + str(estimated_total)
            + " Credits geschätzt; eingestellt sind "
            + str(credit_budget)
            + ". Es wird nichts generiert."
        )

    try:
        from magic_hour import Client
        client = Client(token=api_key)
    except Exception as exc:
        raise RuntimeError("Das offizielle Magic Hour SDK konnte nicht geladen werden: " + str(exc)) from exc

    folder = Path(output_dir) / "magic_hour_clips"
    folder.mkdir(parents=True, exist_ok=True)

    shot_directions = data.get("edit_plan") or [
        "Begin with a strong visual hook and immediate action.",
        "Follow the main action with a clear moving camera.",
        "Use a close detail shot for the important clue or reaction.",
        "Reveal the obstacle or twist with a purposeful transition.",
        "Finish on the strongest final reveal or reaction."
    ]

    normalized = []
    for index, scene in enumerate(scenes, start=1):
        character_bible = data.get("characters", [])
        visual_style = str(data.get("visual_style", "High-quality animated short in a style chosen for the story"))
        edit_direction = shot_directions[index - 1] if len(shot_directions) >= index else "Use a purposeful cinematic cut."
        sound_plan = data.get("sound_plan") or []
        sound_direction = sound_plan[index - 1] if len(sound_plan) >= index else "Use original, rights-cleared sound design."
        prompt = (
            "Create a fully animated vertical 9:16 short-video scene in the exact style described. "
            "Do not force Roblox, block avatars or one fixed animation style unless the selected visual style specifically requests it. "
            "Show the actual event, not static posing or a slideshow. Keep recurring characters, objects, colors and locations consistent across all scenes. "
            "Choose location, props, weather, time of day and lighting from THIS story scene. "
            "No subtitles, captions, readable text, letters, title cards, logos or watermarks. "
            "VISUAL STYLE: " + visual_style + ". "
            "CHARACTER DESIGNS: " + str(character_bible) + ". "
            "EPISODE: " + str(data.get("title", "")) + ". STORY: " + str(data.get("story", "")) + ". "
            "THIS SCENE AND ITS VISIBLE ACTION: " + str(scene) + ". "
            "EDIT AND CAMERA DIRECTION: " + str(edit_direction) + ". "
            "SOUND DESIGN INTENTION (audio is mixed later): " + str(sound_direction) + ". "
            "Animate the action continuously with clear movement and interaction. "
        )[:3500]

        try:
            result = client.v1.text_to_video.generate(
                end_seconds=CLIP_SECONDS,
                aspect_ratio="9:16",
                style={"prompt": prompt},
                name= "AI ERIK Story Folge "
                + str(data.get("episode", 1))
                + " Szene "
                + str(index),
                model=MODEL,
                resolution=RESOLUTION,
                audio=False,
                wait_for_completion=True,
                download_outputs=True,
                download_directory=str(folder),
            )
        except Exception as exc:
            status = _error_status(exc)
            if status == 402:
                raise FreeCreditsUnavailable(
                    "Magic Hour meldet zu wenig Credits. Die lokale Animation wird verwendet."
                ) from exc
            if status == 401:
                raise RuntimeError(
                    "Magic Hour hat den API-Schlüssel abgelehnt (401). "
                    "Bitte den Schlüssel im Developer Hub prüfen und das GitHub Secret "
                    "MAGIC_HOUR_API_KEY aktualisieren."
                ) from exc
            raise RuntimeError("Magic Hour API-Fehler: " + str(exc)) from exc

        status = getattr(result, "status", None)
        if status != "complete":
            error = getattr(result, "error", None)
            detail = getattr(error, "message", str(error or "unbekannter Fehler"))
            raise RuntimeError("Magic Hour-Szene " + str(index) + " endete mit " + str(status) + ": " + detail)

        paths = getattr(result, "downloaded_paths", None) or []
        if isinstance(paths, (str, Path)):
            paths = [paths]
        if not paths:
            raise RuntimeError("Magic Hour hat die Szene fertiggestellt, aber keinen Downloadpfad geliefert.")

        raw = Path(paths[0])
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        if not raw.exists():
            raise RuntimeError("Magic Hour-Download wurde nicht gefunden: " + str(raw))

        normalized_path = folder / ("normalized_" + str(index) + ".mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(raw),
                "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-movflags", "+faststart", str(normalized_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        normalized.append(normalized_path)
        print("[VIDEO] Magic Hour-Szene " + str(index) + "/5 fertig.", flush=True)

    concat_file = folder / "concat.txt"
    concat_file.write_text(
        "".join("file '" + str(path.resolve()) + "'\n" for path in normalized),
        encoding="utf-8",
    )
    combined = Path(destination)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-movflags", "+faststart", str(combined),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    return combined
