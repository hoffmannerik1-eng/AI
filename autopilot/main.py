import os,re,json,asyncio,subprocess,math,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from PIL import Image,ImageDraw,ImageFont
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT=Path(__file__).parent
OUT=ROOT/"output"; OUT.mkdir(exist_ok=True)
STATE=ROOT/"city_state.json"
GEMINI=os.getenv("GEMINI_API_KEY","")
ROUTER=os.getenv("OPENROUTER_API_KEY","")
MODEL=os.getenv("GEMINI_MODEL","gemini-3.8-flash")
PRIVACY=os.getenv("YOUTUBE_PRIVACY_STATUS","private")

def log(s): print(f"[{datetime.now(timezone.utc).isoformat()}] {s}",flush=True)
def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"city_name":"Neustadt","episode":0,"population":1,"buildings":[
        {"name":"Rathaus","type":"rathaus","level":1,"x":0,"y":0}],"history":[],"processed_comments":[]}

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
                log(f"Kommentare für Video übersprungen: {str(e)[:120]}")
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
    prompt=f"""Du bist die kreative Stadt-KI einer fortlaufenden fiktiven Stadt für deutsche YouTube Shorts.
Entwickle dieselbe fiktive Stadt über viele Folgen weiter. Zu Beginn gibt es AUSSCHLIESSLICH ein Rathaus. Die Stadt darf ausschließlich durch passende Zuschauerideen aus den neuen Kommentaren wachsen: Baue ein Gebäude oder verbessere etwas nur, wenn ein konkreter Kommentar genau das vorschlägt. Ohne passenden Kommentar darf kein Gebäude hinzukommen und kein Gebäude verbessert werden. Wenn es keine passenden Kommentare gibt, erzähle eine kleine Geschichte rund um die vorhandenen Gebäude, ohne die Stadt baulich zu verändern. Ignoriere private, beleidigende, gefährliche oder unpassende Wünsche. Keine echten Personen oder privaten Daten.
STADTSTAND: {json.dumps(state,ensure_ascii=False)}
NEUE KOMMENTARE: {json.dumps(comments,ensure_ascii=False)}
Erstelle Folge {state.get('episode',0)+1}. Beginne das Skript mit einem starken neugierig machenden Hook in den ersten 2 Sekunden. Erzähle konkret und bildhaft, mit abwechslungsreichem Tempo und einem klaren Spannungsbogen. Beende es mit einer kurzen Frage, die zu einem konkreten Bauvorschlag in den Kommentaren einlädt. Szenen 1-5 müssen jeweils eine klar erkennbare visuelle Aktion beschreiben. Maximal 2 neue Gebäude/Upgrades je Folge; bestehende Gebäude beibehalten. Antworte NUR JSON mit:
city_name, title (beginnt mit Unsere Stadt #{state.get('episode',0)+1}:), description (Hinweis fiktive KI-Stadt), script (75-105 deutsche Wörter), scenes (genau 5 kurze Szenen), buildings (vollständige Liste max 18; jedes Objekt name,type aus rathaus/haus/hochhaus/cafe/schule/park/shop/feuerwehr/polizei/krankenhaus/bahnhof/stadion/schwimmbad/bruecke, level 1-3, x und y ganze Zahlen -3 bis 3; keine doppelten Koordinaten), population (50-50000), change_summary, hashtags (3-5)."""
    def parse_json(text):
        text=(text or "").strip()
        if text.startswith("```"):
            text=re.sub(r"^\`\`\`(?:json)?\s*","",text,flags=re.I)
            text=re.sub(r"\s*\`\`\`$","",text)
        left=text.find("{"); right=text.rfind("}")
        if left<0 or right<left: raise ValueError("KI lieferte kein JSON")
        return json.loads(text[left:right+1])

    def check(obj):
        keys=["city_name","title","description","script","scenes","buildings","population","change_summary","hashtags"]
        if not isinstance(obj,dict) or any(k not in obj for k in keys):
            raise ValueError("KI-Ausgabe unvollständig")
        if not isinstance(obj["scenes"],list) or len(obj["scenes"])!=5:
            raise ValueError("Es müssen genau fünf Szenen sein")
        if not isinstance(obj["buildings"],list) or not 45<=len(str(obj["script"]).split())<=140:
            raise ValueError("Gebäude oder Skript ungültig")
        obj["buildings"]=obj["buildings"][:18]
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
                log("Gemini hat die Stadtfolge geplant")
                if not comments: obj["buildings"]=state.get("buildings",[{"name":"Rathaus","type":"rathaus","level":1,"x":0,"y":0}])
                return obj
            log("Gemini lieferte eine leere Antwort; OpenRouter wird versucht")
        except Exception as e:
            log(f"Gemini nicht verfügbar; OpenRouter wird versucht: {str(e)[:150]}")

    if ROUTER:
        configured=os.getenv("OPENROUTER_MODEL","openrouter/free")
        models=[]
        for model in [configured,"openai/gpt-oss-20b:free","google/gemma-3-27b-it:free",
                      "meta-llama/llama-3.3-70b-instruct:free","qwen/qwen3-4b:free"]:
            if model and model not in models: models.append(model)
        failures=[]
        for model in models:
            try:
                payload={"model":model,"messages":[
                    {"role":"system","content":"Du erstellst eine sichere fiktive Stadt. Antworte ausschließlich mit einem vollständigen JSON-Objekt."},
                    {"role":"user","content":prompt}],
                    "temperature":0.7,"max_tokens":2600,"response_format":{"type":"json_object"}}
                req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization":"Bearer "+ROUTER,"Content-Type":"application/json",
                    "HTTP-Referer":"https://github.com/hoffmannerik1-eng/AI","X-Title":"AI ERIK Stadt"},
                    method="POST")
                with urllib.request.urlopen(req,timeout=90) as response:
                    data=json.loads(response.read().decode())
                choices=data.get("choices") or []
                message=(choices[0].get("message") or {}) if choices else {}
                raw=message.get("content")
                if isinstance(raw,list):
                    raw="".join(x.get("text","") for x in raw if isinstance(x,dict))
                if not isinstance(raw,str) or not raw.strip():
                    raise ValueError("Modell lieferte leeren Inhalt")
                obj=check(parse_json(raw))
                log(f"OpenRouter-Modell {model} hat die Stadtfolge erstellt")
                if not comments: obj["buildings"]=state.get("buildings",[{"name":"Rathaus","type":"rathaus","level":1,"x":0,"y":0}])
                return obj
            except Exception as e:
                detail=str(e)
                if isinstance(e,urllib.error.HTTPError):
                    try: detail=e.read().decode()[:180]
                    except Exception: pass
                failures.append(f"{model}: {detail[:120]}")
                log(f"OpenRouter-Modell {model} nicht verfügbar; nächstes Modell wird versucht")
        raise RuntimeError("Alle KI-Modelle fehlgeschlagen: "+" | ".join(failures[-3:]))
    raise RuntimeError("Keine KI verfügbar. GEMINI_API_KEY oder OPENROUTER_API_KEY in GitHub Secrets prüfen.")

def font(n,b=False):
    p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if b else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p,n) if Path(p).exists() else ImageFont.load_default()

def render(d,duration,fps=24):
    # Vertikales Shorts-Layout: klare Szenen, animierte Stadt und gut lesbare Texteinblendungen.
    w,h=720,1280; total=max(1,math.ceil(duration*fps)); folder=OUT/"frames"; folder.mkdir(exist_ok=True)
    for p in folder.glob("frame_*.png"): p.unlink()
    colors={"rathaus":(75,145,205),"haus":(80,180,130),"hochhaus":(80,115,200),"cafe":(210,135,80),"schule":(210,185,75),"park":(55,145,85),"shop":(175,95,145),"feuerwehr":(195,65,65),"polizei":(70,105,180),"krankenhaus":(205,220,225),"bahnhof":(120,130,150),"stadion":(65,155,135),"schwimmbad":(50,155,215),"bruecke":(140,125,105)}
    def pt(x,y,z=0,cam=0):
        return (360+(x-y)*55+cam,690+(x+y)*28-z)
    def poly(draw,pts,fill,outline=(15,35,45)):
        draw.polygon(pts,fill=fill,outline=outline)
    scenes=d.get("scenes",["Willkommen in unserer Stadt"]*5)
    hook=str(scenes[0])[:75]
    for n in range(total):
        t=n/fps; progress=n/max(1,total-1); si=min(4,int(progress*5))
        # Weicher Himmel mit Farbverlauf.
        im=Image.new("RGB",(w,h),(8,18,34)); q=ImageDraw.Draw(im)
        for yy in range(0,470):
            k=yy/470
            col=(int(7+10*k),int(17+30*k),int(34+38*k))
            q.line((0,yy,w,yy),fill=col)
        # Sterne und langsam ziehende Wolken.
        for k in range(42):
            x=(k*101+int(t*(3+k%3)))%720; y=(k*47)%380
            q.ellipse((x,y,x+2,y+2),fill=(95,150,175))
        for k in range(4):
            cx=(k*220+int(t*8))%900-90; cy=170+k%2*85
            q.ellipse((cx,cy,cx+100,cy+18),fill=(19,43,62))
            q.ellipse((cx+25,cy-10,cx+85,cy+20),fill=(19,43,62))
        # Mond / Lichtschein.
        q.ellipse((520,95,635,210),fill=(28,67,88),outline=(80,180,190),width=3)
        q.ellipse((545,105,625,185),fill=(150,215,205))
        # Header mit Folge- und Serienbranding.
        q.rounded_rectangle((28,25,692,118),radius=22,fill=(8,24,40),outline=(55,205,150),width=2)
        q.text((52,43),"AI ERIK  /  STADT-UPDATE",font=font(24,True),fill=(80,255,175))
        q.text((52,79),f"{d['city_name']}   •   FOLGE {d['episode']}",font=font(17),fill=(205,225,230))
        # Leuchtende Stadtfläche und isometrische Straßen.
        poly(q,[(360,410),(690,585),(360,790),(30,585)],(34,72,78),outline=(65,135,130))
        for k in range(-3,4):
            q.line((pt(k,-3),pt(k,3)),fill=(64,112,108),width=2)
            q.line((pt(-3,k),pt(3,k)),fill=(64,112,108),width=2)
        # Kleine Straßenmarkierungen.
        for k in range(7):
            xx=90+k*90; yy=590+int(15*math.sin(t*1.5+k))
            q.ellipse((xx,yy,xx+4,yy+4),fill=(115,190,160))
        # Gebäude und Parks.
        cam=int(5*math.sin(t*.35))
        for b in sorted(d["buildings"],key=lambda z:int(z.get("x",0))+int(z.get("y",0))):
            x,y=pt(int(b.get("x",0)),int(b.get("y",0)),cam=cam); typ=b.get("type","haus"); lev=max(1,min(3,int(b.get("level",1))))
            if typ=="park":
                poly(q,[(x,y-12),(x+36,y+6),(x,y+25),(x-36,y+6)],(45,135,78))
                for tx in (-18,12):
                    q.ellipse((x+tx-13,y-47,x+tx+13,y-21),fill=(70,190,100))
                    q.rectangle((x+tx-4,y-24,x+tx+4,y+8),fill=(110,75,45))
                continue
            if typ=="bruecke":
                poly(q,[(x-42,y),(x,y+21),(x+42,y),(x,y-21)],colors[typ]); continue
            col=colors.get(typ,colors["haus"]); height=36+lev*22; wd=34
            left=tuple(int(v*.62) for v in col); top=tuple(min(255,int(v*1.18)) for v in col)
            poly(q,[(x-wd,y-height),(x,y-height+20),(x,y+20),(x-wd,y)],left)
            poly(q,[(x,y-height+20),(x+wd,y-height),(x+wd,y),(x,y+20)],col)
            poly(q,[(x,y-height-20),(x+wd,y-height),(x,y-height+20),(x-wd,y-height)],top)
            # Leuchtende Fenster mit leichtem Flackern.
            for row in range(lev):
                yy=y-height+32+row*22
                if yy<y-3:
                    glow=245 if (n//9+row+int(x))%7 else 185
                    q.rectangle((x-25,yy,x-17,yy+10),fill=(glow,225,145))
                    q.rectangle((x+13,yy+5,x+21,yy+15),fill=(glow,225,145))
        # Bewegte Lichtpunkte als kleine Stadtfahrzeuge.
        for j in range(8):
            x=70+((j*83+int(t*34))%580); y=560+int(75*math.sin(t*.8+j))
            q.ellipse((x-3,y-3,x+4,y+4),fill=(100,255,190))
        # Szenenkarte mit stärkerem Kontrast und Textumbruch.
        q.rounded_rectangle((28,865,692,1150),radius=26,fill=(7,22,38),outline=(55,220,160),width=3)
        q.text((55,892),f"STADTENTWICKLUNG  {si+1}/5",font=font(18,True),fill=(80,255,175))
        words=str(scenes[si]).split(); lines=[]; line=""
        for word in words:
            if len(line)+len(word)+1>27:
                if line: lines.append(line)
                line=word
            else: line=(line+" "+word).strip()
        if line: lines.append(line)
        yy=940
        for line in lines[:3]:
            q.text((55,yy),line,font=font(30,True),fill=(248,252,250)); yy+=43
        q.text((55,1090),"EINWOHNER  "+f"{int(d['population']):,}".replace(",","."),font=font(18,True),fill=(170,220,215))
        # Fortschrittsanzeige.
        q.rounded_rectangle((30,1190,690,1202),radius=6,fill=(30,55,68))
        q.rounded_rectangle((30,1190,30+int(660*progress),1202),radius=6,fill=(65,245,165))
        # Schneller, klarer Einstieg und Kommentar-Aufruf am Ende.
        if t<2.4:
            q.rounded_rectangle((28,145,692,265),radius=22,fill=(7,24,40),outline=(90,255,180),width=3)
            q.text((52,165),"WAS PASSIERT IN UNSERER STADT?",font=font(20,True),fill=(80,255,175))
            q.text((52,205),hook[:36],font=font(23,True),fill=(255,255,255))
        if t>max(0,duration-2.8):
            q.rounded_rectangle((42,790,678,850),radius=16,fill=(8,35,42),outline=(95,255,175),width=2)
            q.text((62,800),"DEINE IDEE BAUT DIE NÄCHSTE FOLGE!",font=font(17,True),fill=(95,255,180))
            q.text((62,825),"Welches Gebäude soll als Nächstes kommen?",font=font(14),fill=(245,250,250))
        im.save(folder/f"frame_{n:05d}.png",optimize=True)
    return folder

async def voice(txt,path): await edge_tts.Communicate(txt,"de-DE-ConradNeural").save(str(path))
def make_video(d):
    audio=OUT/"voice.mp3"; asyncio.run(voice(d["script"],audio))
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)],capture_output=True,text=True,check=True)
    seconds=max(1,float(p.stdout.strip())); frames=render(d,seconds)
    video=OUT/"stadtfolge.mp4"
    # Eigener, synthetisch erzeugter Ambient-Soundtrack (keine fremde Musik).
    music=OUT/"ambient.wav"
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",
        f"aevalsrc=0.020*sin(2*PI*110*t)+0.014*sin(2*PI*164.81*t)+0.010*sin(2*PI*220*t):s=44100:d={seconds:.3f}",
        "-af",f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0,seconds-2):.3f}:d=2",
        "-c:a","pcm_s16le",str(music)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    subprocess.run(["ffmpeg","-y","-framerate","24","-i",str(frames/"frame_%05d.png"),"-i",str(audio),"-i",str(music),
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
    log("AI ERIK STADT-AUTOPILOT gestartet"); state=load_state(); ideas=get_comments(state)
    d=generate(state,ideas); d["episode"]=int(state.get("episode",0))+1
    log(f"{d['city_name']} | Folge {d['episode']} | {len(d['buildings'])} Gebäude")
    video=make_video(d); verify_video(video); dry=os.getenv("DRY_RUN","true").lower() in ("1","true","yes")
    vid=None if dry else upload(video,d)
    state.update({"city_name":d["city_name"],"episode":d["episode"],"population":d["population"],"buildings":d["buildings"]})
    state.setdefault("history",[]).append({"episode":d["episode"],"title":d["title"],"change":d["change_summary"],"video_id":vid,"created_at":datetime.now(timezone.utc).isoformat()})
    state["history"]=state["history"][-100:]; STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"latest.json").write_text(json.dumps({"title":d["title"],"episode":d["episode"],"city_name":d["city_name"],"video_id":vid,"change":d["change_summary"]},ensure_ascii=False,indent=2),encoding="utf-8")
    log("Folge fertig; Stadtstand gespeichert. "+("Video wartet als Artifact auf Freigabe." if dry else "Video wurde hochgeladen."))

if __name__=="__main__": main()
