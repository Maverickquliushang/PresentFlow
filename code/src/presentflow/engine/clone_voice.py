"""Content-addressed local Qwen cloning with bounded ASR retries."""
import difflib
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .ppt_voice import command, load, save, sha, spoken

SETTINGS = dict(temperature=.65, top_p=.9, top_k=30,
                subtalker_temperature=.65, subtalker_top_p=.9, subtalker_top_k=30)
ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
TENS = 'zero ten twenty thirty forty fifty sixty seventy eighty ninety'.split()


def number_words(value):
    n = int(value)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else '')
    return ' '.join(ONES[int(d)] for d in str(n))


def tokens(text):
    text = text.casefold()
    text = re.sub(r'\b(\d+)\s*b\b', lambda m: number_words(m[1]) + ' billion', text)
    text = re.sub(r'\d+(?:\.\d+)?', lambda m: number_words(m[0].split('.')[0]) +
                  (' point ' + ' '.join(ONES[int(d)] for d in m[0].split('.')[1]) if '.' in m[0] else ''), text)
    return re.findall(r'[^\W_]+', text, flags=re.UNICODE)


def text_matches(wanted, got):
    wanted, got = tokens(wanted), tokens(got)
    ratio = difflib.SequenceMatcher(None, ''.join(wanted), ''.join(got)).ratio()
    # Do not silently accept a sentence missing its beginning/end or a changed figure.
    n = min(3, len(wanted))
    head = difflib.SequenceMatcher(None, ' '.join(wanted[:n]), ' '.join(got[:n + 1])).ratio()
    tail = difflib.SequenceMatcher(None, ' '.join(wanted[-n:]), ' '.join(got[-n - 1:])).ratio()
    number_tokens = set(ONES + TENS + ['point', 'hundred', 'thousand', 'million', 'billion'])
    figures = [t for t in wanted if t in number_tokens]
    recognized = [t for t in got if t in number_tokens]
    numbers_ok = not figures or figures == recognized
    return ratio >= .86 and min(head, tail) >= .62 and numbers_ok, dict(
        similarity=ratio, head_similarity=head, tail_similarity=tail, numbers_ok=numbers_ok)


def speech_units(text):
    return max(len(text.split()), round(len(re.findall(r'[\u3400-\u9fff]', text)) * .65))


def split_text(text, maximum=48):
    sentences = re.split(r'(?<=[。！？])|(?<=[.!?])\s+', text)
    chunks, current = [], ''
    for sentence in sentences:
        if current and speech_units(current + ' ' + sentence) > maximum:
            chunks.append(current)
            current = ''
        current = (current + ' ' + sentence).strip()
    if current:
        chunks.append(current)
    return chunks


def signature(text, identity):
    value = json.dumps(dict(version=1, text=text, identity=identity, settings=SETTINGS), sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def narrate(slides, profile, work):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['NUMBA_CACHE_DIR'] = str(Path(profile['project_root']) / '.cache/numba')
    cache = work / 'audio'
    cache.mkdir(exist_ok=True)
    model_files = sorted(Path(profile['model']).rglob('*.safetensors'))
    if not model_files:
        raise ValueError('Local Qwen weights not found.')
    identity = dict(reference_audio=sha(profile['reference_audio']),
                    reference_text=profile['reference_text'], language=profile.get('language', 'English'),
                    model_weights=[sha(p) for p in model_files],
                    model_config=sha(Path(profile['model']) / 'config.json'),
                    vocabulary=profile.get('vocabulary', ''), asr_model=profile['asr_model'])
    # Import heavy libraries only if a changed chunk actually requires synthesis.
    engine = {}

    def initialize():
        if engine:
            return
        import numpy as np
        import soundfile as sf
        import torch
        from faster_whisper import WhisperModel
        from qwen_tts import Qwen3TTSModel
        from scipy.signal import resample_poly
        if not torch.cuda.is_available():
            raise ValueError('This validated preset needs an NVIDIA CUDA GPU; configure another engine explicitly.')
        torch.set_num_threads(4)
        model = Qwen3TTSModel.from_pretrained(profile['model'], device_map='cuda:0',
            dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True)
        prompt = model.create_voice_clone_prompt(ref_audio=profile['reference_audio'],
            ref_text=profile['reference_text'], x_vector_only_mode=False)
        asr = WhisperModel(profile['asr_model'], device='cpu', compute_type='int8', cpu_threads=4)
        engine.update(np=np, sf=sf, torch=torch, model=model, prompt=prompt, asr=asr, resample=resample_poly)

    def synthesize(text, depth=0):
        sig = signature(text, identity)
        output, metadata = cache / (sig + '.wav'), cache / (sig + '.json')
        if output.exists() and metadata.exists():
            record = load(metadata)
            if record.get('accepted') and record.get('signature') == sig and sha(output) == record.get('sha256'):
                return output
        initialize()
        np, sf, torch = engine['np'], engine['sf'], engine['torch']
        attempts = []
        for attempt in range(3):
            torch.manual_seed(int(sig[:8], 16) + attempt)
            with torch.inference_mode():
                waves, sr = engine['model'].generate_voice_clone(
                    text=text, language=identity['language'], voice_clone_prompt=engine['prompt'],
                    non_streaming_mode=True, max_new_tokens=max(360, speech_units(text) * 16), **SETTINGS)
            wave = np.asarray(waves[0], dtype=np.float32)
            if not np.isfinite(wave).all() or not len(wave):
                continue
            wave *= min(1, .98 / max(float(np.max(np.abs(wave))), 1e-9))
            segments, _ = engine['asr'].transcribe(engine['resample'](wave, 16000, sr),
                language=profile.get('asr_language', 'en'), beam_size=5,
                condition_on_previous_text=False, initial_prompt=profile.get('vocabulary', ''))
            transcript = ' '.join(segment.text.strip() for segment in segments)
            accepted, check = text_matches(text, transcript)
            seconds = len(wave) / sr
            accepted = accepted and .5 < seconds < max(30, speech_units(text) * 1.3)
            attempts.append(dict(transcript=transcript, seconds=seconds, accepted=accepted, **check))
            print(f'  chunk {sig[:8]} attempt {attempt + 1}: {seconds:.1f}s, match {check["similarity"]:.3f}', flush=True)
            if accepted:
                sf.write(output, wave, sr, subtype='PCM_16')
                save(metadata, dict(signature=sig, accepted=True, text=text, sha256=sha(output), attempts=attempts))
                return output
        parts = split_text(text, 1)
        if len(parts) > 1 and depth == 0:
            files = [synthesize(part, depth + 1) for part in parts]
            arrays, rate = [], None
            for file in files:
                wave, sr = sf.read(file, dtype='float32')
                if rate is not None and sr != rate:
                    raise ValueError('Inconsistent synthesis sample rates.')
                if arrays:
                    arrays.append(np.zeros(round(.22 * sr), dtype=np.float32))
                arrays.append(wave)
                rate = sr
            sf.write(output, np.concatenate(arrays), rate, subtype='PCM_16')
            save(metadata, dict(signature=sig, accepted=True, text=text, sha256=sha(output), sentence_fallback=True))
            return output
        save(metadata, dict(signature=sig, accepted=False, text=text, attempts=attempts))
        raise ValueError(f'Cannot verify speech: {text[:100]}. Review technical spelling or listen before accepting.')

    records = []
    for slide in slides:
        text = spoken(slide['text'], profile)
        sig = signature(text, identity)
        output = cache / (sig + '.mp3')
        metadata = cache / (sig + '.mp3.json')
        if output.exists() and metadata.exists() and load(metadata).get('sha256') == sha(output):
            print(f'Slide {slide["slide"]:02}: cached audio.', flush=True)
        else:
            print(f'Slide {slide["slide"]:02}: generating changed narration.', flush=True)
            files = [synthesize(chunk) for chunk in split_text(text)]
            # Concatenation is handled by ffmpeg; generated paths contain only hexadecimal names.
            listing = cache / (sig + '.concat.txt')
            listing.write_text('\n'.join(f"file '{file.name}'" for file in files), encoding='utf-8')
            try:
                command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'concat', '-safe', '1', '-i', listing.as_posix(), '-af', 'loudnorm=I=-18:TP=-1.5:LRA=11',
                    '-ar', '24000', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '128k', output], capture_output=True)
            except subprocess.CalledProcessError as exc:
                raise ValueError('ffmpeg encoding failed: ' + exc.stderr.decode('utf-8', errors='replace')) from exc
            save(metadata, dict(signature=sig, sha256=sha(output), spoken=text))
        records.append(dict(slide=slide['slide'], spoken=text, file=str(output), sha256=sha(output), signature=sig))
        save(work / 'progress.json', records)
    return records
