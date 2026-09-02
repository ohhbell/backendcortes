
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re, os, json, tempfile, glob, traceback
from youtube_transcript_api import YouTubeTranscriptApi

app = FastAPI(title="Klap V3 Anti-Block")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Req(BaseModel):
    url: str
    language: str = "pt"

def extract_id(url):
    patterns = [r'(?:v=|youtu\.be/|embed/|shorts/)([^&\?/]+)']
    for pat in patterns:
        m = re.search(pat, url)
        if m: return m.group(1)[:11]
    return None

def parse_vtt_to_segments(vtt_text):
    # parse VTT simple
    segments = []
    import re
    # remove WEBVTT header
    lines = vtt_text.split("\n")
    i=0
    current_start=None
    current_text=[]
    # VTT timestamp pattern
    time_pat = re.compile(r'(\d+:)?\d+:\d+\.\d+\s*-->\s*(\d+:)?\d+:\d+\.\d+')
    def ts_to_sec(ts):
        ts=ts.strip().split(":")
        try:
            if len(ts)==3:
                h,m,s = ts
                return int(h)*3600 + int(m)*60 + float(s)
            elif len(ts)==2:
                m,s = ts
                return int(m)*60 + float(s)
            else:
                return float(ts[0])
        except:
            return 0.0
    while i < len(lines):
        line = lines[i]
        if time_pat.search(line):
            # flush previous
            if current_start is not None and current_text:
                txt = " ".join(current_text).strip()
                txt = re.sub(r'<[^>]+>', '', txt)
                if txt:
                    segments.append({"start": current_start, "text": txt})
            parts = line.split("-->")
            start_str = parts[0].strip().split(" ")[-1]
            current_start = ts_to_sec(start_str)
            current_text = []
            # next lines until blank
            i+=1
            while i < len(lines) and lines[i].strip()!="":
                if not time_pat.search(lines[i]):
                    current_text.append(lines[i].strip())
                i+=1
        else:
            i+=1
    if current_start is not None and current_text:
        txt = " ".join(current_text).strip()
        txt = re.sub(r'<[^>]+>', '', txt)
        if txt:
            segments.append({"start": current_start, "text": txt})
    # dedup and format
    final=[]
    for seg in segments:
        s=seg["start"]
        final.append({
            "start": s,
            "end": s+4,
            "text": seg["text"],
            "startFormatted": f"{int(s//3600):02d}:{int((s%3600)//60):02d}:{int(s%60):02d}",
            "endFormatted": f"{int((s+4)//3600):02d}:{int(((s+4)%3600)//60):02d}:{int((s+4)%60):02d}"
        })
    return final

def try_youtube_transcript_api(video_id):
    try:
        t_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # try get any
        for t in t_list:
            data = t.fetch()
            trans=[]
            for seg in data:
                s=seg['start']
                d=seg['duration']
                trans.append({
                    "start": s,
                    "end": s+d,
                    "text": seg['text'].strip(),
                    "startFormatted": f"{int(s//3600):02d}:{int((s%3600)//60):02d}:{int(s%60):02d}",
                    "endFormatted": f"{int((s+d)//3600):02d}:{int(((s+d)%3600)//60):02d}:{int((s+d)%60):02d}"
                })
            return trans, f"yt_api_{t.language_code}"
    except Exception as e:
        print(f"yt_api failed: {e}")
        traceback.print_exc()
    return None, None

def try_ytdlp(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmpdir = tempfile.mkdtemp()
    try:
        import yt_dlp
        # options to get auto subs without downloading video
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'writeautosub': True,
            'subtitleslangs': ['pt', 'pt-BR', 'en', 'en-US', 'es'],
            'subtitlesformat': 'vtt',
            'outtmpl': os.path.join(tmpdir, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            # anti-block
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # now download subs
            ydl.download([url])
        
        # find vtt files
        vtts = glob.glob(os.path.join(tmpdir, "*.vtt"))
        print(f"VTTs found: {vtts}")
        best = None
        for vtt_path in vtts:
            if 'pt' in vtt_path.lower():
                best = vtt_path
                break
        if not best and vtts:
            best = vtts[0]
        if best:
            with open(best, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            segs = parse_vtt_to_segments(content)
            if segs:
                return segs, f"ytdlp_{os.path.basename(best)}"
    except Exception as e:
        print(f"ytdlp failed: {e}")
        traceback.print_exc()
    finally:
        # cleanup
        try:
            import shutil; shutil.rmtree(tmpdir)
        except: pass
    return None, None

def analyze(transcript):
    keywords = {
        "GANCHO": ["segredo","ninguém","vou te contar","como","por que","maior erro","nunca te contaram","truque"],
        "PICO": ["nunca","sempre","impossível","absurdo","tem que","precisa","proibido"],
        "HUMOR": ["engraçado","meme","kkk","risada"],
        "POLEMICA": ["polêmica","errado","mentira","odeio","não faça","pare de"],
        "HISTORIA": ["quando eu","lembro","aconteceu","era","história","eu tinha"]
    }
    moments=[]
    for i, seg in enumerate(transcript):
        score=50; cat="PICO"; reason="Tamanho ideal"
        low=seg['text'].lower()
        for c, words in keywords.items():
            if any(w in low for w in words):
                cat=c; score+=20; reason=f"Gatilho {c}"; break
        if seg['start'] < 60: score+=15; cat="GANCHO"
        if "?" in seg['text']: score+=10
        if 10 <= len(seg['text']) <= 180: score+=5
        moments.append({
            "id": i, "start": seg['start'], "end": seg['end'],
            "startFormatted": seg['startFormatted'], "endFormatted": seg['endFormatted'],
            "text": seg['text'], "category": cat, "score": min(99, score),
            "reason": reason, "viralHook": seg['text'][:50].upper()
        })
    return sorted(moments, key=lambda x: x['score'], reverse=True)[:12]

@app.get("/")
def root(): return {"ok": True, "message": "Klap V3 Anti-Block"}

@app.get("/health")
def health(): return {"ok": True, "status": "v3 anti-block ytdlp + yt_api"}

@app.post("/api/transcribe")
def transcribe(req: Req):
    video_id = extract_id(req.url)
    if not video_id:
        return {"error": "URL inválida. Use link tipo youtube.com/watch?v=..."}
    print(f"=== Transcribe request video_id={video_id} ===")
    
    transcript, source = try_youtube_transcript_api(video_id)
    if not transcript:
        print("Tentando ytdlp fallback...")
        transcript, source = try_ytdlp(video_id)
    
    if not transcript:
        return {"error": "YouTube bloqueou o IP do Render. Tente um vídeo diferente ou aguarde 5 min. Vídeos com legenda manual (não só automática) funcionam melhor. Dica: teste com vídeo grande do Cariani/Flow que tem legenda PT."}
    
    moments = analyze(transcript)
    return {"success": True, "source": source, "transcript": transcript, "moments": moments, "videoId": video_id, "language": "pt"}
