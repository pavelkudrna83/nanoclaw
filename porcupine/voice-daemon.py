#!/usr/bin/env python3
"""
NanoClaw Voice Daemon
Listens for "Hey Gimme" wake word via Porcupine.
First detection = start recording, second = stop recording + transcribe + inject into NanoClaw.
"""

import io
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import re

import pvporcupine
from pvrecorder import PvRecorder

# --- Config ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
KEYWORD_PATH = str(Path(__file__).resolve().parent / "Hey-Gimme_en_mac_v4_0_0.ppn")
DB_PATH = str(PROJECT_ROOT / "store" / "messages.db")
CHAT_JID = "tg:8253215818"  # Telegram main chat
SOUND_START = "/System/Library/Sounds/Purr.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"
SOUND_CLIPBOARD = "/System/Library/Sounds/Tink.aiff"
SOUND_CANCEL = "/System/Library/Sounds/Basso.aiff"
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
PLAYBACK_PID_FILE = os.path.join(tempfile.gettempdir(), "nanoclaw-playback.pid")


def load_env():
    """Load .env file into a dict."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def play_sound(path):
    """Play a sound file asynchronously."""
    subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_audio_playing():
    """Check if NanoClaw is currently playing audio via afplay."""
    try:
        with open(PLAYBACK_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # check if process exists
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def stop_audio_playback():
    """Kill the running audio playback process (afplay) and its process group."""
    pid = is_audio_playing()
    if pid:
        try:
            os.killpg(os.getpgid(pid), 15)  # SIGTERM to process group
            print(f"  Audio playback interrupted (pgid of pid {pid})")
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, 15)  # fallback to single process kill
                print(f"  Audio playback interrupted (pid {pid})")
            except ProcessLookupError:
                pass
        try:
            os.unlink(PLAYBACK_PID_FILE)
        except FileNotFoundError:
            pass
        return True
    return False


def transcribe(audio_bytes, openai_key):
    """Transcribe audio via OpenAI Whisper API."""
    from openai import OpenAI

    client = OpenAI(api_key=openai_key)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.wav"
    transcript = client.audio.transcriptions.create(
        file=audio_file,
        model="whisper-1",
        response_format="text",
        language="cs",
        prompt="audio session, schránka, zrušit, nová session, Hey Gimme",
    )
    return transcript.strip()


def inject_message(text, chat_jid):
    """Insert a message into NanoClaw's SQLite DB."""
    db = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    msg_id = f"voice-{int(time.time() * 1000)}"
    db.execute(
        "INSERT OR IGNORE INTO messages (id, chat_jid, sender, sender_name, content, timestamp, is_from_me, is_bot_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, chat_jid, "voice-daemon", "Pavel", f"[Voice: {text}]", now, 0, 0),
    )
    db.commit()
    db.close()
    return now


def send_telegram_transcript(text, chat_jid, bot_token):
    """Send voice transcript to Telegram so user sees it in chat."""
    import urllib.request
    import urllib.parse
    numeric_id = chat_jid.replace("tg:", "")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": numeric_id,
        "text": f"🎤 {text}",
        "disable_notification": "true",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"  Failed to send Telegram transcript: {e}")


_WAKE_WORD_RE = re.compile(
    r'\s*[,.]?\s*(?:h[ea][ijy][\s-]*[dgj][iey]+[\s-]*m+[iey]+|h[ea][ijy][\s-]*jimm?[iey]|h[ea]j[iy]m[iey]+|g[iy]m+[iey]+|h[ea][ijy])[\s.,!?]*$',
    re.IGNORECASE
)


def strip_wake_words(text):
    """Remove trailing wake word variations from text."""
    text = text.strip().strip(".,!?;:\"'")
    for _ in range(2):
        text = _WAKE_WORD_RE.sub('', text)
    return text.strip().strip(".,!?;:\"'")


def clean_transcription(text):
    """Strip wake words, mode announcement leak, and punctuation from transcription."""
    text = strip_wake_words(text)
    # Remove leading mode announcement leaked from speaker into mic
    text = re.sub(r'^(?:schránka|audio)\s*[,.]?\s*', '', text, flags=re.IGNORECASE)
    return text.strip().strip(".,!?;:\"'")


def copy_to_clipboard(text):
    """Copy text to macOS clipboard via pbcopy."""
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


class VoiceSessionLog:
    """Manages voice session markdown files in the group workspace."""

    def __init__(self, group_folder):
        self.sessions_dir = PROJECT_ROOT / "groups" / group_folder / "voice-sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.current_file = None
        self._new_session()

    def _new_session(self):
        """Start a new session file."""
        now = datetime.now()
        filename = now.strftime("%Y-%m-%d_%H-%M") + ".md"
        self.current_file = self.sessions_dir / filename
        header = f"# Voice Session {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        self.current_file.write_text(header)
        print(f"  Voice session log: {self.current_file}")

    def new_session(self):
        """Public method to start a new session."""
        self._new_session()

    def log(self, sender, text):
        """Append a message to the current session file."""
        now = datetime.now().strftime("%H:%M")
        with open(self.current_file, "a") as f:
            f.write(f"**{sender}** ({now}): {text}\n\n")


def run_diagnostics(chat_jid, bot_token=None):
    """Run system diagnostics and report via voice."""
    results = []

    # 1. Audio výstup (afplay)
    try:
        r = subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"],
                          timeout=5, capture_output=True)
        results.append(("Audio výstup", r.returncode == 0))
    except Exception:
        results.append(("Audio výstup", False))

    # 2. Přehrání OGG (afplay s rate)
    test_ogg = os.path.join(tempfile.gettempdir(), "nanoclaw-diag.ogg")
    try:
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-f", "lavfi",
                       "-i", "sine=frequency=440:duration=0.5",
                       "-c:a", "libopus", "-f", "ogg", test_ogg,
                       "-y", "-loglevel", "error"],
                      timeout=5, capture_output=True)
        r = subprocess.run(["afplay", "-r", "1.25", test_ogg],
                          timeout=5, capture_output=True)
        results.append(("OGG přehrávání", r.returncode == 0))
    except Exception:
        results.append(("OGG přehrávání", False))
    finally:
        try:
            os.unlink(test_ogg)
        except OSError:
            pass

    # 3. Databáze
    try:
        db = sqlite3.connect(DB_PATH)
        count = db.execute("SELECT COUNT(*) FROM messages WHERE chat_jid = ?",
                          (chat_jid,)).fetchone()[0]
        db.close()
        results.append(("Databáze", True, f"{count} zpráv"))
    except Exception as e:
        results.append(("Databáze", False, str(e)))

    # 4. NanoClaw process
    try:
        r = subprocess.run(["pgrep", "-f", "nanoclaw.*index"],
                          capture_output=True, text=True, timeout=5)
        running = r.returncode == 0
        results.append(("NanoClaw proces", running))
    except Exception:
        results.append(("NanoClaw proces", False))

    # 5. Audio zařízení
    try:
        mic = subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-t", "input", "-c"],
                            capture_output=True, text=True, timeout=5)
        spk = subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-t", "output", "-c"],
                            capture_output=True, text=True, timeout=5)
        mic_name = mic.stdout.strip()
        spk_name = spk.stdout.strip()
        results.append(("Mikrofon", bool(mic_name), mic_name))
        results.append(("Reproduktor", bool(spk_name), spk_name))
    except Exception:
        results.append(("Audio zařízení", False))

    # Sestavit hlášení
    ok_count = sum(1 for r in results if r[1])
    total = len(results)

    lines = [f"Diagnostika: {ok_count} z {total} ok."]
    for r in results:
        status = "ok" if r[1] else "chyba"
        detail = f", {r[2]}" if len(r) > 2 else ""
        lines.append(f"{r[0]}: {status}{detail}")
        print(f"  DIAG: {r[0]}: {status}{detail}")

    # Nahlásit výsledky hlasem
    report = ". ".join(lines)
    subprocess.Popen(["say", "-v", "Zuzana", "-r", "220", report],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Odeslat do Telegramu
    if bot_token:
        tg_report = "\n".join([f"{'✅' if r[1] else '❌'} {r[0]}" +
                              (f" ({r[2]})" if len(r) > 2 else "")
                              for r in results])
        send_telegram_transcript(f"🔧 Diagnostika:\n{tg_report}", chat_jid, bot_token)

    return results


def frames_to_wav(frames, sample_rate):
    """Convert raw PCM frames to WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for frame in frames:
            wf.writeframes(struct.pack(f"{len(frame)}h", *frame))
    return buf.getvalue()


def main():
    env = load_env()
    access_key = env.get("PICOVOICE_ACCESS_KEY")
    openai_key = env.get("OPENAI_API_KEY")

    if not access_key:
        print("ERROR: PICOVOICE_ACCESS_KEY not found in .env")
        sys.exit(1)
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    bot_token = env.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("WARNING: TELEGRAM_BOT_TOKEN not found in .env, transcripts won't appear in Telegram")

    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    agent_keyword = settings.get("agent_keyword", "audio").lower()
    session_keyword = settings.get("session_keyword", "nová session").lower()
    cancel_keyword = settings.get("cancel_keyword", "zrušit").lower()
    test_keyword = settings.get("test_keyword", "testy").lower()
    group_folder = settings.get("group_folder", "telegram_main")
    mode_timeout_minutes = settings.get("mode_timeout_minutes", 10)
    voice_log = VoiceSessionLog(group_folder)

    # Voice mode: "schránka" (clipboard, default) or "audio" (agent)
    MODE_CLIPBOARD = "schránka"
    MODE_AUDIO = "audio"
    DEFAULT_MODE = MODE_CLIPBOARD
    current_mode = DEFAULT_MODE
    last_activity_time = time.time()

    porcupine = pvporcupine.create(
        access_key=access_key,
        keyword_paths=[KEYWORD_PATH],
        sensitivities=[0.6],
    )

    sample_rate = porcupine.sample_rate
    recording = False
    recorded_frames = []

    def get_audio_type():
        """Return 'interní' or 'externí' based on current input/output devices."""
        try:
            mic_result = subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-t", "input", "-c"],
                                 capture_output=True, text=True, timeout=5)
            spk_result = subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-t", "output", "-c"],
                                 capture_output=True, text=True, timeout=5)
            mic = mic_result.stdout.strip().lower()
            spk = spk_result.stdout.strip().lower()
            if not mic and not spk:
                print(f"  get_audio_type: empty (mic_rc={mic_result.returncode}, spk_rc={spk_result.returncode}, err={mic_result.stderr.strip()})")
                return ""
            is_builtin = "macbook" in mic and "macbook" in spk
            result = "interní" if is_builtin else "externí"
            print(f"  get_audio_type: {result} (mic={mic}, spk={spk})")
            return result
        except Exception as e:
            print(f"  get_audio_type exception: {e}")
            return ""

    def say_and_beep(word):
        """Say the mode name + audio type, wait for it to finish, then play the start beep."""
        audio_type = get_audio_type()
        announcement = f"{word}, {audio_type}" if audio_type else word
        print(f"  say: [{announcement}]")
        proc = subprocess.Popen(["say", "-v", "Zuzana", "-r", "220", announcement], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        play_sound(SOUND_START)

    MODE_LABELS = {
        MODE_AUDIO: "audio session",
        MODE_CLIPBOARD: "schránka",
    }

    def switch_mode(new_mode):
        nonlocal current_mode
        if current_mode != new_mode:
            current_mode = new_mode
            label = MODE_LABELS.get(new_mode, new_mode)
            print(f"  Mode switched to: {label}")
            subprocess.Popen(["say", "-v", "Zuzana", "-r", "220", f"Nastavena {label}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            label = MODE_LABELS.get(new_mode, new_mode)
            print(f"  Already in mode: {label}")
            subprocess.Popen(["say", "-v", "Zuzana", "-r", "220", f"Již {label}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Mode switch patterns — transcription contains just the mode switch command
    # Whisper can transcribe "audio session" in many ways: "audio session", "audio sešn", etc.
    mode_switch_patterns = {
        MODE_AUDIO: re.compile(r'^audio[\s-]*(se[sš]+[ieaí]?[oó]?n?|m[oó]d)$', re.IGNORECASE),
        MODE_CLIPBOARD: re.compile(r'^schr[áa]nka[\s-]*(m[oó]d)?$', re.IGNORECASE),
    }

    def create_recorder():
        rec = PvRecorder(
            frame_length=porcupine.frame_length,
            device_index=-1,
        )
        rec.start()
        return rec

    print(f"Voice daemon started. Say 'Hey Gimme' to start/stop recording.")
    print(f"Chat: {CHAT_JID}")
    print(f"Default mode: {DEFAULT_MODE}, timeout: {mode_timeout_minutes}min")
    print(f"Press Ctrl+C to quit.\n")

    recorder = create_recorder()

    try:
        while True:
            try:
                frame = recorder.read()
            except OSError:
                # Microphone disconnected — wait and try to reconnect
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Microphone lost. Waiting for reconnect...")
                recording = False
                recorded_frames = []
                try:
                    recorder.stop()
                    recorder.delete()
                except Exception:
                    pass
                while True:
                    time.sleep(5)
                    try:
                        recorder = create_recorder()
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Microphone reconnected.")
                        break
                    except Exception:
                        pass  # keep waiting
                continue

            keyword_index = porcupine.process(frame)

            if recording:
                recorded_frames.append(frame)

            # Auto-reset mode after inactivity
            if not recording and current_mode != DEFAULT_MODE:
                if time.time() - last_activity_time > mode_timeout_minutes * 60:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Mode timeout, resetting to {DEFAULT_MODE}")
                    current_mode = DEFAULT_MODE

            if keyword_index >= 0:
                # If audio is playing, interrupt it instead of starting recording
                if not recording and stop_audio_playback():
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Playback interrupted")
                    continue

                if not recording:
                    # Announce current mode, then beep — only start recording AFTER
                    say_and_beep(current_mode)
                    # Drain mic buffer for 1s to flush any speaker bleed
                    flush_end = time.time() + 1.0
                    while time.time() < flush_end:
                        try:
                            recorder.read()
                        except Exception:
                            break
                    recording = True
                    recorded_frames = []
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Recording started [{current_mode}]...")
                else:
                    # Stop recording
                    recording = False
                    play_sound(SOUND_STOP)
                    last_activity_time = time.time()
                    duration = len(recorded_frames) * porcupine.frame_length / sample_rate
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Recording stopped ({duration:.1f}s)")

                    if duration < 0.5:
                        print("  Too short, skipping.")
                        continue

                    # Transcribe with retry (30s timeout)
                    print("  Transcribing...")
                    wav_bytes = frames_to_wav(recorded_frames, sample_rate)
                    text = None
                    deadline = time.time() + 30
                    while time.time() < deadline:
                        try:
                            text = transcribe(wav_bytes, openai_key)
                            break
                        except Exception as e:
                            print(f"  Transcription failed: {e}")
                            if time.time() + 5 < deadline:
                                print("  Retrying in 5s...")
                                time.sleep(5)
                            else:
                                break
                    if not text:
                        play_sound(SOUND_CANCEL)
                        subprocess.Popen(["say", "-v", "Zuzana", "-r", "220", "Přepis nedostupný"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        print("  Transcription unavailable after retries.\n")
                        continue

                    if not text:
                        print("  Empty transcription, skipping.")
                        continue

                    print(f"  Raw transcription: {text}")

                    # Mode switch detection — check before full cleaning
                    # (clean_transcription strips leading mode words which would break this)
                    raw_stripped = strip_wake_words(text)
                    raw_stripped_lower = raw_stripped.lower().strip().strip(".,!?;:\"'")
                    mode_switched = False
                    for mode, pattern in mode_switch_patterns.items():
                        if pattern.match(raw_stripped_lower):
                            switch_mode(mode)
                            play_sound(SOUND_CLIPBOARD)
                            mode_switched = True
                            break
                    if mode_switched:
                        print()
                        continue

                    # Route based on keyword
                    cleaned = clean_transcription(text)
                    if not cleaned:
                        print("  Empty after cleaning, skipping.\n")
                        continue
                    cleaned_lower = cleaned.lower()
                    print(f"  Cleaned: {cleaned_lower}")

                    # Cancel detection — last word before wake word
                    if cleaned_lower.endswith(cancel_keyword):
                        play_sound(SOUND_CANCEL)
                        print(f"  Cancelled.\n")
                        continue

                    # Diagnostika
                    if cleaned_lower.startswith(test_keyword):
                        play_sound(SOUND_CLIPBOARD)
                        print(f"  Running diagnostics...\n")
                        run_diagnostics(CHAT_JID, bot_token)
                        continue

                    if cleaned_lower.startswith(session_keyword):
                        voice_log.new_session()
                        play_sound(SOUND_CLIPBOARD)
                        print(f"  New voice session started.\n")
                    elif current_mode == MODE_AUDIO:
                        # In audio mode, everything goes to the agent (no keyword needed)
                        voice_log.log("Pavel", cleaned)
                        inject_message(cleaned, CHAT_JID)
                        if bot_token:
                            send_telegram_transcript(cleaned, CHAT_JID, bot_token)
                        print(f"  Injected into NanoClaw. Waiting for response...\n")
                    else:
                        # Clipboard mode (default)
                        copy_to_clipboard(cleaned)
                        play_sound(SOUND_CLIPBOARD)
                        print(f"  Copied to clipboard: {cleaned}\n")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        try:
            recorder.stop()
            recorder.delete()
        except Exception:
            pass
        porcupine.delete()


if __name__ == "__main__":
    main()
