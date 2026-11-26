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
        raise RuntimeError("Agnes ist bewusst nicht freigegeben. Es wird kein anderer Anbieter automatisch gestartet: "+str(exc)) from exc
    except Exception as agnes_exc:
        log("Agnes ist fehlgeschlagen: "+str(agnes_exc)[:300])
        log("Magic Hour wird als Ersatz versucht; Freigabe und Creditlimit werden geprüft.")
        try:
            generate_magic_hour_video(d,OUT,raw,clip_duration=5)
            log("Magic Hour hat alle fünf Ersatzszenen fertiggestellt.")
        except FreeCreditsUnavailable as mh_exc:
            raise RuntimeError("Agnes ist fehlgeschlagen; Magic Hour wurde durch die Sicherheits-/Creditgrenze gestoppt: "+str(mh_exc)) from mh_exc
        except Exception as mh_exc:
            raise RuntimeError("Beide Videoanbieter sind fehlgeschlagen. Agnes: "+str(agnes_exc)[:220]+" | Magic Hour: "+str(mh_exc)[:220]) from mh_exc

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
