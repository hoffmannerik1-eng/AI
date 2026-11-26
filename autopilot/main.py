import os, re, json, random, asyncio, subprocess, textwrap, math, urllib.request, urllib.error
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
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_DAILY = int(os.getenv("MAX_DAILY_VIDEOS", "1"))
PRIVACY = os.getenv("YOUTUBE_PRIVACY_STATUS", "private").lower()
if GEMINI_KEY:
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
scenes (genau 5 kurze, bildhafte Szenen mit konkreter Handlung und sichtbarer Bewegung, je max 8 Wörter; eine Hauptfigur bleibt durchgehend dieselbe),
hashtags (Array mit 3 bis 5 passenden Hashtags).
"""
    raw = ""
    primary_error = None

    if GEMINI_KEY:
        try:
            log(f"KI 1: Gemini ({MODEL_NAME}) wird verwendet")
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            raw = response.text.strip()
        except Exception as exc:
            primary_error = exc
            log(f"Gemini nicht verfügbar: {str(exc)[:300]}")
    else:
        log("GEMINI_API_KEY fehlt; Gemini wird übersprungen")

    if not raw:
        if not OPENROUTER_KEY:
            reason = f" Gemini-Fehler: {str(primary_error)[:250]}" if primary_error else ""
            raise RuntimeError("Keine KI verfügbar. Füge GEMINI_API_KEY und/oder OPENROUTER_API_KEY als GitHub Actions Secrets hinzu." + reason)
        try:
            router_model = os.getenv("OPENROUTER_MODEL", "openrouter/free")
            log(f"KI 2: OpenRouter ({router_model}) übernimmt")
            payload = {
                "model": router_model,
                "messages": [
                    {"role": "system", "content": "Du erstellst sichere, sachliche, jugendgeeignete deutschsprachige YouTube-Shorts. Antworte ausschließlich mit gültigem JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1400,
                "response_format": {"type": "json_object"}
            }
            request = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/hoffmannerik1-eng/AI",
                    "X-Title": "AI ERIK Autopilot"
                },
                method="POST"
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            raw = result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise RuntimeError(f"Gemini und OpenRouter konnten nicht generieren. OpenRouter-Fehler: {str(exc)[:400]}") from exc

    raw = re.sub(r"^`{3}(?:json)?\s*|\s*`{3}$", "", raw, flags=re.I)
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
    """Erzeugt eine gezeichnete 2D-Cartoon-Animation mit Figuren und Bewegung."""
    w, h = 720, 1280
    total = max(1, int(math.ceil(duration * fps)))
    scene_count = len(data["scenes"])
    frame_dir = OUT / "frames"
    frame_dir.mkdir(exist_ok=True)
    for old in frame_dir.glob("frame_*.png"):
        old.unlink()

    title_font = font(25, True)
    scene_font = font(42, True)
    small_font = font(20)
    particles = [(random.randint(0, w), random.randint(0, h),
                  random.uniform(8, 30), random.uniform(1, 3)) for _ in range(32)]

    def character(draw, x, y, scale, t, pose, shirt, skin=(255, 194, 145)):
        bob = math.sin(t * 5.5) * 7 * scale
        x, y = int(x), int(y + bob)
        head_r = int(48 * scale)
        swing = math.sin(t * 6.0) * 24 * scale
        if pose == "walk":
            left_leg, right_leg = swing, -swing
            left_arm, right_arm = -swing * .75, swing * .75
        elif pose == "point":
            left_leg, right_leg = 0, 0
            left_arm, right_arm = -35 * scale, -95 * scale
        elif pose == "celebrate":
            left_leg, right_leg = -12 * scale, 12 * scale
            left_arm, right_arm = -105 * scale, 105 * scale
        elif pose == "think":
            left_leg, right_leg = 0, 0
            left_arm, right_arm = 0, -65 * scale
        else:
            left_leg, right_leg = 0, 0
            left_arm, right_arm = -8 * scale, 8 * scale

        hip_y = y + int(155 * scale)
        shoulder_y = y + int(75 * scale)
        leg_len = int(92 * scale)
        limb = max(8, int(17 * scale))
        for side, offset in [(-1, left_leg), (1, right_leg)]:
            knee_x = x + int(side * 19 * scale + offset * .45)
            knee_y = hip_y + int(leg_len * .52)
            foot_x = knee_x + int(offset * .8)
            foot_y = hip_y + leg_len
            draw.line((x + int(side*17*scale), hip_y, knee_x, knee_y, foot_x, foot_y),
                      fill=(28, 35, 52), width=limb + 5, joint="curve")
            draw.ellipse((knee_x-limb//2, knee_y-limb//2, knee_x+limb//2, knee_y+limb//2), fill=skin)
            draw.rounded_rectangle((foot_x-17*scale, foot_y-5*scale, foot_x+19*scale, foot_y+10*scale),
                                   radius=int(8*scale), fill=(235, 242, 248))
        draw.rounded_rectangle((x-int(39*scale), shoulder_y, x+int(39*scale), hip_y+int(8*scale)),
                               radius=int(22*scale), fill=shirt,
                               outline=(245, 250, 255), width=max(2, int(3*scale)))
        for side, amount in [(-1, left_arm), (1, right_arm)]:
            elbow_x = x + int(side * 65 * scale + amount * .35)
            elbow_y = shoulder_y + int(38*scale) + int(amount * .15)
            hand_x = x + int(side * 75 * scale + amount)
            hand_y = shoulder_y + int(75*scale) + int(amount * .8)
            draw.line((x+int(side*30*scale), shoulder_y+int(18*scale),
                       elbow_x, elbow_y, hand_x, hand_y), fill=skin, width=limb, joint="curve")
            draw.ellipse((hand_x-9*scale, hand_y-9*scale, hand_x+9*scale, hand_y+9*scale), fill=skin)
        draw.rectangle((x-12*scale, y+38*scale, x+12*scale, y+70*scale), fill=skin)
        draw.ellipse((x-head_r, y-head_r, x+head_r, y+head_r),
                     fill=skin, outline=(75, 48, 42), width=max(2, int(3*scale)))
        draw.pieslice((x-head_r-2, y-head_r-8, x+head_r+2, y+head_r+25), 180, 360, fill=(48, 35, 31))
        eye_y = y - 3*scale
        draw.ellipse((x-20*scale, eye_y-5*scale, x-10*scale, eye_y+6*scale), fill=(30, 35, 45))
        draw.ellipse((x+10*scale, eye_y-5*scale, x+20*scale, eye_y+6*scale), fill=(30, 35, 45))
        draw.arc((x-14*scale, y+7*scale, x+14*scale, y+25*scale), 5, 175,
                 fill=(120, 45, 45), width=max(2, int(3*scale)))

    for n in range(total):
        t = n / fps
        scene_index = min(scene_count - 1, int(t / duration * scene_count))
        local = (t * scene_count / duration) % 1.0
        enter = min(1.0, local / 0.2)
        enter = enter * enter * (3 - 2 * enter)
        scene = str(data["scenes"][scene_index])
        lower = scene.lower()
        img = Image.new("RGB", (w, h), (8, 13, 27))
        d = ImageDraw.Draw(img)
        drift = int(math.sin(t * .45) * 24)
        d.ellipse((70+drift, 170, 650+drift, 750), fill=(15, 38, 57))
        d.ellipse((130-drift, 230, 590-drift, 690), outline=(32, 93, 105), width=5)
        for px, py, speed, radius in particles:
            yy = (py - t * speed * 14) % h
            xx = (px + math.sin(t + py) * 16) % w
            rr = max(1, int(radius))
            d.ellipse((xx-rr, yy-rr, xx+rr, yy+rr), fill=(50, 145, 135))

        prop_x, prop_y = 555, 390
        if any(k in lower for k in ("planet", "weltall", "raum", "stern", "mond")):
            d.ellipse((prop_x-62, prop_y-62, prop_x+62, prop_y+62), fill=(70, 110, 205), outline=(180, 210, 255), width=5)
            d.ellipse((prop_x-105, prop_y-25, prop_x+105, prop_y+25), outline=(120, 225, 205), width=9)
            d.ellipse((prop_x-17, prop_y-28, prop_x+12, prop_y+5), fill=(100, 160, 220))
        elif any(k in lower for k in ("licht", "energie", "idee", "erfind", "gehirn")):
            d.ellipse((prop_x-48, prop_y-55, prop_x+48, prop_y+45), fill=(255, 207, 83), outline=(255, 240, 170), width=5)
            d.rounded_rectangle((prop_x-22, prop_y+39, prop_x+22, prop_y+70), radius=8, fill=(220, 230, 240))
            for a in range(8):
                ang = a * math.tau / 8
                ex, ey = prop_x+math.cos(ang)*78, prop_y+math.sin(ang)*78
                d.line((prop_x+math.cos(ang)*58, prop_y+math.sin(ang)*58, ex, ey), fill=(255, 218, 100), width=6)
        elif any(k in lower for k in ("handy", "smartphone", "app", "computer", "internet")):
            d.rounded_rectangle((prop_x-48, prop_y-85, prop_x+48, prop_y+85), radius=17,
                                fill=(28, 38, 58), outline=(115, 235, 205), width=6)
            d.rounded_rectangle((prop_x-34, prop_y-62, prop_x+34, prop_y+58), radius=8, fill=(42, 120, 145))
            d.ellipse((prop_x-6, prop_y+66, prop_x+6, prop_y+78), fill=(220, 235, 240))
        elif any(k in lower for k in ("wasser", "meer", "ozean", "flut")):
            for j in range(4):
                yy = prop_y-45+j*32
                d.arc((prop_x-100, yy-20, prop_x+100, yy+30), 0, 180, fill=(70, 190, 245), width=9)
            d.ellipse((prop_x-32, prop_y-95, prop_x+32, prop_y-30), fill=(80, 170, 235))
        else:
            d.rounded_rectangle((prop_x-70, prop_y-62, prop_x+70, prop_y+62), radius=22,
                                fill=(20, 70, 75), outline=(75, 230, 175), width=5)
            d.line((prop_x-35, prop_y, prop_x-8, prop_y+25, prop_x+42, prop_y-30),
                   fill=(190, 255, 220), width=9)

        pose = "walk" if scene_index % 3 == 0 else ("point" if scene_index % 3 == 1 else "think")
        if any(k in lower for k in ("entdeck", "gewinn", "lösung", "geschafft", "erfolg")):
            pose = "celebrate"
        character(d, int(245 + math.sin(t*1.4)*25), 475, 1.0, t, pose, (48, 170, 130))

        bubble = scene[:48] + ("…" if len(scene) > 48 else "")
        bb = d.textbbox((0, 0), bubble, font=small_font)
        bw = min(430, bb[2]-bb[0]+42)
        d.rounded_rectangle((70, 760, 70+bw, 825), radius=20, fill=(240, 248, 246), outline=(75, 235, 175), width=3)
        d.text((90, 779), bubble, font=small_font, fill=(12, 30, 38))
        d.text((42, 48), "AI ERIK  /  ANIMATED STORY", font=title_font, fill=(90, 255, 180))
        d.text((w-130, 52), f"{scene_index+1:02d}/{scene_count:02d}", font=small_font, fill=(175, 205, 215))
        lines = textwrap.wrap(scene, width=24)
        y = 890 + int((1-enter)*45)
        for line in lines[:2]:
            box = d.textbbox((0, 0), line, font=scene_font)
            x = (w-(box[2]-box[0]))//2
            d.text((x+2, y+3), line, font=scene_font, fill=(8, 55, 48))
            d.text((x, y), line, font=scene_font, fill=(248, 252, 250))
            y += 58
        d.rounded_rectangle((42, 1190, w-42, 1205), radius=7, fill=(30, 50, 60))
        progress = n / max(1, total-1)
        d.rounded_rectangle((42, 1190, 42+int((w-84)*progress), 1205), radius=7, fill=(65, 245, 165))
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
