import os, re, json, random, asyncio, subprocess, textwrap, math
from datetime import datetime, timezone
from pathlib import Path
import feedparser
import google.generativeai as genai
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).parent
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_DAILY = int(os.getenv("MAX_DAILY_VIDEOS", "1"))
PRIVACY = os.getenv("YOUTUBE_PRIVACY_STATUS", "private").lower()
genai.configure(api_key=GEMINI_KEY)

def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)

def get_trends():
    feeds = [
        "https://trends.google.com/trending/rss?geo=DE",
        "https://news.google.com/rss?hl=de&gl=DE&ceid=DE:de"
    ]
    found = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for item in feed.entries[:12]:
                title = re.sub(r"\s+", " ", item.get("title", "")).strip()
                if title and title not in found:
                    found.append(title)
        except Exception as exc:
            log(f"Feed konnte nicht geladen werden: {exc}")
    if not found:
        raise RuntimeError("Keine Trendthemen erreichbar. Abbruch statt erfundene Trends.")
    return found[:20]

def generate_content(trends):
    prompt = f"""
Du bist Redakteur für einen deutschsprachigen YouTube-Shorts-Kanal.
Wähle EIN Thema aus dieser aktuellen Themenliste und erstelle ein originelles,
sachliches und jugendgeeignetes Short. Keine Gerüchte, keine unbelegten Behauptungen,
keine gefährlichen Challenges, keine medizinischen oder politischen Falschinformationen.
Erfinde keine aktuellen Fakten. Wenn ein Thema nicht zuverlässig erklärbar ist,
wähle ein anderes.
Trendthemen:
{json.dumps(trends, ensure_ascii=False)}
Antworte ausschließlich als JSON mit:
title (max 70 Zeichen), description (kurz, mit Hinweis auf KI-Visualisierung),
script (deutscher Sprechertext, 75 bis 105 Wörter),
scenes (genau 5 kurze Szenentexte, je max 8 Wörter),
hashtags (Array mit 3 bis 5 passenden Hashtags).
"""
    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(prompt)
    raw = response.text.strip()
    raw = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", raw, flags=re.I)
    data = json.loads(raw)
    if not all(k in data for k in ("title", "description", "script", "scenes", "hashtags")):
        raise ValueError("KI-Antwort unvollständig")
    if len(data["scenes"]) != 5 or not (45 <= len(data["script"].split()) <= 140):
        raise ValueError("Skript besteht Qualitätsprüfung nicht")
    return data

def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf"
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def render_animated_frames(data, duration, fps=18):
    """Erzeugt eigenständige Motion-Graphics-Szenen mit laufender Bewegung."""
    w, h = 720, 1280
    total = max(1, int(math.ceil(duration * fps)))
    scene_count = len(data["scenes"])
    frame_dir = OUT / "frames"
    frame_dir.mkdir(exist_ok=True)
    for old in frame_dir.glob("frame_*.png"):
        old.unlink()

    title_font = font(28, True)
    scene_font = font(47, True)
    small_font = font(22)
    label_font = font(25, True)
    particles = [
        (random.randint(20, w-20), random.randint(0, h),
         random.uniform(12, 42), random.uniform(1.0, 3.0))
        for _ in range(42)
    ]

    for n in range(total):
        t = n / fps
        scene_index = min(scene_count - 1, int(t / duration * scene_count))
        local = (t * scene_count / duration) % 1.0
        enter = min(1.0, local / 0.18)
        enter = enter * enter * (3 - 2 * enter)
        img = Image.new("RGB", (w, h), (5, 9, 18))
        d = ImageDraw.Draw(img)

        offset = int((t * 34) % 80)
        for x in range(-80 + offset, w, 80):
            d.line((x, 0, x, h), fill=(12, 27, 39), width=1)
        for y in range(-80 + offset, h, 80):
            d.line((0, y, w, y), fill=(12, 27, 39), width=1)
        for px, py, speed, radius in particles:
            yy = (py - t * speed * 18) % h
            xx = (px + math.sin(t * 0.7 + py) * 15) % w
            rr = max(1, int(radius))
            d.ellipse((xx-rr, yy-rr, xx+rr, yy+rr), fill=(38, 125, 111))

        d.rounded_rectangle((24, 24, w-24, h-24), radius=30,
                            outline=(36, 190, 130), width=3)
        d.text((52, 55), "AI ERIK  /  AUTOPILOT", font=title_font, fill=(74, 255, 170))
        d.text((w-150, 60), f"{scene_index+1:02d} / {scene_count:02d}",
               font=small_font, fill=(155, 183, 198))

        cx, cy = w//2, 440
        pulse = 1.0 + 0.07 * math.sin(t * 4.2)
        radius = int(112 * pulse)
        for ring in range(3):
            rr = radius + ring * 34
            d.ellipse((cx-rr, cy-rr, cx+rr, cy+rr),
                      outline=(25 + ring*8, 130 + ring*25, 105 + ring*18), width=3)
        angle = t * 1.8
        for k in range(6):
            a = angle + k * math.tau / 6
            ox = cx + math.cos(a) * (radius + 68)
            oy = cy + math.sin(a) * (radius + 68)
            d.ellipse((ox-8, oy-8, ox+8, oy+8), fill=(70, 255, 170))
        d.ellipse((cx-radius//2, cy-radius//2, cx+radius//2, cy+radius//2),
                  fill=(12, 65, 63), outline=(85, 255, 180), width=5)
        d.text((cx, cy), "AI", font=font(55, True), fill=(210, 255, 235), anchor="mm")

        scene = str(data["scenes"][scene_index])
        lines = textwrap.wrap(scene, width=22)
        y = 690 + int((1-enter) * 90)
        for line in lines[:3]:
            box = d.textbbox((0, 0), line, font=scene_font)
            x = (w - (box[2]-box[0])) // 2
            d.text((x+3, y+4), line, font=scene_font, fill=(14, 55, 48))
            d.text((x, y), line, font=scene_font, fill=(245, 252, 250))
            y += 64

        base_y = 1010
        for k in range(31):
            bh = 12 + int((0.5 + 0.5 * math.sin(t*5 + k*0.55)) * 52)
            bx = 92 + k * 18
            d.rounded_rectangle((bx, base_y-bh, bx+8, base_y),
                                radius=4, fill=(52, 220, 145))
        d.text((52, 1090), "WISSEN IN BEWEGUNG", font=label_font, fill=(75, 245, 165))
        d.text((52, 1140), "KI-Visualisierung • automatisch animiert",
               font=small_font, fill=(150, 174, 190))

        progress = n / max(1, total-1)
        d.rounded_rectangle((52, 1200, w-52, 1212), radius=6, fill=(25, 45, 55))
        d.rounded_rectangle((52, 1200, 52 + int((w-104)*progress), 1212),
                            radius=6, fill=(65, 255, 165))
        img.save(frame_dir / f"frame_{n:05d}.png", optimize=True)

    return frame_dir


async def make_voice(text, path):
    communicate = edge_tts.Communicate(text, "de-DE-ConradNeural", rate="+0%")
    await communicate.save(str(path))


def make_video(data):
    audio = OUT / "voice.mp3"
    asyncio.run(make_voice(data["script"], audio))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
        check=True, capture_output=True, text=True
    )
    audio_seconds = max(1.0, float(probe.stdout.strip()))
    fps = 18
    frame_dir = render_animated_frames(data, audio_seconds, fps)
    video = OUT / "short.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frame_dir / "frame_%05d.png"), "-i", str(audio),
        "-vf", "scale=720:1280:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-r", str(fps), "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart", str(video)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    log(f"Animiertes Motion-Graphics-Video erstellt: {audio_seconds:.1f} Sekunden")
    return video

def upload(video, data):
    required = ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]
    if not all(os.getenv(k) for k in required):
        raise RuntimeError("YouTube-OAuth-Secrets fehlen. Video wurde erstellt, aber nicht hochgeladen.")
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": data["title"][:100],
            "description": data["description"] + "\n\n" + " ".join(data["hashtags"]),
            "categoryId": "27",
            "defaultLanguage": "de"
        },
        "status": {
            "privacyStatus": PRIVACY if PRIVACY in ("private","unlisted","public") else "private",
            "selfDeclaredMadeForKids": False
        }
    }
    result = youtube.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True)
    ).execute()
    log(f"YouTube Upload abgeschlossen: https://youtu.be/{result.get('id')}")
    return result.get("id")

def main():
    log("AI ERIK AUTOPILOT gestartet")
    trends = get_trends()
    log(f"{len(trends)} Trend-Einträge gefunden")
    data = generate_content(trends)
    log(f"Thema gewählt: {data['title']}")
    video = make_video(data)
    log(f"Video erstellt: {video}")
    dry_run = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
    video_id = None if dry_run else upload(video, data)
    if dry_run:
        log("TESTMODUS: Kein YouTube-Upload ausgeführt. Video liegt als Artifact bereit.")
    (OUT / "latest.json").write_text(json.dumps({
        "title": data["title"], "video_id": video_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "privacy": PRIVACY
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log("Durchlauf erfolgreich beendet")

if __name__ == "__main__":
    main()
