
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os, tempfile, shutil, re
from youtube_transcript_api import YouTubeTranscriptApi
from pytube import YouTube
import whisper

app = FastAPI(title="Klap V4 Free - YouTube Link Auto")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Req(BaseModel):
    url: str
    language: str = "pt"
    youtube_api_key: str = ""  # opcional, user tem

def extract_id(url):
    m = re.search(r'(?:v=|youtu\.be/|embed/)([^&\?/]+)', url)
    return m.group(1) if m else None

# Carrega whisper tiny (39MB) - 100% gratis, roda local, sem OpenAI
# Modelos: tiny, base, small, medium. tiny é mais rápido e grátis
model = None
def get_model():
    global model
    if model is None:
        print("Carregando Whisper tiny (free)...")
        model = whisper.load_model("tiny")
    return model

@app.post("/api/transcribe")
def transcribe(req: Req):
    video_id = extract_id(req.url)
    if not video_id:
        return {"error": "URL inválida"}

    tmpdir = tempfile.mkdtemp()
    try:
        # TENTATIVA 1: tenta pegar legenda pronta do YouTube (se existir, é instantâneo e grátis)
        # Usa youtube_transcript_api (não precisa da sua chave)
        try:
            print(f"Tentando pegar legenda do YouTube para {video_id}")
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            # tenta pt, pt-BR, en
            trans = None
            for lang in ['pt', 'pt-BR', 'en', 'en-US']:
                try:
                    trans = transcript_list.find_transcript([lang])
                    break
                except: continue
            if not trans:
                trans = transcript_list.find_transcript(transcript_list._generated_transcripts.keys()) if hasattr(transcript_list, '_generated_transcripts') else list(transcript_list)[0]

            data = trans.fetch()
            transcript = []
            for seg in data:
                transcript.append({
                    "start": seg['start'],
                    "end": seg['start'] + seg['duration'],
                    "text": seg['text'],
                    "startFormatted": f"{int(seg['start']//3600):02d}:{int((seg['start']%3600)//60):02d}:{int(seg['start']%60):02d}",
                    "endFormatted": f"{int((seg['start']+seg['duration'])//3600):02d}:{int(((seg['start']+seg['duration'])%3600)//60):02d}:{int((seg['start']+seg['duration'])%60):02d}"
                })
            print(f"Legenda encontrada: {len(transcript)} segmentos")
            # Se achou legenda, já retorna com análise
            moments = analyze(transcript)
            return {"success": True, "source": "youtube_captions", "transcript": transcript, "moments": moments, "videoId": video_id}
        except Exception as e:
            print(f"Sem legenda pronta, vai transcrever áudio: {e}")

        # TENTATIVA 2: baixa áudio e transcreve com Whisper FREE (sem OpenAI, sem custo)
        print("Baixando áudio...")
        yt = YouTube(req.url)
        audio_stream = yt.streams.filter(only_audio=True).first()
        audio_path = os.path.join(tmpdir, "audio.mp4")
        audio_stream.download(output_path=tmpdir, filename="audio.mp4")

        print("Transcrevendo com Whisper tiny (free)...")
        m = get_model()
        result = m.transcribe(audio_path, language=req.language, verbose=False)
        
        transcript = []
        for seg in result['segments']:
            transcript.append({
                "start": seg['start'],
                "end": seg['end'],
                "text": seg['text'].strip(),
                "startFormatted": f"{int(seg['start']//3600):02d}:{int((seg['start']%3600)//60):02d}:{int(seg['start']%60):02d}",
                "endFormatted": f"{int(seg['end']//3600):02d}:{int((seg['end']%3600)//60):02d}:{int(seg['end']%60):02d}"
            })

        moments = analyze(transcript)
        return {"success": True, "source": "whisper_free", "transcript": transcript, "moments": moments, "videoId": video_id}

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

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

@app.get("/health")
def health():
    return {"ok": True, "whisper": "tiny free - sem OpenAI", "youtube_key_needed": False}

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
