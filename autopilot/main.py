import os,re,json,asyncio,subprocess,math,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from PIL import Image,ImageDraw,ImageFont
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from video_provider import generate_video as generate_magic_hour_video, FreeCreditsUnavailable

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
Erstelle Folge {state.get('episode',0)+1}. Das Skript soll 90-120 deutsche Wörter haben. Die ersten zwei Sekunden brauchen einen individuellen Hook. Erstelle genau 5 visuelle Szenen, die die Handlung in der richtigen Reihenfolge zeigen. Jede Szene braucht einen konkreten Schauplatz, Figuren und eine sichtbare Aktion. Keine Stadt- oder Gebäudebau-Simulation. Titel kurz, neugierig und nicht ähnlich zu früheren Titeln. Antworte nur mit JSON mit diesen Feldern: title (beginnt mit Folge {state.get('episode',0)+1}:), description (kurze Beschreibung als fiktive KI-Geschichte), script, story (1-2 Sätze über das neue Ereignis), scenes (genau 5 kurze visuelle Szenen), hashtags (3-5)."""
    def parse_json(text):
        text=(text or "").strip()
        if text.startswith("```"):
            text=re.sub(r"^\`\`\`(?:json)?\s*","",text,flags=re.I)
            text=re.sub(r"\s*\`\`\`$","",text)
        left=text.find("{"); right=text.rfind("}")
        if left<0 or right<left: raise ValueError("KI lieferte kein JSON")
        return json.loads(text[left:right+1])

    def check(obj):
        keys=["title","description","script","scenes","story","hashtags"]
        if not isinstance(obj,dict) or any(k not in obj for k in keys):
            raise ValueError("KI-Ausgabe unvollständig")
        if not isinstance(obj["scenes"],list) or len(obj["scenes"])!=5:
            raise ValueError("Es müssen genau fünf Szenen sein")
        if not 80<=len(str(obj["script"]).split())<=150:
            raise ValueError("Skript muss zwischen 80 und 150 Wörtern haben")
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
                {"role":"system","content":"Du erstellst eine sichere fiktive Stadt. Antworte ausschließlich mit einem vollständigen JSON-Objekt."},
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
                    log(f"OpenRouter-Modell {model} hat die Stadtfolge erstellt")
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

def font(n,b=False):
    p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if b else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p,n) if Path(p).exists() else ImageFont.load_default()

def render(d,duration,fps=24):
    # Story-Video ohne Stadtkarte, Gebäude oder Einwohneranzeige.
    w,h=720,1280; total=max(1,math.ceil(duration*fps)); folder=OUT/"frames"; folder.mkdir(exist_ok=True)
    for p in folder.glob("frame_*.png"): p.unlink()
    scenes=d.get("scenes",["Eine neue Geschichte beginnt"]*5); title=str(d.get("title","Eine neue Geschichte")); hook=str(d.get("script",""))[:85]
    def wrap(value,limit):
        words=str(value).split(); lines=[]; line=""
        for word in words:
            if len(line)+len(word)+1>limit:
                if line: lines.append(line)
                line=word
            else: line=(line+" "+word).strip()
        if line: lines.append(line)
        return lines
    for n in range(total):
        t=n/fps; progress=n/max(1,total-1); si=min(4,int(progress*5))
        im=Image.new("RGB",(w,h),(7,13,28)); q=ImageDraw.Draw(im)
        for yy in range(h):
            k=yy/h; q.line((0,yy,w,yy),fill=(int(7+8*k),int(14+10*k),int(29+20*k)))
        for j in range(70):
            x=(j*97+int(t*(5+j%4)))%w; y=(j*61)%h; rr=1+(j%3)
            q.ellipse((x-rr,y-rr,x+rr,y+rr),fill=(35,75+(j%5)*12,105+(j%4)*18))
        shift=int(24*math.sin(t*.7)); q.ellipse((420+shift,170,760+shift,510),fill=(13,35,59)); q.ellipse((-180-shift,650,220-shift,1080),fill=(12,29,50))
        q.rounded_rectangle((28,28,692,145),radius=24,fill=(8,22,40),outline=(65,210,165),width=2)
        q.text((52,48),"AI ERIK  /  STORY",font=font(23,True),fill=(80,255,175))
        for idx,line in enumerate(wrap(title,42)[:2]): q.text((52,88+idx*29),line,font=font(19,True),fill=(245,250,255))
        q.rounded_rectangle((30,300,690,940),radius=30,fill=(9,24,43),outline=(58,135,155),width=3)
        q.text((60,340),f"KAPITEL  {si+1}/5",font=font(20,True),fill=(80,255,175)); q.line((60,390,660,390),fill=(55,105,125),width=2)
        yy=450
        for line in wrap(scenes[si],31)[:8]:
            q.text((60,yy),line,font=font(29,True),fill=(248,250,255)); yy+=55
        q.rounded_rectangle((30,1190,690,1204),radius=7,fill=(27,49,67)); q.rounded_rectangle((30,1190,30+int(660*progress),1204),radius=7,fill=(65,245,165))
        if t<2.5:
            q.rounded_rectangle((30,175,690,270),radius=20,fill=(8,28,46),outline=(70,235,175),width=2)
            q.text((55,193),"WAS PASSIERT ALS NÄCHSTES?",font=font(19,True),fill=(80,255,175))
            for idx,line in enumerate(wrap(hook,43)[:2]): q.text((55,225+idx*27),line,font=font(17,True),fill=(255,255,255))
        if t>max(0,duration-2.5):
            q.rounded_rectangle((55,1000,665,1080),radius=18,fill=(8,32,45),outline=(75,225,170),width=2)
            q.text((82,1022),"Welche Geschichte soll als Nächstes kommen?",font=font(18,True),fill=(235,250,245))
        im.save(folder/f"frame_{n:05d}.png",optimize=True)
    return folder

async def voice(txt,path): await edge_tts.Communicate(txt,"de-DE-ConradNeural").save(str(path))
def make_video(d):
    audio=OUT/"voice.mp3"; asyncio.run(voice(d["script"],audio))
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)],capture_output=True,text=True,check=True)
    seconds=max(1,float(p.stdout.strip()))
    video=OUT/"stadtfolge.mp4"
    music=OUT/"ambient.wav"
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",
        f"aevalsrc=0.020*sin(2*PI*110*t)+0.014*sin(2*PI*164.81*t)+0.010*sin(2*PI*220*t):s=44100:d={seconds:.3f}",
        "-af",f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0,seconds-2):.3f}:d=2",
        "-c:a","pcm_s16le",str(music)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    magic_key=os.getenv("MAGIC_HOUR_API_KEY","").strip()
    if magic_key:
        log("Magic Hour Free-Video-KI wird geprüft.")
        raw=OUT/"magic_hour_combined.mp4"
        try:
            generate_magic_hour_video(d,OUT,raw,clip_duration=max(5,math.ceil(seconds/5)))
            subprocess.run(["ffmpeg","-y","-i",str(raw),"-i",str(audio),"-i",str(music),
                "-filter_complex","[1:a]volume=1.0[voice];[2:a]volume=0.22[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map","0:v:0","-map","[a]","-vf","scale=1080:1920:flags=lanczos,format=yuv420p",
                "-c:v","libx264","-preset","medium","-crf","21","-r","24","-c:a","aac","-b:a","192k",
                "-shortest","-movflags","+faststart",str(video)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
            return video
        except FreeCreditsUnavailable as exc:
            log(str(exc))
            log("Kostenlose Credits reichen nicht; lokale Animation wird als Fallback erstellt.")
        except Exception as exc:
            log("Magic Hour konnte nicht verwendet werden: " + str(exc))
            log("Die Folge wird mit der lokalen Animation fertiggestellt, damit der Workflow nicht abbricht.")
    else:
        log("MAGIC_HOUR_API_KEY fehlt: lokale Animations-Fallback-Version wird verwendet.")
    frames=render(d,seconds)
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
