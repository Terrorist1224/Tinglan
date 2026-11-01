"""Create deterministic audio/video fixtures without a system speech voice."""
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import FFMPEG
from backend.media import run


def make_fixtures(root=ROOT):
    folder = Path(root) / ".tmp" / "samples"
    folder.mkdir(parents=True, exist_ok=True)
    sound = folder / "english.wav"
    rate = 16000
    with wave.open(str(sound), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        second = b"".join(struct.pack("<h", round(3000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate))
        for _ in range(17):
            output.writeframesraw(second)
    video = folder / "听澜样片.mp4"
    run([
        FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=0x173c46:s=960x540:r=24:d=17", "-i", str(sound),
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        "-movflags", "+faststart", str(video),
    ])
    return sound, video


if __name__ == "__main__":
    make_fixtures()
    print("Deterministic test media ready in .tmp/samples (no speech).")
