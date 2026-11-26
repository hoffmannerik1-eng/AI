import os,re,json,asyncio,subprocess,math,urllib.request,time,shutil
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

def research_shorts_trends(state=None):
    """Breite, zeitlich begrenzte Trendbeobachtung über viele Shorts-Themen."""
    import urllib.parse
    import feedparser
    queries = ["trending YouTube Shorts","funny viral Shorts","gaming Shorts trend","Minecraft Shorts",
        "Roblox Shorts","AI animation Shorts","animated story Shorts","meme Shorts trend",
        "satisfying videos Shorts","animals funny Shorts","football skills Shorts","science facts Shorts",
        "DIY creative Shorts","food viral Shorts","technology Shorts","comedy skits Shorts",
        "mystery story Shorts","German viral Shorts","creative editing Shorts","popular sound Shorts"]
    episode=int((state or {}).get("episode",0)); shift=(episode*3)%len(queries); queries=queries[shift:]+queries[:shift]
    try: minutes=max(10,min(15,int(os.getenv("TREND_RESEARCH_MINUTES","10"))))
    except ValueError: minutes=10
    started=time.monotonic(); deadline=started+minutes*60; observations=[]; seen=set(); round_no=0
    log(f"Trend-Analyse gestartet: mindestens {minutes} Minuten, viele Themenbereiche.")
    while time.monotonic()<deadline:
        offset=(round_no*3)%len(queries); batch=[queries[(offset+j)%len(queries)] for j in range(3)]
        for query in batch:
            try:
                url="https://www.youtube.com/feeds/videos.xml?search_query="+urllib.parse.quote(query)
                req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 AI-ERIK trend research"})
                with urllib.request.urlopen(req,timeout=15) as response: feed=feedparser.parse(response.read())
                for entry in feed.entries[:8]:
                    title=re.sub(r"\\s+"," ",entry.get("title","")).strip(); key=title.casefold()
                    if title and key not in seen:
                        seen.add(key); observations.append({"query":query,"title":title[:180],"published":entry.get("published","")[:80]})
            except Exception as exc: log(f"Trend-Feed übersprungen ({query}): {str(exc)[:100]}")
        round_no+=1
        log(f"Trend-Analyse Runde {round_no}: {len(observations)} verschiedene Titel nach {(time.monotonic()-started)/60:.1f} Minuten.")
        if time.monotonic()<deadline: time.sleep(min(25,max(1,deadline-time.monotonic())))
    counts={}
    for item in observations: counts[item["query"]]=counts.get(item["query"],0)+1
    ranked=sorted(counts.items(),key=lambda x:x[1],reverse=True)
    result={"duration_minutes":round((time.monotonic()-started)/60,1),"observations":observations[:70],
        "top_signals":[{"topic":name,"items_found":count} for name,count in ranked[:8]],
        "limitations":"YouTube-Such-RSS-Inspiration; keine verifizierten Views, Rankings oder Sound-Nutzungszahlen."}
    log(f"Trend-Analyse abgeschlossen: {result['duration_minutes']} Minuten, {len(observations)} Titel.")
    return result

def local_story_fallback(state, comments):
    """Lokaler Planer für einzigartige Disziplin-vs-Ego-Shorts."""
    episode=int(state.get("episode",0))+1
    challenges=[
        ("KLIMMZÜGE","Wer hält am längsten durch?","Wiederholungen"),
        ("LAUFEN","Wer bleibt bis zum Ende dran?","Kilometer"),
        ("SCHWIMMEN","Wer teilt seine Kräfte klug ein?","Bahnen"),
        ("LERNEN","Wer bleibt konzentriert?","Lernblöcke"),
        ("SEILSPRINGEN","Wer findet seinen Rhythmus?","Sprünge"),
        ("RADFAHREN","Wer fährt konstant weiter?","Kilometer"),
        ("WANDERN","Wer geht Schritt für Schritt weiter?","Kilometer"),
        ("LIEGESTÜTZE","Wer bleibt sauber und konstant?","Wiederholungen")
    ]
    activity,question,unit=challenges[(episode-1)%len(challenges)]
    stages=[
        {"day":"DAY 1","scores":[6,32,16,6],"line":"Am Anfang will das Ego allen etwas beweisen."},
        {"day":"DAY 3","scores":[10,38,24,14],"line":"Die Motivation wird leiser. Das Ego will trotzdem gewinnen."},
        {"day":"DAY 7","scores":[11,39,34,28],"line":"Obsession gibt alles – aber ohne Pause wird es schwer."},
        {"day":"DAY 14","scores":[11,39,42,52],"line":"Disziplin bleibt konstant und überholt die anderen."}
    ]
    script=(f"Vier Kräfte. Eine Herausforderung: {activity.lower()}. "
        "Motivation startet stark, aber sie ist nicht jeden Tag da. Ego will beweisen, dass es besser ist. "
        "Obsession kennt kaum Grenzen und vergisst manchmal Pausen. Disziplin macht ruhig weiter – Schritt für Schritt. "
        "Am Ende gewinnt nicht der lauteste Start, sondern eine gesunde Gewohnheit. "
        "Bleib dran, mach Pausen und vergleiche dich nicht mit anderen.")
    return {
        "title":f"Folge {episode}: Motivation vs Ego vs Obsession vs Discipline",
        "description":f"Vier Eigenschaften, eine Challenge: {activity}. Eine kurze Story über gesunde Disziplin.",
        "topic":"Disziplin, Motivation, Ego und Obsession",
        "format":"Schwarzer Stickman-Vergleich",
        "trend_reason":"Eigenständige Folge im festgelegten Vergleichsformat.",
        "challenge":activity,"challenge_question":question,"unit":unit,"stages":stages,
        "script":script,"story":script,
        "visual_style":"Minimalistische weiße leuchtende Strichmännchen auf tiefschwarzem Hintergrund, vier getrennte farbige Lichtkegel: Motivation rot-orange, Ego violett, Obsession orange, Discipline gold. Vertikales 9:16-Video.",
        "characters":[{"name":n,"appearance":"weiß leuchtendes Strichmännchen","clothing":"keine Details","personality":p}
            for n,p in [("Motivation","startet begeistert"),("Ego","will sich beweisen"),("Obsession","treibt sich stark an, braucht aber Pausen"),("Discipline","bleibt ruhig und konstant")]],
        "scenes":[f"{x['day']}: vier Strichmännchen beginnen {activity.lower()}, Werte {x['scores']}." for x in stages],
        "edit_plan":["Sofortiger DAY-1-Hook mit Timer und Challenge-Frage","Vier Figuren nebeneinander, farbige Lichtkegel","Schnelle Tageswechsel und animierte Zähler","Spannender Vergleich kurz vor dem Ende","Discipline-Reveal und klare Schlussbotschaft"],
        "sound_plan":["Tiefer atmosphärischer Auftakt","Leises rhythmisches Original-Ticken","Steigernder Original-Beat","Kurzer Spannungs-Whoosh","Ruhiger kraftvoller Abschlussklang"],
        "voice_required":True,"hashtags":["#Discipline","#Motivation","#Mindset","#Shorts"]
    }

def generate(state,comments,trend_data=None):
    previous=state.get("history",[])[-35:]
    previous_stories=[{"episode":x.get("episode"),"title":x.get("title"),"topic":x.get("topic"),
        "format":x.get("format"),"story":x.get("story",""),"script":x.get("script","")[:500],"challenge":x.get("challenge")} for x in previous]
    learning=state.get("learning",{})
    prompt=f"""Du bist AI ERIK und produzierst AUSSCHLIESSLICH Shorts im Format Disziplin vs Motivation vs Ego vs Obsession.
KEINE Gaming-Videos, keine Cartoon-Geschichten, keine Trend-Recherche, keine anderen Formate.
Jede Folge ist ein eigener Vergleich mit vier weißen leuchtenden Strichmännchen auf rein schwarzem Hintergrund:
MOTIVATION (rot-orange), EGO (violett), OBSESSION (orange), DISCIPLINE (goldgelb). Alle vier stehen nebeneinander und machen dieselbe Challenge.
Challenge selbst auswählen und zwischen Klimmzügen, Laufen, Schwimmen, Lernen, Seilspringen, Radfahren, Wandern, Liegestützen, Tanzen, Treppensteigen oder einer anderen sicheren Alltags-Challenge wechseln.
WICHTIG: Challenge nicht in zwei aufeinanderfolgenden Folgen wiederholen. Auch Zahlen, Tagesverlauf, Hook, Frage, Erzähltext, Schnitte, Kamera, Sound und Schlussbotschaft müssen sich sichtbar unterscheiden. Prüfe die letzten Folgen.
Ablauf: starker DAY 1-Hook mit Frage; vier Figuren starten; mehrere Tage/Etappen vergehen; animierte Zähler zeigen den Fortschritt; Motivation kann nachlassen, Ego startet groß, Obsession muss Pausen lernen, Discipline bleibt konstant; am Ende gibt es eine überraschende, nachvollziehbare Auflösung und eine kurze Botschaft für den Alltag.
Die Zahlen sind eine erzählerische Visualisierung, keine echten Leistungsversprechen. Keine gefährlichen Challenges, keine Aufforderung zu Übertraining oder Selbstbestrafung. Zeige Pausen und gesunde Grenzen als Stärke.
VISUALS: schwarzer Hintergrund, leuchtende weiße Strichmännchen, vier farbige Lichtkegel, gut lesbare englische Labels MOTIVATION / EGO / OBSESSION / DISCIPLINE und Zähler. Kein realistischer Mensch, keine fremden Creator, keine Logos.
EDIT: dynamischer vertikaler 9:16-Schnitt, Timer, Zähler-Animation, schnelle Tageswechsel, kurze Zooms, Spannungsaufbau und klares Finale. Jede Folge eigene Schnittabfolge.
SOUND: eigener atmosphärischer Sound, rhythmische Originaleffekte und passende lizenzierte Musik; keine fremden Trend-Sounds kopieren.
Letzte Folgen zur Vermeidung von Wiederholungen:
{json.dumps(previous_stories,ensure_ascii=False)}
Lernprofil:
{json.dumps(learning,ensure_ascii=False)[:5000]}
Zuschauerideen:
{json.dumps(comments,ensure_ascii=False)}
Erstelle Folge {state.get('episode',0)+1}. Skript auf Deutsch, 55-85 Wörter. Antworte NUR als JSON mit title, description, topic, format, trend_reason, challenge, challenge_question, unit, stages (genau 4 Objekte mit day und scores; scores immer vier Zahlen in Reihenfolge Motivation,Ego,Obsession,Discipline), script, story, visual_style, characters, scenes (genau 4), edit_plan (genau 5), sound_plan (genau 5), voice_required, hashtags (3-5).
title beginnt mit "Folge {state.get('episode',0)+1}:". Keine Untertitel oder zusätzliche Texttafeln außer DAY, Challenge-Frage, vier Labels und Zähler."""

    def parse_json(raw):
        raw=(raw or "").strip()
        if raw.startswith("```"):
            raw=re.sub(r"^```(?:json)?\\s*","",raw,flags=re.I); raw=re.sub(r"\\s*```$","",raw)
        left,right=raw.find("{"),raw.rfind("}")
        if left<0 or right<left: raise ValueError("KI lieferte kein JSON")
        return json.loads(raw[left:right+1])
    def check(obj):
        required=["title","description","script","scenes","story","visual_style","characters","hashtags"]
        if not isinstance(obj,dict) or any(k not in obj for k in required): raise ValueError("KI-Ausgabe unvollständig")
        if not isinstance(obj["scenes"],list) or len(obj["scenes"])!=4: raise ValueError("Es müssen genau vier Tages-Etappen sein")
        if not 35<=len(str(obj["script"]).split())<=90: raise ValueError("Skriptlänge außerhalb des erlaubten Bereichs")
        obj.setdefault("topic","Kreative Unterhaltung"); obj.setdefault("format","Animation")
        obj.setdefault("trend_reason","Eigenständige Idee aus der Trendanalyse")
        obj.setdefault("edit_plan",["Dynamischer Einstieg"]+["Passender Schnitt und Perspektivwechsel"]*4)
        obj.setdefault("sound_plan",["Originaler Soundeffekt"]*5); obj.setdefault("voice_required",True)
        if not isinstance(obj["edit_plan"],list) or len(obj["edit_plan"])!=5: obj["edit_plan"]=["Passender Schnitt"]*5
        if not isinstance(obj["sound_plan"],list) or len(obj["sound_plan"])!=5: obj["sound_plan"]=["Originaler Soundeffekt"]*5
        obj["voice_required"]=bool(obj["voice_required"]); return obj
    if GEMINI:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI); response=genai.GenerativeModel(MODEL).generate_content(prompt)
            raw=getattr(response,"text",None)
            if raw and raw.strip():
                obj=check(parse_json(raw)); log("Gemini plante Thema, Story, Schnitt und Sound."); return obj
            log("Gemini lieferte eine leere Antwort; OpenRouter wird versucht.")
        except Exception as exc: log(f"Gemini nicht verfügbar; OpenRouter wird versucht: {str(exc)[:150]}")
    if ROUTER:
        models=["openrouter/free"]
        try:
            req=urllib.request.Request("https://openrouter.ai/api/v1/models",headers={"Authorization":"Bearer "+ROUTER,"Accept":"application/json"})
            with urllib.request.urlopen(req,timeout=25) as response: catalog=json.loads(response.read().decode())
            for item in catalog.get("data",[]):
                mid=item.get("id",""); arch=item.get("architecture") or {}; pricing=item.get("pricing") or {}
                if mid.endswith(":free") and "text" in (arch.get("output_modalities") or []) and str(pricing.get("prompt","")) in ("0","0.0","0.000000") and str(pricing.get("completion","")) in ("0","0.0","0.000000") and mid not in models: models.append(mid)
                if len(models)>=6: break
            log(f"{len(models)-1} aktuell kostenlose Textmodelle als Fallback gefunden.")
        except Exception as exc: log(f"Modellliste nicht abrufbar: {str(exc)[:140]}")
        for model in models:
            base={"model":model,"messages":[{"role":"system","content":"Du bist ein origineller, sicherer und vielseitiger Shorts-Produzent. Antworte nur als JSON."},{"role":"user","content":prompt}],"temperature":0.85,"max_tokens":4000}
            for json_mode in (True,False):
                try:
                    payload=dict(base)
                    if json_mode: payload["response_format"]={"type":"json_object"}
                    req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=json.dumps(payload).encode(),
                        headers={"Authorization":"Bearer "+ROUTER,"Content-Type":"application/json","HTTP-Referer":"https://github.com/hoffmannerik1-eng/AI","X-Title":"AI ERIK Shorts"},method="POST")
                    with urllib.request.urlopen(req,timeout=100) as response: data=json.loads(response.read().decode())
                    choices=data.get("choices") or []; message=(choices[0].get("message") or {}) if choices else {}; raw=message.get("content")
                    if isinstance(raw,list): raw="".join(x.get("text","") for x in raw if isinstance(x,dict))
                    if not isinstance(raw,str) or not raw.strip(): raise ValueError("Modell lieferte leeren Inhalt")
                    obj=check(parse_json(raw)); log(f"OpenRouter-Modell {model} plante Folge, Schnitt und Sound."); return obj
                except Exception as exc:
                    if json_mode and isinstance(exc,urllib.error.HTTPError) and exc.code in (400,404,422): continue
                    log(f"OpenRouter-Modell {model} nicht verfügbar: {str(exc)[:180]}"); break
        log("Alle kostenlosen Text-KIs ausgefallen; lokale Story wird verwendet.")
    else: log("Keine Text-KI konfiguriert; lokale Story wird verwendet.")
    return local_story_fallback(state,comments)

async def voice(txt,path): await edge_tts.Communicate(txt,"de-DE-ConradNeural").save(str(path))

def create_local_cartoon(raw,d,seconds):
    """Kostenloser Offline-Fallback: fünf reich illustrierte, animierte Story-Szenen."""
    from PIL import Image, ImageDraw, ImageFilter
    import math, shutil

    W,H,FPS=540,960,12
    frame_dir=OUT/"local_frames"
    if frame_dir.exists(): shutil.rmtree(frame_dir)
    frame_dir.mkdir(parents=True)
    scenes=d.get("scenes") or [""]*5
    chars=d.get("characters") or [{"name":"Held","clothing":"blaues Outfit","appearance":"dunkle Haare"}]
    chars=chars[:3]
    outfits=[(48,125,235),(225,82,65),(55,170,115)]
    skin=(238,190,148)
    palettes=[
        ((45,90,145),(235,170,90),(60,105,70)),
        ((55,135,175),(240,205,125),(45,125,145)),
        ((48,48,105),(145,115,205),(55,55,105)),
        ((155,100,70),(245,190,105),(100,80,60)),
        ((60,125,100),(180,220,165),(50,105,65))
    ]

    def rounded(draw,box,fill,r=12,outline=None,width=2):
        draw.rounded_rectangle(tuple(int(v) for v in box),radius=int(r),fill=fill,outline=outline,width=width)
    def gradient(top,bottom):
        im=Image.new("RGB",(W,H))
        pix=im.load()
        for y in range(H):
            t=y/(H-1)
            c=tuple(int(top[k]*(1-t)+bottom[k]*t) for k in range(3))
            for x in range(W): pix[x,y]=c
        return im
    def avatar(draw,x,ground,idx,t,scene_i,action):
        spec=chars[idx]
        shirt=outfits[(sum(map(ord,str(spec.get("clothing",""))))+idx)%len(outfits)]
        scale=0.92 if len(chars)>1 else 1.05
        x=float(x)+math.sin(t*2.4+idx)*10
        stride=math.sin(t*5+idx)*22
        jump=max(0,math.sin(t*3+scene_i)*12)
        y=ground-jump
        # Boden-Schatten
        draw.ellipse((x-54,y+3,x+54,y+20),fill=(20,35,45))
        # Beine mit sichtbarer Laufbewegung
        rounded(draw,(x-27+stride,y-132,x-5+stride,y-42),(35,43,62),5)
        rounded(draw,(x+5-stride,y-132,x+27-stride,y-42),(35,43,62),5)
        rounded(draw,(x-34+stride,y-52,x-1+stride,y-35),(25,30,42),5)
        rounded(draw,(x+1-stride,y-52,x+34-stride,y-35),(25,30,42),5)
        # Torso und Arme; Armhaltung reagiert auf die Szene
        rounded(draw,(x-48,y-255,x+48,y-128),shirt,10,tuple(max(0,c-35) for c in shirt),3)
        arm_swing=math.sin(t*4+idx)*18
        if action in ("reach","point","open"):
            left_end=(x-93,y-185+arm_swing); right_end=(x+70,y-245)
        elif action in ("run","escape","chase"):
            left_end=(x-85,y-220+arm_swing); right_end=(x+85,y-155-arm_swing)
        elif action in ("surprise","discover"):
            left_end=(x-72,y-285); right_end=(x+72,y-285)
        else:
            left_end=(x-72,y-180+arm_swing); right_end=(x+72,y-180-arm_swing)
        for side,end in ((-1,left_end),(1,right_end)):
            sx=x+side*43; sy=y-235
            draw.line((sx,sy,end[0],end[1]),fill=tuple(max(0,c-30) for c in shirt),width=25)
            draw.ellipse((end[0]-12,end[1]-12,end[0]+12,end[1]+12),fill=skin)
        # Kopf, Haare und ausdrucksstarkes Gesicht
        rounded(draw,(x-37,y-344,x+37,y-265),skin,9,(120,85,65),2)
        hair=(55,38,32)
        rounded(draw,(x-39,y-350,x+39,y-318),hair,8)
        draw.rectangle((x-38,y-335,x-25,y-304),fill=hair)
        draw.rectangle((x+25,y-335,x+38,y-304),fill=hair)
        eye_y=y-310
        draw.ellipse((x-20,eye_y,x-11,eye_y+10),fill=(25,30,38))
        draw.ellipse((x+11,eye_y,x+20,eye_y+10),fill=(25,30,38))
        if action in ("surprise","discover"):
            draw.ellipse((x-7,y-292,x+7,y-277),outline=(110,45,45),width=3)
        else:
            draw.arc((x-10,y-294,x+10,y-280),5,175,fill=(115,45,45),width=3)

    def scene_background(draw,scene,idx,t,sky,glow,ground):
        s=scene.lower()
        # Hügel, entfernte Ebenen und atmosphärisches Licht
        draw.ellipse((365,75,485,195),fill=glow)
        for layer in range(3):
            y=520+layer*65
            color=tuple(max(0,min(255,c-layer*13)) for c in ground)
            points=[(0,H),(0,y)]
            for x in range(-20,W+40,80):
                points.append((x,y-35-math.sin(x*.018+layer)*28))
            points.extend([(W,H)])
            draw.polygon(points,fill=color)
        if any(k in s for k in ("space","weltall","planet","mond","rakete","raumschiff")):
            for x,y in ((45,130),(170,245),(420,300),(490,460),(100,390),(300,115)):
                draw.ellipse((x,y,x+5,y+5),fill=(255,255,255))
            draw.ellipse((355,270,505,420),fill=(135,105,195))
            draw.ellipse((380,290,475,395),fill=(95,90,160))
            draw.ellipse((390,320,415,345),fill=(165,145,215))
        elif any(k in s for k in ("meer","ocean","wasser","strand","boot","insel","unterwasser")):
            draw.rectangle((0,500,W,790),fill=(35,115,165))
            for y in (545,610,680,735):
                for x in range(-30,W,110):
                    yy=y+int(math.sin(t*3+x)*7)
                    draw.arc((x,yy,x+100,yy+35),0,180,fill=(160,225,240),width=5)
            if "boot" in s or "schiff" in s:
                draw.polygon(((160,505),(385,505),(340,570),(210,570)),fill=(115,65,45))
                draw.polygon(((260,360),(260,495),(350,495)),fill=(245,235,205))
                draw.line((260,350,260,510),fill=(75,55,45),width=8)
        elif any(k in s for k in ("wald","forest","baum","dschungel","park")):
            for x in (15,105,420,500):
                draw.rectangle((x+20,385,x+48,735),fill=(105,65,40))
                draw.ellipse((x-35,300,x+105,470),fill=(35,115,62))
                draw.ellipse((x-10,270,x+85,420),fill=(50,145,72))
        elif any(k in s for k in ("höhle","cave","berg","mine","tunnel")):
            draw.polygon(((0,0),(110,0),(180,210),(145,450),(0,570)),fill=(65,62,75))
            draw.polygon(((W,0),(430,0),(365,220),(410,460),(W,560)),fill=(55,52,68))
            for x,y in ((85,590),(455,620),(160,730)):
                draw.polygon(((x,y),(x+18,y-55),(x+36,y)),fill=(110,105,125))
        elif any(k in s for k in ("schule","labor","computer","büro","zimmer","haus","stadt","straße","gebäude")):
            for x,w,h,col in ((25,120,270,(115,130,155)),(390,125,340,(145,125,115))):
                rounded(draw,(x,500-h,x+w,750),8,col)
                for wx in range(x+18,x+w-12,42):
                    for wy in range(520-h,700,62):
                        rounded(draw,(wx,wy,wx+22,wy+30),(100,175,220),4)
            draw.rectangle((0,748,W,790),fill=(80,85,95))
        else:
            # Fantasie-Landschaft: Felsen, Gras, kleine Büsche
            for x in (30,460):
                draw.ellipse((x-55,620,x+65,760),fill=(70,100,75))
                draw.ellipse((x-30,590,x+45,700),fill=(85,130,80))

    def draw_story_object(draw,scene,t,idx):
        s=scene.lower()
        x=270+int(math.sin(t*2.2)*55)
        y=635+int(math.sin(t*3)*10)
        # Das zentrale Objekt wird aus der konkreten Szenenbeschreibung abgeleitet.
        if any(k in s for k in ("schatz","truhe","kiste","gold","geheimnis","fund")):
            rounded(draw,(x-48,y-55,x+48,y+12),(120,70,35),9,(245,185,65),4)
            draw.rectangle((x-48,y-35,x+48,y-20),fill=(225,165,45))
            rounded(draw,(x-10,y-58,x+10,y-43),(255,220,90),5)
            for a in range(3):
                sx=x-65+a*60
                draw.line((sx,y-95,sx+math.sin(t*4+a)*8,y-125),fill=(255,230,110),width=4)
        elif any(k in s for k in ("schlüssel","key","tür","portal","tor","öffnen")):
            rounded(draw,(x-43,y-105,x+43,y+20),(95,55,45),9,(225,175,100),4)
            draw.ellipse((x-14,y-62,x+14,y-34),outline=(255,215,80),width=7)
            draw.line((x,y-34,x,y-5),fill=(255,215,80),width=7)
        elif any(k in s for k in ("computer","roboter","maschine","gerät","konsole","hacker")):
            rounded(draw,(x-62,y-75,x+62,y+5),(45,55,70),10,(125,210,235),4)
            rounded(draw,(x-47,y-60,x+47,y-22),(45,155,175),5)
            draw.line((x,y+5,x,y+25),fill=(70,75,85),width=8)
            draw.line((x-35,y+25,x+35,y+25),fill=(70,75,85),width=8)
        elif any(k in s for k in ("rakete","raumschiff","flugzeug","fliegen")):
            draw.polygon(((x,y-100),(x+38,y+20),(x,y+5),(x-38,y+20)),fill=(205,215,225),outline=(70,85,110))
            draw.ellipse((x-12,y-45,x+12,y-20),fill=(80,190,230))
            draw.polygon(((x-13,y+8),(x,y+65),(x+13,y+8)),fill=(255,135,45))
        elif any(k in s for k in ("ball","spiel","rennen","wettkampf")):
            draw.ellipse((x-35,y-35,x+35,y+35),fill=(245,95,65),outline=(255,235,190),width=5)
            draw.arc((x-25,y-25,x+25,y+25),0,300,fill=(255,255,255),width=4)
        elif any(k in s for k in ("buch","karte","brief","nachricht","plan")):
            rounded(draw,(x-48,y-60,x+48,y+30),(235,220,175),5,(110,75,45),4)
            for yy in (y-35,y-15,y+5):
                draw.line((x-30,yy,x+30,yy),fill=(120,95,70),width=3)
        elif any(k in s for k in ("auto","wagen","fahrzeug","motorrad","fahrrad")):
            rounded(draw,(x-70,y-48,x+70,y+4),(205,65,55),15,(80,45,45),3)
            rounded(draw,(x-35,y-78,x+38,y-38),(100,185,220),10)
            for wx in (x-45,x+45):
                draw.ellipse((wx-15,y-8,wx+15,y+22),fill=(30,35,45),outline=(210,210,210),width=3)
        else:
            # Leuchtender Story-Gegenstand, sichtbar und animiert
            draw.ellipse((x-35,y-35,x+35,y+35),fill=(255,205,55),outline=(255,245,180),width=5)
            draw.ellipse((x-12,y-12,x+12,y+12),fill=(255,250,220))
            for a in range(8):
                ang=a*math.pi/4+t
                ex=x+math.cos(ang)*52; ey=y+math.sin(ang)*52
                draw.line((x+math.cos(ang)*40,y+math.sin(ang)*40,ex,ey),fill=(255,235,130),width=3)

    frame_no=0
    per_scene=max(1,round((seconds/5)*FPS))
    for si in range(5):
        desc=str(scenes[si] if si<len(scenes) else "")
        lower=desc.lower()
        sky,glow,ground=palettes[si]
        if any(k in lower for k in ("space","weltall","planet","mond","stern")):
            sky,glow,ground=(12,18,55),(115,100,190),(40,42,85)
        elif any(k in lower for k in ("meer","ocean","wasser","strand","boot")):
            sky,glow,ground=(65,145,195),(235,220,150),(35,115,150)
        elif any(k in lower for k in ("wald","forest","baum","dschungel")):
            sky,glow,ground=(75,145,175),(235,215,130),(45,105,55)
        elif any(k in lower for k in ("höhle","cave","mine","tunnel")):
            sky,glow,ground=(35,35,55),(220,145,65),(65,60,70)
        for fi in range(per_scene):
            t=fi/max(1,per_scene-1)*math.pi*2
            img=gradient(sky,tuple(max(0,int(c*.48)) for c in sky))
            draw=ImageDraw.Draw(img)
            scene_background(draw,desc,si,t,sky,glow,ground)
            # Vordergrund, Lichtpunkte und filmische Partikel
            for p in range(18):
                px=(p*97+int(t*18))%W
                py=(p*137+si*53)%720
                draw.ellipse((px,py,px+3,py+3),fill=(255,240,190))
            action="reach"
            if any(k in lower for k in ("rennen","flucht","verfolgt","flieht","entkommen","jagt")): action="run"
            elif any(k in lower for k in ("entdeckt","findet","plötzlich","überrasch","geheim")): action="discover"
            elif any(k in lower for k in ("öffnet","öffnet sich","drückt","aktiviert","greift","nimmt")): action="open"
            elif any(k in lower for k in ("zeigt","ruft","warnt","signal")): action="point"
            for ci in range(len(chars)):
                cx=W*(0.34 if ci==0 else (0.68 if ci==1 else 0.52))
                if action=="run": cx+=math.sin(t*2+ci)*55
                avatar(draw,cx,820,ci,t,si,action)
            draw_story_object(draw,desc,t,si)
            # Szenenwechsel als kurze filmische Lichtblende, ohne eingeblendeten Text.
            if fi<2:
                overlay=Image.new("RGBA",(W,H),(255,255,255,int((2-fi)*22)))
                img=Image.alpha_composite(img.convert("RGBA"),overlay).convert("RGB")
            img.save(frame_dir/f"frame_{frame_no:05d}.jpg",quality=91,optimize=True)
            frame_no+=1

    subprocess.run(["ffmpeg","-y","-loglevel","error","-framerate",str(FPS),
        "-i",str(frame_dir/"frame_%05d.jpg"),"-c:v","libx264","-preset","ultrafast",
        "-crf","22","-pix_fmt","yuv420p","-r","24",str(raw)],check=True)
    log("Lokaler Gratis-Fallback: fünf individuell illustrierte und animierte Story-Szenen gerendert.")

def create_local_discipline_video(raw,d,seconds=20):
    """Rendert lokal ein animiertes Disziplin-vs-Ego-Video mit vier Stickfiguren."""
    import math
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    W,H,FPS=540,960,15
    frames=round(seconds*FPS)
    episode=int(d.get("episode",1))
    challenge=str(d.get("challenge","KLIMMZÜGE")).upper()
    stages=d.get("stages") or [
        {"day":"DAY 1","scores":[6,32,16,6]},
        {"day":"DAY 3","scores":[10,38,24,14]},
        {"day":"DAY 7","scores":[11,39,34,28]},
        {"day":"DAY 14","scores":[11,39,42,52]}]
    stages=(stages+[stages[-1]]*4)[:4]
    labels=["MOTIVATION","EGO","OBSESSION","DISCIPLINE"]
    colors=[(255,90,48),(155,105,255),(255,145,45),(255,205,70)]
    font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        big=ImageFont.truetype(font_path,40); med=ImageFont.truetype(font_path,19); small=ImageFont.truetype(font_path,15)
    except Exception:
        big=med=small=ImageFont.load_default()
    frame_dir=OUT/"discipline_frames"; shutil.rmtree(frame_dir,ignore_errors=True); frame_dir.mkdir(parents=True)
    def center(draw,y,text,font,fill):
        box=draw.textbbox((0,0),text,font=font); draw.text(((W-(box[2]-box[0]))/2,y),text,font=font,fill=fill)
    def stick(draw,cx,cy,phase,kind,color):
        # soft coloured halo behind each glowing white stick figure
        glow=Image.new("RGBA",(W,H),(0,0,0,0)); gd=ImageDraw.Draw(glow)
        gd.ellipse((cx-54,cy-100,cx+54,cy+92),fill=(*color,45))
        glow=glow.filter(ImageFilter.GaussianBlur(28))
        base.alpha_composite(glow)
        draw=ImageDraw.Draw(base)
        white=(245,248,255,255); width=5
        head=(cx-12,cy-75,cx+12,cy-51)
        draw.ellipse(head,outline=white,width=width)
        if kind=="KLIMMZÜGE":
            draw.line((cx,cy-50,cx,cy+4),fill=white,width=width)
            lift=math.sin(phase*math.pi*2)*10
            draw.line((cx,cy-36,cx-22,cy-12+lift),fill=white,width=width)
            draw.line((cx,cy-36,cx+22,cy-12+lift),fill=white,width=width)
            draw.line((cx,cy+4,cx-18,cy+38),fill=white,width=width)
            draw.line((cx,cy+4,cx+18,cy+38),fill=white,width=width)
        elif kind in ("LAUFEN","RADFAHREN","WANDERN","SEILSPRINGEN"):
            draw.line((cx,cy-50,cx,cy+5),fill=white,width=width)
            swing=math.sin(phase*math.pi*2)*18
            draw.line((cx,cy-32,cx-20-swing,cy-8),fill=white,width=width)
            draw.line((cx,cy-32,cx+20+swing,cy-8),fill=white,width=width)
            draw.line((cx,cy+5,cx-18+swing,cy+38),fill=white,width=width)
            draw.line((cx,cy+5,cx+18-swing,cy+38),fill=white,width=width)
        elif kind=="SCHWIMMEN":
            draw.line((cx-28,cy-10,cx+28,cy-10),fill=white,width=width)
            draw.line((cx-8,cy-10,cx+14,cy+13),fill=white,width=width)
            draw.line((cx+14,cy+13,cx+30,cy-3),fill=white,width=width)
            draw.arc((cx-42,cy+22,cx+42,cy+48),0,180,fill=white,width=3)
        elif kind=="LERNEN":
            draw.line((cx,cy-50,cx,cy+18),fill=white,width=width)
            draw.line((cx,cy-28,cx-18,cy-4),fill=white,width=width)
            draw.line((cx,cy-28,cx+18,cy-4),fill=white,width=width)
            draw.line((cx,cy+18,cx-16,cy+38),fill=white,width=width)
            draw.line((cx,cy+18,cx+16,cy+38),fill=white,width=width)
            draw.line((cx-30,cy+42,cx+30,cy+42),fill=white,width=3)
        else:
            draw.line((cx,cy-50,cx,cy+5),fill=white,width=width)
            draw.line((cx,cy-30,cx-20,cy-8),fill=white,width=width)
            draw.line((cx,cy-30,cx+20,cy-8),fill=white,width=width)
            draw.line((cx,cy+5,cx-18,cy+38),fill=white,width=width)
            draw.line((cx,cy+5,cx+18,cy+38),fill=white,width=width)
    for fi in range(frames):
        t=fi/FPS; stage_index=min(3,int(t/max(0.1,seconds/4)))
        local=(t/(seconds/4))-stage_index
        st=stages[stage_index]; scores=st.get("scores",[6,32,16,6])
        base=Image.new("RGBA",(W,H),(2,3,8,255)); draw=ImageDraw.Draw(base)
        center(draw,38,str(st.get("day","DAY 1")),big,(250,250,255,255))
        center(draw,94,challenge,med,(220,225,240,255))
        center(draw,132,str(d.get("challenge_question","WHO LASTS LONGER?")).upper()[:34],small,(180,185,205,255))
        # Four columns and separate beams
        col_w=W/4
        for i,(label,color) in enumerate(zip(labels,colors)):
            cx=int(col_w*(i+.5))
            beam=Image.new("RGBA",(W,H),(0,0,0,0)); bd=ImageDraw.Draw(beam)
            bd.polygon([(cx-8,245),(cx+8,245),(cx+55,620),(cx-55,620)],fill=(*color,28))
            beam=beam.filter(ImageFilter.GaussianBlur(18)); base.alpha_composite(beam)
            draw=ImageDraw.Draw(base)
            draw.line((int(i*col_w+10),250,int((i+1)*col_w-10),250),fill=(*color,130),width=2)
            if challenge=="KLIMMZÜGE":
                draw.line((cx-42,280,cx+42,280),fill=(205,210,225,255),width=5)
                draw.line((cx-42,280,cx-42,293),fill=(205,210,225,255),width=3)
                draw.line((cx+42,280,cx+42,293),fill=(205,210,225,255),width=3)
            elif challenge=="SCHWIMMEN":
                for yy in (400,425,450):
                    draw.arc((cx-45,yy-8,cx+45,yy+8),0,180,fill=(70,150,220,190),width=2)
            elif challenge=="LERNEN":
                draw.rectangle((cx-32,410,cx+32,430),outline=(180,190,210,220),width=2)
            elif challenge in ("RADFAHREN","WANDERN"):
                draw.ellipse((cx-28,405,cx+28,461),outline=(110,120,140,170),width=2)
            stick(draw,cx,360,local+i*.13,challenge,color)
            label_box=draw.textbbox((0,0),label,font=small)
            draw.text((cx-(label_box[2]-label_box[0])/2,500),label,font=small,fill=(*color,255))
            count=str(scores[i] if i<len(scores) else 0)
            count_box=draw.textbbox((0,0),count,font=big)
            draw.text((cx-(count_box[2]-count_box[0])/2,540),count,font=big,fill=(255,255,255,255))
            draw.text((cx-25,592),str(d.get("unit","REPS")).upper()[:9],font=small,fill=(150,155,175,255))
        if stage_index==3:
            center(draw,690,"DISCIPLINE WINS",med,(255,215,95,255))
            center(draw,735,"CONSISTENCY BEATS THE PERFECT START",small,(235,235,245,255))
            center(draw,820,"REST. RESET. KEEP GOING.",small,(175,185,205,255))
        else:
            center(draw,710,"FOUR MINDSETS. ONE CHALLENGE.",small,(175,185,205,255))
            center(draw,760,"WHO WILL LAST?",med,(235,235,245,255))
        base.convert("RGB").save(frame_dir/f"frame_{fi:05d}.jpg",quality=88)
    subprocess.run(["ffmpeg","-y","-loglevel","error","-framerate",str(FPS),"-i",str(frame_dir/"frame_%05d.jpg"),
        "-c:v","libx264","-preset","ultrafast","-crf","21","-pix_fmt","yuv420p","-r","24",str(raw)],check=True)
    log(f"Lokales Stickman-Video gerendert: {challenge}, 4 Figuren, schwarzer Hintergrund.")

def make_video(d):
    raw=OUT/"video_ai_combined.mp4"; video=OUT/"story_episode.mp4"
    music=OUT/"ambient.wav"; effects=OUT/"sound_effects.wav"; audio=OUT/"voice.mp3"
    log("Lokaler Stickman-Renderer wird verwendet; keine externe Video-KI wird gestartet.")
    create_local_discipline_video(raw,d,20)

    probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of",
        "default=noprint_wrappers=1:nokey=1",str(raw)],capture_output=True,text=True,check=True)
    seconds=max(5.0,float(probe.stdout.strip()))
    voice_required=bool(d.get("voice_required",True))
    if voice_required:
        asyncio.run(voice(d["script"],audio))
        voice_probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of",
            "default=noprint_wrappers=1:nokey=1",str(audio)],capture_output=True,text=True,check=True)
        seconds=max(seconds,float(voice_probe.stdout.strip()))
        log("Erzählerstimme wird verwendet.")
    else:
        log("Produktionsplan: keine Erzählerstimme nötig.")

    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",
        f"aevalsrc=0.018*sin(2*PI*110*t)+0.012*sin(2*PI*164.81*t)+0.008*sin(2*PI*220*t):s=44100:d={seconds:.3f}",
        "-af",f"afade=t=in:st=0:d=1.0,afade=t=out:st={max(0,seconds-1.5):.3f}:d=1.5",
        "-c:a","pcm_s16le",str(music)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)

    # Original erzeugte Soundakzente an Szenenwechseln; keine ungeklärten Fremd-Audios.
    sound_plan=d.get("sound_plan") or []
    expression="0"
    for i in range(1,5):
        moment=min(seconds-0.4,seconds*i/5)
        hint=str(sound_plan[i] if len(sound_plan)>i else "whoosh").lower()
        if any(word in hint for word in ("impact","boom","knall","stoß","hit")):
            freq=95+i*12
        elif any(word in hint for word in ("chime","glöck","magic","spark","glitzer")):
            freq=720+i*80
        else:
            freq=260+i*35
        expression+=f"+(0.035*sin(2*PI*({freq}+120*(t-{moment:.3f}))*t)*exp(-13*(t-{moment:.3f}))*gte(t,{moment:.3f})*lt(t,{moment+0.38:.3f}))"
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"aevalsrc={expression}:s=44100:d={seconds:.3f}",
        "-c:a","pcm_s16le",str(effects)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)

    inputs=["-i",str(raw),"-i",str(music),"-i",str(effects)]
    graph=["[1:a]volume=0.24[music]","[2:a]volume=0.70[sfx]"]
    if voice_required:
        inputs += ["-i",str(audio)]
        graph += ["[3:a]volume=1.0[voice]","[voice][music][sfx]amix=inputs=3:duration=first:dropout_transition=2[a]"]
    else:
        graph += ["[music][sfx]amix=inputs=2:duration=first:dropout_transition=2[a]"]
    graph.append(f"[0:v]scale=1080:1920:flags=lanczos,eq=contrast=1.04:saturation=1.10,unsharp=5:5:0.35:3:3:0.0,fade=t=in:st=0:d=0.18,format=yuv420p[v]")
    subprocess.run(["ffmpeg","-y"]+inputs+["-filter_complex",";".join(graph),"-map","[v]","-map","[a]",
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

def update_learning(state,data):
    """Speichert Themen-, Format- und Stilvielfalt für spätere Produktionen."""
    profile=state.setdefault("learning",{})
    for key in ("topics","formats","styles"): profile.setdefault(key,{})
    for key,value in (("topics",data.get("topic","Unbekannt")),("formats",data.get("format","Unbekannt")),("styles",data.get("visual_style","Unbekannt"))):
        label=str(value or "Unbekannt")[:100]; profile[key][label]=int(profile[key].get(label,0))+1
    recent=profile.setdefault("recent_approaches",[])
    recent.append({"episode":data.get("episode"),"topic":data.get("topic"),"format":data.get("format"),"style":data.get("visual_style"),"trend_reason":data.get("trend_reason","")})
    profile["recent_approaches"]=recent[-40:]; state["learning"]=profile


def main():
    log("AI ERIK MULTI-FORMAT SHORTS-AUTOPILOT gestartet.")
    state=load_state(); dry=os.getenv("DRY_RUN","true").lower() in ("1","true","yes")
    deadline=time.monotonic()+5.5*60*60; episode_dir=OUT/"episodes"; episode_dir.mkdir(exist_ok=True); completed=0
    while time.monotonic()<deadline:
        ideas=get_comments(state)
        trend_data=research_shorts_trends(state)
        data=generate(state,ideas,trend_data); data["episode"]=int(state.get("episode",0))+1
        log(f"Neue Folge {data['episode']}: {data['title']} | {data.get('topic')} | {data.get('format')}")
        video=make_video(data); verify_video(video)
        archive=episode_dir/f"episode_{data['episode']:05d}.mp4"; shutil.copy2(video,archive)
        video_id=None; upload_pending=False
        if not dry:
            try: video_id=upload(video,data)
            except Exception as exc:
                detail=str(exc)
                upload_pending=True
                if "uploadLimitExceeded" in detail or "number of videos they may upload" in detail:
                    log("YouTube-Tageslimit erreicht. Video bleibt gespeichert; Workflow läuft weiter.")
                else: log("Upload fehlgeschlagen; Video bleibt gespeichert: "+detail[:240])
        else: log("DRY_RUN aktiv: Video wurde nicht veröffentlicht.")
        state.update({"episode":data["episode"]})
        state.setdefault("history",[]).append({"episode":data["episode"],"title":data["title"],"topic":data.get("topic"),
            "format":data.get("format"),"challenge":data.get("challenge"),"trend_reason":data.get("trend_reason"),"story":data.get("story",""),
            "script":data.get("script",""),"edit_plan":data.get("edit_plan",[]),"sound_plan":data.get("sound_plan",[]),
            "video_id":video_id,"upload_pending":upload_pending,"created_at":datetime.now(timezone.utc).isoformat()})
        state["history"]=state["history"][-100:]; update_learning(state,data)
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
        (OUT/"latest.json").write_text(json.dumps({"title":data["title"],"episode":data["episode"],"video_id":video_id,
            "topic":data.get("topic"),"format":data.get("format"),"challenge":data.get("challenge"),"upload_pending":upload_pending,"story":data.get("story","")},ensure_ascii=False,indent=2),encoding="utf-8")
        completed+=1; log(f"Folge {data['episode']} gespeichert. Lernprofil für nächste Folge aktualisiert.")
    log(f"Serienlauf beendet: {completed} Folge(n). Verlauf und Lernprofil gespeichert.")

if __name__=="__main__": main()
