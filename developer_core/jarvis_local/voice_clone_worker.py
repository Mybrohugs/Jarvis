"""Speak text in the user's own voice with XTTS-v2. Runs ONLY in the isolated clone venv.

Self-contained (no developer_core imports). Refuses to run unless prepared/consent.json
exists (written by voice_prep.py after hearing the consent sentence). Model weights stay
in <voice_dir>/models; no network after the first model download.
usage: python -I voice_clone_worker.py <voice_dir> <out.wav> "<text>"
       python -I voice_clone_worker.py <voice_dir> --batch <jobs.json>   ([{"text": ..., "out": ...}])
"""
import json
import os
from pathlib import Path
import sys


def main():
    voice_dir = Path(sys.argv[1])
    if sys.argv[2] == '--batch':
        jobs = json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
    else:
        jobs = [{'text': sys.argv[3], 'out': sys.argv[2]}]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 50:
        raise SystemExit('DENIED: 1..50 jobs')
    for job in jobs:
        if not isinstance(job.get('text'), str) or not 1 <= len(job['text']) <= 500 or Path(job['out']).exists():
            raise SystemExit('DENIED: bad text or output exists')
    prepared = voice_dir / 'prepared'
    consent = json.loads((prepared / 'consent.json').read_text(encoding='utf-8'))
    if consent.get('consent_ok') is not True or consent.get('model_license_accepted') != 'CPML (non-commercial)':
        raise SystemExit('DENIED: consent or model licence missing')
    refs = sorted(str(p) for p in prepared.glob('ref_*.wav'))
    if not refs:
        raise SystemExit('DENIED: no reference audio')
    os.environ.setdefault('TTS_HOME', str(voice_dir / 'models'))
    # torchcodec needs FFmpeg shared DLLs; Python on Windows ignores PATH for DLL dependencies.
    for d in os.environ.get('JARVIS_FFMPEG_BIN', '').split(';'):
        if d and os.path.isdir(d):
            os.add_dll_directory(d)
            os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
    import torch
    from TTS.api import TTS
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    tts = TTS('tts_models/multilingual/multi-dataset/xtts_v2').to(device)
    for job in jobs:
        extra = {}
        if isinstance(job.get('speed'), (int, float)) and 0.7 <= job['speed'] <= 1.3:
            extra['speed'] = float(job['speed'])
        if isinstance(job.get('temperature'), (int, float)) and 0.1 <= job['temperature'] <= 1.0:
            extra['temperature'] = float(job['temperature'])
        tts.tts_to_file(text=job['text'], speaker_wav=refs, language='ru', file_path=str(job['out']), **extra)
    print(json.dumps({'status': 'PASS', 'device': device, 'files': len(jobs), 'refs': len(refs)}))


if __name__ == '__main__':
    main()
