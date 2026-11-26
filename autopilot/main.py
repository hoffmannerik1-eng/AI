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

def local_story_fallback(state, comments):
    """Erstellt ohne externe Text-KI eine vollständige, sichere Story mit fünf Bildszenen."""
    episode=int(state.get("episode",0))+1
    heroes=[
        ("Milo","blauer Hoodie und dunkle Haare","neugierig und mutig"),
        ("Lina","roter Hoodie und braune Haare","clever und entschlossen"),
        ("Noah","grüne Jacke und schwarze Haare","ruhig und erfinderisch"),
        ("Mira","violette Jacke und blonde Haare","witzig und aufmerksam"),
        ("Ben","orangefarbene Jacke und braune Haare","abenteuerlustig")
    ]
    name,clothes,personality=heroes[(episode-1)%len(heroes)]
    plots=[
        ("Das geheime Portal","In einer alten Höhle entdeckt {n} eine leuchtende Tür. Dahinter wartet ein Rätsel, das nur mit Mut und Köpfchen lösbar ist.",
         ["{n} entdeckt eine geheimnisvolle Höhle zwischen hohen Felsen.","Ein goldenes Portal leuchtet plötzlich in der dunklen Wand auf.","{n} findet einen Schlüssel neben einer alten Truhe.","Das Portal öffnet sich und ein funkelnder Sternenweg erscheint.","{n} tritt hindurch und entdeckt eine fantastische neue Welt."]),
        ("Der Roboter erwacht","Im verlassenen Labor findet {n} einen kleinen Roboter. Als er plötzlich erwacht, beginnt eine überraschende Suche nach seinem verlorenen Energie-Kristall.",
         ["{n} betritt ein verlassenes Hightech-Labor voller blinkender Geräte.","Ein kleiner Roboter erwacht auf einem Arbeitstisch.","Der Roboter zeigt auf eine leere Energiehalterung.","Gemeinsam entdecken sie einen leuchtenden Kristall in einer Kiste.","Der Roboter startet und projiziert eine Karte zum nächsten Abenteuer."]),
        ("Das Rätsel der Insel","Auf einer einsamen Insel entdeckt {n} eine geheimnisvolle Karte. Eine Reihe überraschender Hinweise führt zu einem Schatz, den niemand erwartet hat.",
         ["{n} landet mit einem kleinen Boot an einer tropischen Insel.","Eine alte Karte liegt halb im Sand vergraben.","{n} folgt leuchtenden Markierungen durch den Dschungel.","Hinter einem Wasserfall entdeckt {n} eine goldene Schatztruhe.","In der Truhe liegt ein kleiner Kompass, der geheimnisvoll aufleuchtet."]),
        ("Die Rakete ohne Pilot","Bei einem Schulausflug entdeckt {n} eine startbereite Mini-Rakete. Ein unerwartetes Signal führt zu einer lustigen Rettungsmission im Weltall.",
         ["{n} entdeckt in einer Raumstation eine kleine silberne Rakete.","Ein blinkendes Signal erscheint auf dem Kontrollpult.","Die Rakete hebt ab und fliegt an einem blauen Planeten vorbei.","{n} entdeckt einen freundlichen kleinen Roboter im All.","Gemeinsam landen sie sicher und feiern ihre unerwartete Begegnung."]),
        ("Das verschwundene Licht","Als im Wald alle Lichter verschwinden, folgt {n} einer geheimnisvollen Spur. Am Ende wartet eine freundliche Überraschung.",
         ["{n} bemerkt, dass im Wald alle leuchtenden Blumen dunkel sind.","Eine Spur aus kleinen Lichtpunkten führt tiefer zwischen die Bäume.","{n} entdeckt eine alte Maschine unter den Wurzeln.","Mit einem gefundenen Kristall bringt {n} die Maschine wieder zum Leuchten.","Der ganze Wald erstrahlt und kleine Glühwürmchen tanzen um {n}."])
    ]
    title,story,scenes=plots[(episode-1)%len(plots)]
    story=story.format(n=name)
    scenes=[x.format(n=name) for x in scenes]
    scripts=[
        f"Mitten in der Höhle entdeckt {name} eine Tür, die eigentlich nicht existieren dürfte. Ein goldenes Licht flackert auf. Neben einer alten Truhe liegt ein geheimnisvoller Schlüssel. {name} zögert kurz, steckt ihn ins Schloss und hört ein tiefes Klicken. Das Portal öffnet sich. Dahinter wartet eine völlig neue Welt.",
        f"Dieses Labor sollte längst verlassen sein. Doch als {name} den Raum betritt, bewegt sich plötzlich ein kleiner Roboter. Er sucht seinen Energie-Kristall. Gemeinsam durchsuchen sie das Labor und entdecken eine geheimnisvolle Kiste. Darin liegt der Kristall. Als der Roboter erwacht, erscheint eine Karte zu einem neuen Abenteuer.",
        f"Niemand hatte diese Insel auf der Karte gefunden. Doch {name} entdeckt eine alte Karte im Sand. Leuchtende Zeichen führen durch den Dschungel bis zu einem Wasserfall. Dahinter wartet eine goldene Truhe. {name} öffnet sie gespannt. Statt Gold liegt darin ein Kompass, der auf etwas Geheimnisvolles zeigt.",
        f"Plötzlich blinkt ein Warnsignal in der Raumstation. {name} entdeckt eine kleine Rakete und startet sie. Vorbei an einem blauen Planeten taucht ein kleiner Roboter auf. Er braucht Hilfe bei der Rückkehr. Gemeinsam finden sie den Weg. Die Landung gelingt perfekt und aus der Mission wird eine neue Freundschaft.",
        f"Mitten im Wald verschwinden alle Lichter. {name} folgt einer Spur aus funkelnden Punkten und findet eine alte Maschine unter den Wurzeln. Ein Kristall passt genau in die Öffnung. Die Maschine beginnt zu leuchten. Augenblicklich erwacht der ganze Wald. Doch zwischen den Bäumen erscheint noch ein geheimnisvoller Lichtpunkt."
    ]
    script=scripts[(episode-1)%len(scripts)]
    return {
        "title":f"Folge {episode}: {title}",
        "description":"Eine originelle Roblox-inspirierte Cartoon-Geschichte.",
        "script":script,
        "story":story,
        "visual_style":"Hochwertiger 3D-Cartoon im Roblox-Look: blockige Figuren, klare Quaderformen, plastische Materialien, kräftige Farben, filmisches Licht, Hochformat 9:16.",
        "characters":[{"name":name,"appearance":clothes,"clothing":clothes,"personality":personality}],
        "scenes":scenes,
        "hashtags":["#Roblox","#Shorts","#Cartoon","#Story","#AI"]
    }

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
        log("Alle kostenlosen Text-KIs sind ausgefallen; lokale Story wird ohne API erstellt.")
        return local_story_fallback(state,comments)
    log("Keine Text-KI konfiguriert; lokale Story wird ohne API erstellt.")
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
    log("AI ERIK STORY-AUTOPILOT gestartet – Serienmodus aktiv")
    state=load_state()
    dry=os.getenv("DRY_RUN","true").lower() in ("1","true","yes")
    # GitHub Actions hat ein Zeitlimit von 6 Stunden. Wir beenden vorher
    # sauber, damit Episodenverlauf und Videos noch gespeichert werden können.
    deadline=time.monotonic()+5.5*60*60
    episode_dir=OUT/"episodes"
    episode_dir.mkdir(exist_ok=True)
    completed=0

    while time.monotonic()<deadline:
        ideas=get_comments(state)
        d=generate(state,ideas)
        d["episode"]=int(state.get("episode",0))+1
        log(f"Neue Geschichte | Folge {d['episode']} | {d['title']}")
        video=make_video(d)
        verify_video(video)
        vid=None if dry else upload(video,d)

        # Jede fertige Folge separat aufbewahren, bevor die nächste beginnt.
        archive=episode_dir/f"episode_{d['episode']:05d}.mp4"
        shutil.copy2(video,archive)

        state.update({"episode":d["episode"]})
        state.setdefault("history",[]).append({"episode":d["episode"],"title":d["title"],"story":d.get("story",""),"script":d.get("script",""),"change":d.get("story",""),"video_id":vid,"created_at":datetime.now(timezone.utc).isoformat()})
        state["history"]=state["history"][-100:]
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
        (OUT/"latest.json").write_text(json.dumps({"title":d["title"],"episode":d["episode"],"video_id":vid,"story":d.get("story","")},ensure_ascii=False,indent=2),encoding="utf-8")
        completed+=1
        log(f"Folge {d['episode']} fertig und gespeichert. Starte automatisch die nächste Folge.")

    log(f"Serienlauf beendet: {completed} neue Folge(n). Episodenverlauf gespeichert. "+("Videos liegen als Artifact bereit." if dry else "Videos wurden hochgeladen."))
if __name__=="__main__": main()
