from flask import Flask, request, Response, send_from_directory, render_template, render_template_string
import threading
import time
from db import init_db, get_db
from twilio.twiml.voice_response import VoiceResponse

init_db()
from twilio.rest import Client
import requests, os
import re, unicodedata
import google.generativeai as genai
import whisper
import shutil
from pydub import AudioSegment
from gtts import gTTS
import edge_tts
import asyncio
from dotenv import load_dotenv

# ---------------- LOAD ENV ----------------
from pathlib import Path
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

print("LOADED SID:", os.getenv("TWILIO_ACCOUNT_SID"))
print("LOADED GEMINI KEY:", "YES" if os.getenv("GOOGLE_API_KEY") else "NO")

app = Flask(__name__, template_folder="templates", static_folder="static")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
YOUR_PHONE = os.getenv("YOUR_PHONE")
PUBLIC_URL = os.getenv("PUBLIC_URL")

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
gemini_model = genai.GenerativeModel("gemini-flash-latest")

# Load Whisper model for accurate multilingual speech recognition
print("🚀 Loading Whisper model...")
whisper_model = whisper.load_model("small")  # Using 'small' for better Indian language support
print("✅ Whisper model loaded")

# ---------------- VALIDATION ----------------
if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE, YOUR_PHONE, PUBLIC_URL]):
    raise Exception("❌ Missing environment variables. Check your .env file")

# ---------------- ASYNC ----------------
# Using threading instead of Celery so you don't need a Redis server running!

os.makedirs("static", exist_ok=True)

# ---------------- HOME PAGE ----------------
@app.route("/")
def index():
    return '<h1>Voice Bot is Running! 🚀</h1><p><a href="/call-me">Click here to trigger a call to your phone</a></p><p><a href="/dashboard">View Call Analytics Dashboard</a></p>'

@app.route("/jinja-test")
def jinja_test():
    return render_template_string("<h1>{{ 2 + 2 }}</h1>")

# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    status = request.args.get("status")
    language = request.args.get("language")  # 8) Add language filter
    decision = request.args.get("decision")  # 6) Add decision filter
    page = int(request.args.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page
    
    conn = get_db()
    
    # 1) Get robust success rate: completed & not "unclear"
    stats = conn.execute("""
    SELECT 
      COUNT(*) AS total,
      SUM(CASE WHEN status='completed' AND decision IN ('confirmed','cancelled') THEN 1 ELSE 0 END) AS success,
      AVG(latency) as avg_latency
    FROM calls
    """).fetchone()
    
    # 8) Get success rate by language
    lang_stats = conn.execute("""
    SELECT 
      language,
      COUNT(*) AS lang_total,
      SUM(CASE WHEN status='completed' AND decision IN ('confirmed','cancelled') THEN 1 ELSE 0 END) AS lang_success
    FROM calls
    WHERE language IS NOT NULL AND language != ''
    GROUP BY language
    ORDER BY lang_total DESC
    """).fetchall()
    
    # 6) Get confidence distribution for badges
    confidence_stats = conn.execute("""
    SELECT 
      confidence,
      COUNT(*) AS count
    FROM calls
    WHERE confidence IS NOT NULL AND confidence != ''
    GROUP BY confidence
    ORDER BY confidence DESC
    """).fetchall()
    
    # Get unique languages for filter
    languages = conn.execute("SELECT DISTINCT language FROM calls WHERE language IS NOT NULL AND language != '' ORDER BY language").fetchall()

    # 2) Get filtered rows with pagination (now with language + decision filter)
    where_clause = "WHERE 1=1"
    params = []
    
    if status:
        where_clause += " AND status=?"
        params.append(status)
    
    if language:
        where_clause += " AND language=?"
        params.append(language)
    
    if decision:
        where_clause += " AND decision=?"
        params.append(decision)
    
    rows = conn.execute(
        f"SELECT * FROM calls {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()
        
    conn.close()
    
    # Format stats safely
    total = stats["total"] if stats["total"] else 0
    success_count = stats["success"] if stats["success"] else 0
    success_rate = round((success_count / total * 100), 1) if total > 0 else 0
    avg_latency = round(stats["avg_latency"], 2) if stats["avg_latency"] else 0
    
    # Format language stats
    lang_stats_formatted = []
    for row in lang_stats:
        lang = row["language"]
        lang_total = row["lang_total"]
        lang_success = row["lang_success"] if row["lang_success"] else 0
        lang_success_rate = round((lang_success / lang_total * 100), 1) if lang_total > 0 else 0
        lang_stats_formatted.append({
            "language": lang,
            "total": lang_total,
            "success": lang_success,
            "success_rate": lang_success_rate
        })
    
    # Format confidence stats
    confidence_stats_formatted = {}
    for row in confidence_stats:
        confidence_stats_formatted[row["confidence"]] = row["count"]
    
    return render_template("dashboard.html", 
                         rows=rows, 
                         total=total, 
                         success_rate=success_rate, 
                         avg_latency=avg_latency, 
                         current_status=status, 
                         current_language=language,
                         current_decision=decision,
                         languages=[row["language"] for row in languages],
                         lang_stats=lang_stats_formatted,
                         confidence_stats=confidence_stats_formatted,
                         page=page, 
                         per_page=per_page)

# ---------------- CALL TRIGGER ----------------
@app.route("/call-me")
def call_me():
    try:

        if not PUBLIC_URL:
            return "❌ PUBLIC_URL not set"

        if not PUBLIC_URL:
            return "❌ PUBLIC_URL not set"
        # ✅ Create client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # ✅ Make call
        call = client.calls.create(
            url=f"{PUBLIC_URL}/voice",
            to=YOUR_PHONE,
            from_=TWILIO_PHONE
        )

        return f"✅ Calling... SID: {call.sid}"

    except Exception as e:
        return f"❌ Error: {str(e)}"
# ---------------- VOICE ----------------
@app.route("/voice", methods=["POST", "GET"])
def voice():
    r = VoiceResponse()

    # English intro (OK to use .say here)
    r.say("Hello. You have a new order. Please say yes to confirm or no to cancel.")

    r.record(
        action=f"{PUBLIC_URL}/process?a=1",  # 5) Start with attempt=1
        method="POST",
        maxLength=8,
        playBeep=True,
        timeout=3,
        trim="trim-silence"
    )

    return Response(str(r), mimetype="text/xml")

# ---------------- PROCESS ----------------
@app.route("/process", methods=["POST"])
def process():
    recording_url = request.form.get("RecordingUrl")
    call_sid = request.form.get("CallSid", "default")
    attempt = int(request.args.get("a", 1))  # Track attempt number
    next_a = min(attempt + 1, 2)  # 3) Keep strict retry cap: never exceed attempt 2

    response = VoiceResponse()

    if recording_url:
        # Make processing synchronous to ensure DB is updated before redirect
        process_audio(recording_url, call_sid, attempt)
        response.say("Processing your response, please wait.")
    else:
        response.say("No recording received.")

    response.pause(length=5)
    # Keep current attempt for response logic; response route decides whether to retry with a=2.
    response.redirect(f"{PUBLIC_URL}/response?CallSid={call_sid}&a={attempt}")

    return Response(str(response), mimetype="text/xml")

import time

def delete_later(path, delay=60):
    def _del():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
            print("🧹 Cleaned up old audio file:", path)
    threading.Thread(target=_del, daemon=True).start()

# ---------------- PLAY RESPONSE ----------------
@app.route("/response", methods=["POST", "GET"])
def play_response():
    r = VoiceResponse()
    
    call_sid = request.values.get("CallSid")
    attempt = int(request.args.get("a", 1))  # 5) Get attempt number

    if not call_sid:
        r.say("Missing session id.")
        r.hangup()
        return Response(str(r), mimetype="text/xml")

    file_path = f"static/{call_sid}.mp3"

    # Read latest decision for this call to decide retry vs finalize.
    latest_decision = None
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT decision FROM calls WHERE call_sid=? ORDER BY created_at DESC LIMIT 1",
            (call_sid,)
        ).fetchone()
        conn.close()
        if row:
            latest_decision = row["decision"]
    except Exception as db_e:
        print(f"[{call_sid}] ⚠️ Decision lookup failed: {db_e}")

    if os.path.exists(file_path):
        if latest_decision == "unclear" and attempt == 1:
            print(f"[{call_sid}] 🔁 Unclear on attempt 1, asking for one retry")
            r.say("I didn't catch that. Please say yes or no.")
            r.record(
                action=f"{PUBLIC_URL}/process?a=2",
                method="POST",
                maxLength=5,
                playBeep=True,
                timeout=3,
                trim="trim-silence"
            )
        elif latest_decision is not None:
            print(f"[{call_sid}] ▶ playing static/{call_sid}.mp3 (attempt {attempt})")
            r.play(f"{PUBLIC_URL}/static/{call_sid}.mp3")
            r.say("Thank you. Goodbye.")
            r.hangup()
            # Give Twilio 60 seconds to download the file, then delete it to save space.
            delete_later(file_path, delay=60)
        else:
            # Decision not ready yet, redirect back
            print(f"[{call_sid}] ⏳ Decision not ready, redirecting")
            r.say("Still processing. Please wait.")
            r.pause(length=3)
            r.redirect(f"{PUBLIC_URL}/response?CallSid={call_sid}&a={attempt}")
    else:
        if attempt >= 2:
            # Stop on second attempt if processing still failed to avoid infinite loop.
            r.say("Sorry, we could not process your response. Please try again later.")
            r.hangup()
        else:
            r.say("Still processing. Please wait.")
            r.pause(length=3)
            r.redirect(f"{PUBLIC_URL}/response?CallSid={call_sid}&a={attempt}")

    return Response(str(r), mimetype="text/xml")

# ---------------- STATIC ----------------
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

# 4) Export CSV for demos and analysis
@app.route("/export")
def export_csv():
    import csv, io
    conn = get_db()
    rows = conn.execute("SELECT * FROM calls ORDER BY created_at DESC").fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    if rows:
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow(list(r))
    else:
        writer.writerow(["call_sid", "user_text", "reply", "status", "created_at", "latency", "decision", "language"])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=calls.csv"}
    )


# 3) TEXT NORMALIZATION - handle punctuation, diacritics, mixed scripts
def normalize(s):
    """Normalize text for consistent keyword matching"""
    s = unicodedata.normalize("NFKC", s.lower())
    # Keep Indian scripts (Devanagari, Kannada, Tamil, Telugu, etc.)
    s = re.sub(r"[^\w\s\u0900-\u0D7F]", " ", s)  # \u0900-\u0D7F covers Indian scripts
    return re.sub(r"\s+", " ", s).strip()


def detect_language_by_script(text, default="en"):
    """Infer language from Unicode script and strong keyword signals."""
    if not text:
        return default

    if re.search(r"[\u0C80-\u0CFF]", text):
        return "kn"
    if re.search(r"[\u0900-\u097F]", text):
        # Hindi and Marathi share Devanagari script; use known Marathi-specific words first.
        if any(x in text for x in ["होय", "नकाशी", "नको", "हो"]):
            return "mr"
        return "hi"
    if re.search(r"[A-Za-z]", text):
        return "en"

    return default


def detect_whisper_language(result, default="en"):
    """Pick the best Whisper language info available."""
    language = result.get("language") or default
    if not language:
        probs = result.get("language_probs")
        if isinstance(probs, list) and probs:
            best = max(probs, key=lambda x: x.get("prob", 0))
            language = best.get("language", default)
    return language

PRE_TRANSLATED = {
    "hi": {
        "confirmed": "आपका ऑर्डर पुष्टि हो गया है। धन्यवाद।",
        "cancelled": "आपका ऑर्डर रद्द कर दिया गया है।",
        "unclear": "क्षमा करें, मुझे समझ नहीं आया। कृपया हाँ या नहीं कहें।"
    },
    "kn": {
        "confirmed": "ನಿಮ್ಮ ಆದೇಶ ದೃಢೀಕರಿಸಲಾಗಿದೆ. ಧನ್ಯವಾದಗಳು.",
        "cancelled": "ನಿಮ್ಮ ಆದೇಶ ರದ್ದುಪಡಿಸಲಾಗಿದೆ.",
        "unclear": "ಕ್ಷಮಿಸಿ, ನಾನು ಅರ್ಥ ಮಾಡಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಹೌದು ಅಥವಾ ಇಲ್ಲ ಎಂದು ಹೇಳಿ."
    },
    "mr": {
        "confirmed": "तुमची ऑर्डर कन्फर्म झाली आहे. धन्यवाद.",
        "cancelled": "तुमची ऑर्डर रद्द झाली आहे.",
        "unclear": "क्षमस्व, मला समजले नाही. कृपया होय किंवा नाही म्हणा."
    }
}

# 4) DISABLE CACHE FOR NOW (resolves language playback issues)
"""
CACHE = {
    ("en", "confirmed"): "static/cache_en_confirmed.mp3",
...
"""

# Pre-generate intro audio on startup (No longer used, using .say for English intro)
"""
def init_intro():
    ...
init_intro()
"""


# Pre-generate cache files on startup (if needed)
def init_cache():
    """Generate cached TTS files for common phrases"""
    cache_phrases = {
        ("en", "confirmed"): "Your order is confirmed. Thank you.",
        ("en", "cancelled"): "Your order is cancelled.",
        ("en", "unclear"): "Sorry, I didn't understand. Please say yes or no.",
        ("kn", "confirmed"): "ನಿಮ್ಮ ಆದೇಶ ದೃಢೀಕರಿಸಲಾಗಿದೆ. ಧನ್ಯವಾದಗಳು.",
        ("kn", "cancelled"): "ನಿಮ್ಮ ಆದೇಶ ರದ್ದುಪಡಿಸಲಾಗಿದೆ.",
        ("kn", "unclear"): "ಕ್ಷಮಿಸಿ, ನಾನು ಅರ್ಥ ಮಾಡಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಹೌದು ಅಥವಾ ಇಲ್ಲ ಎಂದು ಹೇಳಿ.",
        ("hi", "confirmed"): "आपका ऑर्डर पुष्टि हो गया है। धन्यवाद।",
        ("hi", "cancelled"): "आपका ऑर्डर रद्द कर दिया गया है।",
        ("hi", "unclear"): "क्षमा करें, मुझे समझ नहीं आया। कृपया हां या नहीं कहें।",
    }
    
    for (lang, decision), phrase in cache_phrases.items():
        cache_file = CACHE.get((lang, decision))
        if cache_file and not os.path.exists(cache_file):
            try:
                tts = gTTS(phrase, lang=lang)
                tts.save(cache_file)
                print(f"✅ Cached: {cache_file}")
            except Exception as e:
                print(f"⚠️ Failed to cache {cache_file}: {e}")


# Call cache init on startup
# init_cache()


# ---------------- BACKGROUND TASK ----------------
def process_audio(recording_url, call_sid="default", attempt=1):
    start_time = time.time()
    confidence = "unknown"  # 6) Track confidence for dashboard
    avg_logprob = -1.0  # Initialize to avoid UnboundLocalError
    try:
        t_stt = 0
        t_ai = 0
        t_tts = 0
        
        # 1) Download audio from Twilio (Must include Auth!)
        audio_content = requests.get(
            recording_url + ".wav", 
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        ).content
        
        input_file = f"input_{call_sid}.wav"
        with open(input_file, "wb") as f:
            f.write(audio_content)

        # 5) IMPROVE AUDIO QUALITY (normalize + boost volume) 🔥
        try:
            sound = AudioSegment.from_wav(input_file)
            sound = sound.set_frame_rate(16000).set_channels(1).normalize()
            sound = sound + 15  # Boost volume
            clean_file = f"clean_{call_sid}.wav"
            sound.export(clean_file, format="wav")
            print(f"[{call_sid}] ✅ Audio cleaned and normalized")
        except Exception as audio_e:
            print(f"[{call_sid}] ⚠️ Audio processing failed, using original: {audio_e}")
            clean_file = input_file

        # 2) NOISE FLOOR CHECK (skip junk early) 🔇
        try:
            audio = AudioSegment.from_wav(clean_file)
            # Skip if too short or too quiet (8kHz phone line threshold)
            if audio.duration_seconds < 0.7 or audio.dBFS < -45:
                print(f"[{call_sid}] ⚠️ Below noise floor: duration={audio.duration_seconds:.2f}s, dBFS={audio.dBFS:.1f}")
                user_text = ""
                lang = "unknown"
                decision = "unclear"
                confidence = "low"
                t_stt = 0
                t_ai = 0
                t_tts = 0
                # Skip to TTS generation with empty text
                skip_to_tts = True
            else:
                skip_to_tts = False
                print(f"[{call_sid}] ✅ Audio valid: duration={audio.duration_seconds:.2f}s, dBFS={audio.dBFS:.1f}")
        except Exception as noise_e:
            print(f"[{call_sid}] ⚠️ Noise check failed: {noise_e}, continuing...")
            skip_to_tts = False

        # 1) WHISPER: Force language detection with confidence checking 🎯
        if not skip_to_tts:
            stt_start = time.time()

            try:
                result = whisper_model.transcribe(
                    clean_file,
                    task="transcribe",
                    language=None,
                    temperature=0,
                    best_of=5,
                    beam_size=5,
                    fp16=False
                )

                user_text = result.get("text", "").strip()
                lang = detect_whisper_language(result, default="en")

                avg_logprob = -1.0

                segments = result.get("segments", [])
                if segments:
                    avg_logprob = sum(
                        s.get("avg_logprob", -1) for s in segments
                    ) / len(segments)

                if avg_logprob is None:
                    avg_logprob = -1.0

                # Use script detection plus common keyword matches to fix Whisper language misses.
                inferred_lang = detect_language_by_script(user_text, default=lang)
                text_lower = user_text.lower()
                if inferred_lang == "en" and any(x in text_lower for x in ["haan", "nahi", "hai", "ji", "bilkul"]):
                    inferred_lang = "hi"
                elif inferred_lang == "en" and any(x in text_lower for x in ["haudu", "illa", "beda"]):
                    inferred_lang = "kn"
                elif inferred_lang == "en" and any(x in text_lower for x in ["hoy", "nako"]):
                    inferred_lang = "mr"
                elif inferred_lang == "en" and any(x in text_lower for x in ["yes", "no", "confirm", "order", "cancel", "yeah", "yep"]):
                    inferred_lang = "en"

                lang = inferred_lang

                print(f"[DEBUG] USER TEXT: {user_text}")
                print(f"[DEBUG] LANG: {lang}")
                print(f"[DEBUG] CONF: {avg_logprob:.2f}")

                if not user_text:
                    decision = "unclear"
                    confidence = "low"

                elif avg_logprob < -2.5:
                    decision = "unclear"
                    confidence = "low"

                else:
                    if avg_logprob > -1.0:
                        confidence = "high"
                    elif avg_logprob > -1.5:
                        confidence = "medium"
                    else:
                        confidence = "low"

                    decision = None

            except Exception as stt_e:
                print(f"[ERROR] Whisper failed: {stt_e}")
                user_text = "[Error]"
                lang = "en"
                decision = "unclear"
                confidence = "low"
                avg_logprob = -1.0

            t_stt = round(time.time() - stt_start, 2)

        else:
            user_text = ""
            lang = "en"
            decision = "unclear"
            confidence = "low"
            avg_logprob = -1.0
            t_stt = 0
        # Clean up audio files
        try:
            os.remove(input_file)
            if clean_file != input_file:
                os.remove(clean_file)
        except:
            pass

        # 2) MANUAL DECISION LOGIC with native script support 🌐
        if decision is None or decision == "unclear":  # Determine decision if not already set
            # 3) Normalize text for consistent keyword matching
            t = normalize(user_text)

            # ---------- YES ----------
            if any(x in t for x in [
                # English
                "yes", "yeah", "yep", "ok", "okay", "confirm", "sure", "please",

                # Hindi
                "हाँ", "haan", "ha", "h", "ji", "bilkul", "theek", "theek hai", "haan ji",

                # Kannada
                "ಹೌದು", "haudu", "houdu", "aavu", "avnu", "avunu",

                # Marathi
                "होय", "hoy", "hoo", "ho", "ya", "thik", "thik ahe"
            ]):
                decision = "confirmed"

            # ---------- NO ----------
            elif any(x in t for x in [
                # English
                "no", "nope", "cancel", "stop", "nah",

                # Hindi
                "नहीं", "nahi", "nai", "na", "nehi", "nahin", "nahi ji",

                # Kannada
                "ಇಲ್ಲ", "illa", "beda", "bēda", "illa",

                # Marathi
                "नाही", "nahi", "nako", "naa", "naahi"
            ]):
                decision = "cancelled"

            else:
                decision = "unclear"

        # 3) Double-check for short utterances (1-2 words)
        word_count = len(user_text.split())
        if word_count <= 2 and decision == "unclear":
            reply_en = "I didn't catch that. Please say yes to confirm or no to cancel."
            print(f"[{call_sid}] Short utterance ({word_count} words), asking for clarification")
        else:
            # 3) DETERMINE ENGLISH REPLY (no Gemini)
            if decision == "confirmed":
                reply_en = "Your order is confirmed. Thank you."
            elif decision == "cancelled":
                reply_en = "Your order is cancelled."
            else:
                reply_en = "Sorry, I didn't understand. Please say yes or no."
        
        print(f"[{call_sid}] Decision: {decision}")

        ai_start = time.time()
        
        # 4) TRANSLATE TO DETECTED LANGUAGE (Gemini only for translation) 🌐
        try:
            lang_name_map = {
                "en": "English",
                "hi": "Hindi",
                "kn": "Kannada",
                "mr": "Marathi"
            }
            
            lang_name = lang_name_map.get(lang, lang)
            
            if lang != "en":
                # Check for pre-translated response first to save API quota
                lang_replies = PRE_TRANSLATED.get(lang)
                if lang_replies and decision and decision in lang_replies:
                    reply_local = lang_replies[decision]
                    print(f"[DEBUG] Using PRE-TRANSLATED: {reply_local}")
                else:
                    try:
                        translate_prompt = f"""
                        Translate this into {lang_name}. Only give the translated sentence:

                        {reply_en}
                        """
                        response = gemini_model.generate_content(translate_prompt)
                        reply_local = response.text.strip()
                        # Clean up potential markdown formatting or quotes from Gemini
                        reply_local = re.sub(r'["\']', '', reply_local).split('\n')[0].strip()
                        print(f"[DEBUG] TRANSLATED (Gemini): {reply_local}")
                    except Exception as e:
                        print(f"[ERROR] Gemini quota or error: {e}")
                        reply_local = reply_en
            else:
                reply_local = reply_en
                
            print(f"[{call_sid}] Reply ({lang_name}): {reply_local}")
        except Exception as trans_e:
            print(f"[{call_sid}] Translation error: {trans_e}")
            reply_local = reply_en
        
        t_ai = round(time.time() - ai_start, 2)

        # TEXT -> SPEECH (Edge TTS) with high-quality neural voices 🎤
        tts_start = time.time()
        out_file = f"static/{call_sid}.mp3"
        
        edge_voice_map = {
            "hi": "hi-IN-MadhurNeural",
            "kn": "kn-IN-GaganNeural",
            "mr": "mr-IN-ManoharNeural",
            "en": "en-US-AvaNeural"
        }
        tts_voice = edge_voice_map.get(lang, "en-US-AvaNeural")

        try:
            communicate = edge_tts.Communicate(reply_local, tts_voice)
            asyncio.run(communicate.save(out_file))
            print(f"[DEBUG] Edge TTS SUCCESS ({tts_voice})")
        except Exception as e:
            print(f"Edge TTS failed, fallback to gTTS: {e}")
            try:
                gTTS(text=reply_local, lang=lang if lang in ["hi", "kn", "en"] else "en").save(out_file)
            except:
                gTTS(text=reply_en, lang="en").save(out_file)
        
        t_tts = round(time.time() - tts_start, 2)
        
        # LOG TO DATABASE (with confidence for dashboard)
        latency = round(time.time() - start_time, 2)
        print(f"[{call_sid}] ✅ Process Complete")
        print(f"[{call_sid}] ⏱ stt={t_stt}s ai={t_ai}s tts={t_tts}s total={latency}s")
        
        # 5) CANONICAL VERDICT LINE (one line for all debugging)
        print(f"[{call_sid}] lang={lang} text='{user_text}' decision={decision} conf={avg_logprob:.2f} tts={tts_voice}")

        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO calls (call_sid, user_text, reply, status, latency, decision, language, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (call_sid, user_text, reply_local, "completed", latency, decision, lang, confidence)
            )
            conn.commit()
            conn.close()
        except Exception as db_e:
            print("DB Error:", db_e)

        return reply_local

    except Exception as e:
        print("❌ ERROR:", str(e))
        latency = round(time.time() - start_time, 2)
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO calls (call_sid, user_text, reply, status, latency, decision, language) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (call_sid, "Error/Failed", str(e), "failed", latency, "unclear", "en")
            )
            conn.commit()
            conn.close()
        except Exception as db_e:
            pass
        return "error"


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)