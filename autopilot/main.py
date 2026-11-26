import os,re,json,asyncio,subprocess,math,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from PIL import Image,ImageDraw,ImageFont
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from agnes_video_provider import generate_video as generate_agnes_video, AgnesGenerationDisabled
from video_provider import generate_video as generate_magic_hour_video, FreeCreditsUnavailable

ROOT=Path(__file__).parent
OUT=ROOT/"output"; OUT.mkdir(exist_ok=True)
STATE=ROOT/"story_state.json"
GEMINI=os.getenv("GEMINI_API_KEY","")
ROUTER=os.getenv("OPENROUTER_API_KEY","")
MODEL=os.getenv("GEMINI_MODEL","gemini-3.8-flash")
PRIVACY=os.getenv("YOUTUBE_PRIVACY_STATUS","private")

def log(s): print(f"[{datetime.now(timezone.utc).isoformat()}] {s}",flush=True)
def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"episode":0,"history":[],"processed_comments":[]}

def yt_service():
    keys=["YOUTUBE_CLIENT_ID","YOUTUBE_CLIENT_SECRET","YOUTUBE_REFRESH_TOKEN"]
    if not all(os.getenv(k) for k in keys): return None
    c=Credentials(None,refresh_token=os.environ[keys[2]],token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ[keys[0]],client_secret=os.environ[keys[1]],
        scopes=["https://www.googleapis.com/auth/youtube.upload","https://www.googleapis.com/auth/youtube.readonly"])
    return build("youtube","v3",credentials=c)

def get_comments(state):
    """Liest Kommentare der neuesten Videos über die Upload-Playlist."""
    try:
        yt=yt_service()
        if not yt: return []
        channels=yt.channels().list(part="contentDetails",mine=True).execute().get("items",[])
        if not channels: return []
        uploads=channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        videos=yt.playlistItems().list(part="contentDetails",playlistId=uploads,maxResults=10).execute().get("items",[])
        done=set(state.get("processed_comments",[])); out=[]
        for video in videos:
            video_id=video["contentDetails"]["videoId"]
            try:
                items=yt.commentThreads().list(part="snippet",videoId=video_id,maxResults=100,
                    order="time",textFormat="plainText").execute().get("items",[])
            except Exception as e:
                detail=str(e)
                try:
                    raw=getattr(e,"content",b"")
                    if raw:
                        payload=json.loads(raw.decode() if isinstance(raw,bytes) else raw)
                        err=payload.get("error",{})
                        detail=f"HTTP {getattr(e,'status_code',403)}: {err.get('message',detail)}"
                        reasons=[x.get("reason") for x in err.get("errors",[]) if x.get("reason")]
                        if reasons: detail+=" | Grund: "+", ".join(reasons)
                except Exception: pass
                log(f"Kommentare für Video übersprungen: {detail[:240]}")
                continue
            for it in items:
                top=it["snippet"]["topLevelComment"]; cid=top["id"]
                txt=re.sub(r"\s+"," ",top["snippet"].get("textDisplay","")).strip()
                if cid not in done and txt:
                    done.add(cid); out.append(txt[:300])
        state["processed_comments"]=list(done)[-1000:]
        log(f"{len(out)} neue Zuschauerideen")
        return out[:25]
    except Exception as e:
        log(f"Kommentare konnten nicht gelesen werden: {str(e)[:180]}")
        return []

def generate(state,comments):
    previous=state.get("history",[])[-30:]
    previous_stories=[{"episode":x.get("episode"),"title":x.get("title"),"story":x.get("story",""),"script":x.get("script","")[:700]} for x in previous]
    prompt=f"""Du bist Autor und Erzähler einer hochwertigen deutschen YouTube-Shorts-Serie. Es geht NICHT um eine Stadt, keinen Stadtaufbau und keine Gebäude. Erfinde spannende, eigenständige Geschichten mit interessanten Figuren und abwechslungsreichen Schauplätzen.
Jede Folge ist eine neue Episode mit einem völlig neuen Ereignis. Wiederhole niemals frühere Geschichten, Figuren-Konstellationen, Konflikte, Überraschungen, Hooks, Titel oder Szenen. Lies die letzten Episoden und vermeide auch nur leicht umformulierte Wiederholungen. Wechsle bewusst zwischen Abenteuer, Humor, Rätsel, Natur, Technik, Weltraum, Freundschaft, Geheimnissen und anderen harmlosen Themen.
VIDEO-AUFBAU: Beginne sofort mitten in einem spannenden Moment. Stelle eine Hauptfigur und ihr Ziel vor. Entwickle eine klare Handlung mit Hindernis und überraschender Wendung. Gib der Geschichte ein befriedigendes Ende oder einen starken Cliffhanger. Keine Begrüßung und keine lange Einleitung.
Zuschauerkommentare dürfen unverbindliche Ideen für spätere Geschichten sein. Keine echten Personen, privaten Daten oder gefährlichen Handlungen.
LETZTE FOLGEN – NICHT WIEDERHOLEN:
{json.dumps(previous_stories,ensure_ascii=False)}
NEUE ZUSCHAUERKOMMENTARE ALS OPTIONALE IDEEN:
{json.dumps(comments,ensure_ascii=False)}
Erstelle Folge {state.get('episode',0)+1}. Das Skript soll 50-65 deutsche Wörter haben. Die ersten zwei Sekunden brauchen einen individuellen Hook. Der gesamte Film MUSS durchgehend im hochwertigen Roblox-Story-Short-Stil aussehen: blockige Roblox-Avatare aus klaren Quadern (Kopf, Torso, Arme, Beine), glatte leicht plastikartige Oberflächen, einfache Texturen, Low-Poly-Optik. Hochformat 9:16. Die Figuren sollen sich wie Menschen bewegen: glaubwürdiges Gehen, Rennen, Sitzen, Greifen, Zeigen, Drehen, Reagieren und passende Gesichtsausdrücke, aber weiterhin mit Roblox-Körperbau und Roblox-Gelenken. KEIN Realfilm, KEINE Fotorealistik, KEIN anderer Animationsstil. KEINE Untertitel, KEINE eingeblendeten Wörter, KEINE Schrift, KEINE Texttafeln, KEINE Logos und KEINE Wasserzeichen. Erfinde 1-3 Hauptfiguren mit genauer, wiedererkennbarer Beschreibung von Haaren, Gesicht, Hautfarbe, Kleidung, Farben und Persönlichkeit; dieselben Figuren müssen in allen fünf Szenen exakt gleich aussehen. Erstelle genau 5 unterschiedliche animierte Szenen in richtiger Handlungsreihenfolge. Jede Szene muss konkret zeigen, WAS die Figuren gerade tun und WAS dadurch passiert. Wähle Ort, Hintergrund, Gegenstände, Wetter, Tageszeit und Licht ausschließlich passend zur jeweiligen Geschichte; verwende NICHT automatisch die Fahrradstraße, Vorstadthäuser oder das Fahrrad aus einer Beispielszene. Zeige keine beliebigen Standbilder: Figuren müssen sichtbar handeln und miteinander oder mit ihrer Umgebung interagieren. Wechsle passende Kameraperspektiven und beschreibe pro Szene Ort, sichtbare Aktion, Bewegung, wichtige Objekte und Kamerabewegung. Titel kurz, neugierig und nicht ähnlich zu früheren Titeln. Antworte nur mit JSON mit diesen Feldern: title (beginnt mit Folge {state.get('episode',0)+1}:), description (kurze Beschreibung als fiktive KI-Geschichte), script, story (1-2 Sätze über das neue Ereignis), visual_style (konkrete Beschreibung des einheitlichen Cartoon-Stils), characters (Liste mit name, appearance, clothing, personality), scenes (genau 5 kurze visuelle Szenen), hashtags (3-5)."""
    def parse_json(text):
        text=(text or "").strip()
        if text.startswith("```"):
            text=re.sub(r"^\`\`\`(?:json)?\s*","",text,flags=re.I)
            text=re.sub(r"\s*\`\`\`$","",text)
        left=text.find("{"); right=text.rfind("}")
        if left<0 or right<left: raise ValueError("KI lieferte kein JSON")
        return json.loads(text[left:right+1])

    def check(obj):
        keys=["title","description","script","scenes","story","visual_style","characters","hashtags"]
        if not isinstance(obj,dict) or any(k not in obj for k in keys):
            raise ValueError("KI-Ausgabe unvollständig")
        if not isinstance(obj["scenes"],list) or len(obj["scenes"])!=5:
            raise ValueError("Es müssen genau fünf Szenen sein")
        if not 45<=len(str(obj["script"]).split())<=80:
            raise ValueError("Skript muss zwischen 45 und 80 Wörtern haben, damit es zum 25-Sekunden-Cartoon passt")
        return obj

    # Gemini versuchen; bei Quota-Fehler automatisch zu OpenRouter wechseln.
    if GEMINI:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI)
            response=genai.GenerativeModel(MODEL).generate_content(prompt)
            raw=getattr(response,"text",None)
            if raw and raw.strip():
                obj=check(parse_json(raw))
                log("Gemini hat die Cartoon-Story geplant")
                return obj
            log("Gemini lieferte eine leere Antwort; OpenRouter wird versucht")
        except Exception as e:
            log(f"Gemini nicht verfügbar; OpenRouter wird versucht: {str(e)[:150]}")

    if ROUTER:
        # Nur aktuell gelistete kostenlose Textmodelle verwenden. Die Modellliste
        # ändert sich häufig; deshalb keine veralteten, fest codierten IDs.
        models=["openrouter/free"]
        try:
            req=urllib.request.Request("https://openrouter.ai/api/v1/models",
                headers={"Authorization":"Bearer "+ROUTER,"Accept":"application/json"})
            with urllib.request.urlopen(req,timeout=25) as response:
                catalog=json.loads(response.read().decode())
            for item in catalog.get("data",[]):
                model_id=item.get("id","")
                arch=item.get("architecture") or {}
                modalities=arch.get("output_modalities") or []
                pricing=item.get("pricing") or {}
                if (model_id.endswith(":free") and "text" in modalities
                    and str(pricing.get("prompt","")) in ("0","0.0","0.000000")
                    and str(pricing.get("completion","")) in ("0","0.0","0.000000")
                    and model_id not in models):
                    models.append(model_id)
                if len(models)>=6: break
            log(f"{len(models)-1} aktuell gelistete kostenlose Textmodelle als Fallback gefunden")
        except Exception as e:
            log(f"Aktuelle OpenRouter-Modellliste nicht abrufbar: {str(e)[:140]}")

        failures=[]
        for model in models:
            # Erst mit JSON-Modus versuchen. Einige kostenlose Provider unterstützen
            # response_format nicht; dann dieselbe Anfrage ohne diese Option wiederholen.
            base_payload={"model":model,"messages":[
                {"role":"system","content":"Du schreibst einen sicheren, originellen Cartoon-Kurzfilm. Antworte ausschließlich mit einem vollständigen JSON-Objekt."},
                {"role":"user","content":prompt}],
                "temperature":0.7,"max_tokens":3000}
            for json_mode in (True,False):
                try:
                    payload=dict(base_payload)
                    if json_mode: payload["response_format"]={"type":"json_object"}
                    req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                        data=json.dumps(payload).encode(),
                        headers={"Authorization":"Bearer "+ROUTER,"Content-Type":"application/json",
                        "HTTP-Referer":"https://github.com/hoffmannerik1-eng/AI","X-Title":"AI ERIK Stadt"},
                        method="POST")
                    with urllib.request.urlopen(req,timeout=100) as response:
                        data=json.loads(response.read().decode())
                    choices=data.get("choices") or []
                    message=(choices[0].get("message") or {}) if choices else {}
                    raw=message.get("content")
                    if isinstance(raw,list):
                        raw="".join(x.get("text","") for x in raw if isinstance(x,dict))
                    if not isinstance(raw,str) or not raw.strip():
                        raise ValueError("Modell lieferte leeren Inhalt")
                    obj=check(parse_json(raw))
                    log(f"OpenRouter-Modell {model} hat die Cartoon-Story erstellt")
                    return obj
                except Exception as e:
                    detail=str(e)
                    if isinstance(e,urllib.error.HTTPError):
                        try: detail=e.read().decode()[:260]
                        except Exception: pass
                    failures.append(f"{model} ({'JSON-Modus' if json_mode else 'Standard'}): {detail[:180]}")
                    # Wenn nur das JSON-Format nicht unterstützt wird, direkt
                    # ohne response_format erneut versuchen.
                    if json_mode and isinstance(e,urllib.error.HTTPError) and e.code in (400,404,422):
                        continue
                    break
            log(f"OpenRouter-Modell {model} nicht verfügbar; nächstes kostenloses Modell wird versucht")
        raise RuntimeError("Alle aktuell gelisteten kostenlosen KI-Modelle fehlgeschlagen: "+" | ".join(failures[-4:]))
    raise RuntimeError("Keine KI verfügbar. GEMINI_API_KEY oder OPENROUTER_API_KEY in GitHub Secrets prüfen.")

async def voice(txt,path): await edge_tts.Communicate(txt,"de-DE-ConradNeural").save(str(path))

def create_local_cartoon(raw,d,seconds):
    """Kostenloser Offline-Fallback: fünf animierte Roblox-inspirierte Szenen."""
    from PIL import Image, ImageDraw
    import math, shutil

    width,height,fps=540,960,12
    frames_dir=OUT/"local_frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    scenes=d.get("scenes") or [{} for _ in range(5)]
    characters=d.get("characters") or []
    if not characters:
        characters=[{"name":"Figur","appearance":"dunkle Haare","clothing":"blaues Oberteil"}]
    characters=characters[:2]
    palettes=[
        ((90,165,220),(210,235,250),(65,125,75)),
        ((255,170,95),(255,220,160),(105,125,75)),
        ((85,95,155),(175,185,225),(55,60,100)),
        ((65,165,170),(175,225,220),(35,105,115)),
        ((145,115,190),(220,190,230),(75,65,105))
    ]
    outfit_colors=[(45,115,220),(225,85,65),(70,165,110),(170,95,210)]
    skin=(235,190,150)

    def draw_avatar(draw,cx,base,idx,phase,scale=1.0):
        spec=characters[idx % len(characters)]
        clothing=str(spec.get("clothing","")).lower()
        color=outfit_colors[(sum(ord(c) for c in clothing)+idx*3)%len(outfit_colors)]
        sway=math.sin(phase*2.0+idx)*14*scale
        step=math.sin(phase*3.0+idx)*20*scale
        def rect(box,fill):
            draw.rectangle(tuple(int(v) for v in box),fill=fill)
        head_w,head_h=68*scale,72*scale
        head_x=cx-head_w/2
        head_y=base-300*scale
        # legs
        rect((cx-28*scale,base-125*scale,cx-5*scale+step,base), (35,45,70))
        rect((cx+5*scale-step,base-125*scale,cx+28*scale,base), (35,45,70))
        # torso and arms
        rect((cx-48*scale,base-245*scale,cx+48*scale,base-125*scale),color)
        rect((cx-78*scale+sway,base-238*scale,cx-50*scale+sway,base-135*scale),(color[0]//2,color[1]//2,color[2]//2))
        rect((cx+50*scale-sway,base-238*scale,cx+78*scale-sway,base-135*scale),(color[0]//2,color[1]//2,color[2]//2))
        draw.rectangle((cx-78*scale+sway,base-145*scale,cx-50*scale+sway,base-115*scale),fill=skin)
        draw.rectangle((cx+50*scale-sway,base-145*scale,cx+78*scale-sway,base-115*scale),fill=skin)
        # square head, hair and simple face
        rect((head_x,head_y,head_x+head_w,head_y+head_h),skin)
        hair=(55,42,35)
        rect((head_x,head_y,head_x+head_w,head_y+18*scale),hair)
        draw.rectangle((head_x,head_y+8*scale,head_x+13*scale,head_y+32*scale),fill=hair)
        draw.rectangle((head_x+head_w-13*scale,head_y+8*scale,head_x+head_w,head_y+32*scale),fill=hair)
        eye_y=head_y+39*scale
        draw.ellipse((cx-17*scale,eye_y,cx-9*scale,eye_y+8*scale),fill=(25,25,30))
        draw.ellipse((cx+9*scale,eye_y,cx+17*scale,eye_y+8*scale),fill=(25,25,30))
        draw.line((cx-8*scale,head_y+57*scale,cx+8*scale,head_y+57*scale),fill=(120,55,55),width=max(1,int(3*scale)))

    frame_no=0
    for scene_index in range(5):
        scene_text=str(scenes[scene_index] if scene_index < len(scenes) else "").lower()
        sky,glow,ground=palettes[scene_index]
        if any(w in scene_text for w in ("wald","forest","baum","dschungel")):
            sky,glow,ground=(90,155,190),(190,220,150),(65,115,55)
        elif any(w in scene_text for w in ("meer","ocean","wasser","strand","boot")):
            sky,glow,ground=(75,155,205),(190,225,235),(35,125,165)
        elif any(w in scene_text for w in ("weltall","space","planet","mond","stern")):
            sky,glow,ground=(18,22,60),(95,85,150),(40,42,85)
        elif any(w in scene_text for w in ("schule","stadt","haus","straße","labor")):
            sky,glow,ground=(125,175,220),(220,230,235),(100,110,125)
        count=max(1,round((seconds/5)*fps))
        for local_frame in range(count):
            phase=local_frame/max(1,count-1)*math.pi*2
            img=Image.new("RGB",(width,height),sky)
            draw=ImageDraw.Draw(img)
            # Lichtschein und einfache, zur Szene passende Umgebung
            draw.ellipse((330,90,455,215),fill=glow)
            for cloud_x in (45,245,445):
                offset=int(math.sin(phase+cloud_x)*12)
                draw.ellipse((cloud_x-45+offset,190,cloud_x+45+offset,225),fill=(235,240,245))
                draw.ellipse((cloud_x-20+offset,170,cloud_x+25+offset,225),fill=(235,240,245))
            if "weltall" in scene_text or "space" in scene_text or "planet" in scene_text:
                for sx,sy in ((55,110),(160,280),(420,330),(480,500),(100,450)):
                    draw.ellipse((sx,sy,sx+5,sy+5),fill=(255,255,255))
                draw.ellipse((355,270,490,405),fill=(145,115,190))
                draw.ellipse((375,290,470,390),fill=(100,90,160))
            elif any(w in scene_text for w in ("wald","forest","baum","dschungel")):
                for tx in (35,470):
                    draw.rectangle((tx+20,480,tx+45,720),fill=(105,70,45))
                    draw.ellipse((tx-35,380,tx+100,535),fill=(45,125,65))
            elif any(w in scene_text for w in ("meer","ocean","wasser","strand","boot")):
                for wy in (560,610,660):
                    draw.arc((0,wy,600,wy+70),0,180,fill=(190,235,245),width=7)
            else:
                draw.rectangle((55,430,160,680),fill=(175,185,195))
                draw.rectangle((75,455,105,505),fill=(100,165,220))
                draw.rectangle((115,455,145,505),fill=(100,165,220))
                draw.rectangle((365,390,475,680),fill=(195,180,155))
                draw.polygon(((350,395),(420,315),(490,395)),fill=(125,65,65))
            draw.rectangle((0,700,width,height),fill=ground)
            # Figuren bleiben über alle Szenen hinweg gleich und bewegen sich sichtbar.
            for ci in range(len(characters)):
                cx=width*(0.42 if ci==0 else 0.70)+math.sin(phase+ci)*24
                draw_avatar(draw,cx,785,ci,phase+scene_index*0.8,1.0)
            # Ein animiertes Objekt pro Szene als sichtbarer Handlungspunkt.
            obj_x=int(width*0.5+math.sin(phase*1.4)*90)
            draw.ellipse((obj_x-24,625,obj_x+24,673),fill=(255,215,65),outline=(255,245,180),width=4)
            img.save(frames_dir/f"frame_{frame_no:05d}.png",quality=90)
            frame_no+=1
    subprocess.run(["ffmpeg","-y","-loglevel","error","-framerate",str(fps),
        "-i",str(frames_dir/"frame_%05d.png"),"-c:v","libx264","-preset","ultrafast",
        "-crf","24","-pix_fmt","yuv420p","-r","24",str(raw)],check=True)
    log("Lokaler Gratis-Fallback hat fünf animierte Szenen erstellt.")

def make_video(d):
    audio=OUT/"voice.mp3"; asyncio.run(voice(d["script"],audio))
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)],capture_output=True,text=True,check=True)
    seconds=max(1,float(p.stdout.strip()))
    video=OUT/"story_episode.mp4"
    music=OUT/"ambient.wav"
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",
        f"aevalsrc=0.020*sin(2*PI*110*t)+0.014*sin(2*PI*164.81*t)+0.010*sin(2*PI*220*t):s=44100:d={seconds:.3f}",
        "-af",f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0,seconds-2):.3f}:d=2",
        "-c:a","pcm_s16le",str(music)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    agnes_key=os.getenv("AGNES_API_KEY","").strip()
    raw=OUT/"video_ai_combined.mp4"

    # Agnes ist der Hauptanbieter. Magic Hour wird nur bei einem echten
    # Agnes-Fehler versucht und prüft selbst Freigabe sowie Episoden-Creditlimit.
    try:
        if not agnes_key:
            raise RuntimeError("AGNES_API_KEY fehlt.")
        log("Agnes startet fünf animierte Szenen.")
        generate_agnes_video(d,OUT,raw,clip_duration=5)
        log("Agnes hat alle fünf Szenen fertiggestellt.")
    except AgnesGenerationDisabled as exc:
        log("Agnes ist absichtlich deaktiviert: "+str(exc)[:220])
        log("Es wird kein kostenpflichtiger Ersatzanbieter gestartet; lokaler Gratis-Fallback folgt.")
        create_local_cartoon(raw,d,seconds)
    except Exception as agnes_exc:
        log("Agnes ist fehlgeschlagen: "+str(agnes_exc)[:300])
        log("Magic Hour wird als Ersatz versucht; Freigabe und Creditlimit werden geprüft.")
        try:
            generate_magic_hour_video(d,OUT,raw,clip_duration=5)
            log("Magic Hour hat alle fünf Ersatzszenen fertiggestellt.")
        except FreeCreditsUnavailable as mh_exc:
            log("Magic Hour nicht verfügbar (z.B. zu wenig Credits): "+str(mh_exc)[:260])
            log("Keine weiteren Credits werden angefordert; lokaler Gratis-Fallback folgt.")
            create_local_cartoon(raw,d,seconds)
        except Exception as mh_exc:
            log("Magic Hour fehlgeschlagen: "+str(mh_exc)[:260])
            log("Lokaler Gratis-Fallback folgt.")
            create_local_cartoon(raw,d,seconds)

    # Gemeinsame Tonmischung für beide Videoanbieter.
    subprocess.run(["ffmpeg","-y","-i",str(raw),"-i",str(audio),"-i",str(music),
        "-filter_complex","[1:a]volume=1.0[voice];[2:a]volume=0.22[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
        "-map","0:v:0","-map","[a]","-vf","scale=1080:1920:flags=lanczos,format=yuv420p",
        "-c:v","libx264","-preset","medium","-crf","21","-r","24","-c:a","aac","-b:a","192k",
        "-shortest","-movflags","+faststart",str(video)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    return video

def verify_video(video):
    """Prüft das fertige MP4, bevor es an YouTube übergeben wird."""
    probe=subprocess.run(["ffprobe","-v","error","-show_entries",
        "stream=codec_type,width,height","-show_entries","format=duration",
        "-of","json",str(video)],capture_output=True,text=True,check=True)
    info=json.loads(probe.stdout)
    streams=info.get("streams",[])
    picture=next((s for s in streams if s.get("codec_type")=="video"),None)
    sound=any(s.get("codec_type")=="audio" for s in streams)
    seconds=float(info.get("format",{}).get("duration",0))
    if not picture or picture.get("width")!=1080 or picture.get("height")!=1920:
        raise RuntimeError("Video-Prüfung fehlgeschlagen: Format ist nicht 1080x1920.")
    if not sound:
        raise RuntimeError("Video-Prüfung fehlgeschlagen: Tonspur fehlt.")
    if not 3<=seconds<=180:
        raise RuntimeError(f"Video-Prüfung fehlgeschlagen: Länge {seconds:.1f}s liegt außerhalb 3-180s.")
    if Path(video).stat().st_size<50000:
        raise RuntimeError("Video-Prüfung fehlgeschlagen: MP4 ist unerwartet klein.")
    log(f"Video geprüft: 1080x1920 | {seconds:.1f}s | Tonspur vorhanden")

def upload(video,d):
    yt=yt_service()
    if not yt: raise RuntimeError("YouTube-OAuth-Secrets fehlen")
    body={"snippet":{"title":d["title"][:100],"description":d["description"]+"\\n\\n"+" ".join(d["hashtags"]),"categoryId":"20","defaultLanguage":"de"},
        "status":{"privacyStatus":PRIVACY if PRIVACY in ("private","unlisted","public") else "private"}}
    r=yt.videos().insert(part="snippet,status",body=body,media_body=MediaFileUpload(str(video),mimetype="video/mp4",resumable=True)).execute()
    return r.get("id")

def main():
    log("AI ERIK STORY-AUTOPILOT gestartet")
    state=load_state()
    ideas=get_comments(state)
    d=generate(state,ideas)
    d["episode"]=int(state.get("episode",0))+1
    log(f"Neue Geschichte | Folge {d['episode']} | {d['title']}")
    video=make_video(d)
    verify_video(video)
    dry=os.getenv("DRY_RUN","true").lower() in ("1","true","yes")
    vid=None if dry else upload(video,d)
    state.update({"episode":d["episode"]})
    state.setdefault("history",[]).append({"episode":d["episode"],"title":d["title"],"story":d.get("story",""),"script":d.get("script",""),"change":d.get("story",""),"video_id":vid,"created_at":datetime.now(timezone.utc).isoformat()})
    state["history"]=state["history"][-100:]
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"latest.json").write_text(json.dumps({"title":d["title"],"episode":d["episode"],"video_id":vid,"story":d.get("story","")},ensure_ascii=False,indent=2),encoding="utf-8")
    log("Geschichte fertig; Episodenverlauf gespeichert. "+("Video wartet als Artifact auf Freigabe." if dry else "Video wurde hochgeladen."))

if __name__=="__main__": main()
