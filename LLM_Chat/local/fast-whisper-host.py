import base64
import time
import tempfile
import os
from flask import Flask, request, jsonify
from faster_whisper import WhisperModel
import argparse
import signal
import sys

try:
    import soundfile as sf
    import resampy
    RESAMPLE_AVAILABLE = True
except ImportError:
    RESAMPLE_AVAILABLE = False

# ---------- Configuration ----------
# Using "small" — better WER than "base" while processing time is acceptable on Pi5.
# small: ~490 MB RAM (int8), ~4x better WER than tiny, faster than base on Pi5
# due to lower memory bandwidth pressure from the smaller weight matrices.
MODEL_NAME = "small"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"    # Pi must use int8

# ---------- Initialization ----------
app = Flask(__name__)

t0 = time.perf_counter()
print("[INIT] Loading whisper model...")
model = WhisperModel(
    MODEL_NAME,
    device=DEVICE,
    cpu_threads=4,   # Fix 1: Pi5 has 4 cores; base model benefits from the extra thread
    compute_type=COMPUTE_TYPE,
)
t1 = time.perf_counter()
print(f"[INIT] Model loaded in {round(t1 - t0, 2)} seconds")


# ---------- Utility Functions ----------
def save_base64_to_temp_file(b64: str) -> str:
    """Save base64 audio to a temporary file and return its path."""
    try:
        audio_bytes = base64.b64decode(b64)
        fd, temp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(temp_path, "wb") as f:
            f.write(audio_bytes)
        return temp_path
    except Exception as e:
        raise ValueError(f"Failed to save base64 to temp file: {e}")


def resample_to_16k(path: str) -> str:
    """Fix 2: Resample audio to 16 kHz before passing to Whisper.

    Whisper's mel-spectrogram front-end is hardcoded to 16 kHz.
    The recorder captures at 48 kHz; explicit resampling here is higher
    quality than relying on faster-whisper's internal C++ resampler.

    Returns the original path unchanged if already 16 kHz or if
    soundfile/resampy are not available (falls back to internal resampler).
    """
    if not RESAMPLE_AVAILABLE:
        return path

    try:
        import soundfile as _sf
        import resampy as _resampy
        data, sr = _sf.read(path, dtype="float32")
        if sr == 16000:
            return path
        data_16k = _resampy.resample(data, sr, 16000)
        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        _sf.write(out_path, data_16k, 16000)
        return out_path
    except Exception as e:
        print(f"[WARN] Resampling failed, passing original to Whisper: {e}")
        return path


# ---------- API ----------
@app.route("/recognize", methods=["POST"])
def recognize():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    file_path = data.get("filePath")
    b64_audio = data.get("base64")
    language = data.get("language")

    if not file_path and not b64_audio:
        return jsonify({"error": "Either filePath or base64 must be provided"}), 400

    temp_file = None
    resampled_file = None
    try:
        t0 = time.perf_counter()

        # 1. Determine audio file path
        if file_path:
            audio_path = file_path
        else:
            temp_file = save_base64_to_temp_file(b64_audio)
            audio_path = temp_file

        # 2. Fix 2: Resample to 16 kHz explicitly
        resampled_file = resample_to_16k(audio_path)
        # If resampling produced a new file, use it; original temp_file is
        # still tracked separately for cleanup.
        transcribe_path = resampled_file if resampled_file != audio_path else audio_path

        # 3. Fix 5: vad_filter=False — the AudioRecorder already performs
        #    energy-based VAD. Running Whisper's Silero VAD on top strips
        #    quiet word boundaries a second time, causing dropped words.
        segments, info = model.transcribe(
            transcribe_path,
            language=language,
            vad_filter=False,
            beam_size=5,
        )

        text = "".join(seg.text for seg in segments).strip()

        t1 = time.perf_counter()

        return jsonify({
            "recognition": text,
            "language": info.language,
            "time_cost": round(t1 - t0, 3),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        # Clean up temporary files
        for path in (temp_file, resampled_file if resampled_file != (temp_file or "") else None):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


def shutdown(sig, frame):
    print("Shutting down python server...")
    sys.exit(0)


# ---------- Startup ----------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Faster Whisper API Server")
    parser.add_argument("--port", type=int, default=8803, help="Port to run the server on")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"[STARTING] Starting Faster Whisper server on port {args.port}...")

    app.run(
        host="0.0.0.0",
        port=args.port,
        threaded=False,  # Very important on Pi
    )
