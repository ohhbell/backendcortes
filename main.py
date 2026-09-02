
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re
from youtube_transcript_api import YouTubeTranscriptApi

app = FastAPI(title="Klap Backend - Light Free")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Req(BaseModel):
    url: str
    language: str = "pt"
    youtube_api_key: str = ""

def extract_id(url):
    patterns = [
        r'(?:v=|youtu\.be/|embed/|shorts/)([^&\?/]+)',
        r'youtube\.com/watch\?v=([^&]+)'
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def analyze(transcript):
    keywords = {
        "GANCHO": ["segredo","ninguém fala","vou te contar","como","por que","maior erro","nunca te contaram"],
        "PICO": ["nunca","sempre","impossível","absurdo","tem que","precisa"],
        "HUMOR": ["engraçado","meme","frango","cariani","kkk","risada"],
        "POLEMICA": ["polêmica","errado","mentira","odeio","não faça","pare de"],
        "HISTORIA": ["quando eu","lembro","aconteceu","era","história"]
    }
    moments = []
    for i, seg in enumerate(transcript):
        score = 50
        cat = "PICO"
        reason = "Tamanho ideal"
        low = seg['text'].lower()
        for c, words in keywords.items():
            if any(w in low for w in words):
                cat = c; score += 15; reason = f"Palavra-chave {c}"; break
        if seg['start'] < 60: score += 12; cat = "GANCHO"
        if "?" in seg['text']: score += 10
        if 10 <= len(seg['text']) <= 150: score += 5
        moments.append({
            "id": i, "start": seg['start'], "end": seg['end'],
            "startFormatted": seg['startFormatted'], "endFormatted": seg['endFormatted'],
            "text": seg['text'], "category": cat, "score": min(99, score),
            "reason": reason, "viralHook": seg['text'][:50].upper()
        })
    return sorted(moments, key=lambda x: x['score'], reverse=True)[:12]

@app.get("/")
def root():
    return {"ok": True, "message": "Klap Backend Light - Use POST /api/transcribe"}

@app.get("/health")
def health():
    return {"ok": True, "status": "light free - youtube captions"}

@app.post("/api/transcribe")
def transcribe(req: Req):
    video_id = extract_id(req.url)
    if not video_id:
        return {"error": "URL inválida, não achei ID do vídeo"}
    try:
        print(f"Buscando legenda para {video_id}")
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # tenta pegar pt, en, etc
        transcript_obj = None
        # tenta manual primeiro
        for lang in ['pt', 'pt-BR', 'en', 'en-US']:
            try:
                transcript_obj = transcript_list.find_transcript([lang])
                print(f"Achei manual {lang}")
                break
            except:
                continue
        # se não achou, pega auto-gerada
        if not transcript_obj:
            try:
                # pega qualquer uma que existir
                for t in transcript_list:
                    transcript_obj = t
                    print(f"Usando {t.language_code} ({'auto' if t.is_generated else 'manual'})")
                    break
            except Exception as e:
                print(f"Erro list: {e}")

        if not transcript_obj:
            return {"error": "Esse vídeo não tem legenda automática nem manual. Ative legenda no YouTube ou tente outro vídeo."}

        data = transcript_obj.fetch()
        transcript = []
        for seg in data:
            s = seg['start']
            d = seg['duration']
            transcript.append({
                "start": s,
                "end": s + d,
                "text": seg['text'].strip(),
                "startFormatted": f"{int(s//3600):02d}:{int((s%3600)//60):02d}:{int(s%60):02d}",
                "endFormatted": f"{int((s+d)//3600):02d}:{int(((s+d)%3600)//60):02d}:{int((s+d)%60):02d}"
            })

        moments = analyze(transcript)
        return {"success": True, "source": "youtube_captions", "transcript": transcript, "moments": moments, "videoId": video_id, "language": transcript_obj.language_code}

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": f"Erro ao buscar legenda: {str(e)}. Dica: vídeo precisa ter legenda automática ativada no YouTube (90% tem)."}
