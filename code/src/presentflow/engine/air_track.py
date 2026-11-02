"""Rebuild the approved room-tone/inhale layer against native slide timings."""
from pathlib import Path

from .ppt_voice import command, save


def make_air(plan, profile, work):
    import numpy as np
    import soundfile as sf
    from scipy.signal import butter, resample, sosfilt
    sr = 24000
    for key in ('room_audio', 'room_ranges', 'breath_audio', 'breath_ranges'):
        if not profile.get(key):
            raise ValueError(f'No {key} configured; use --no-air or provide local licensed samples.')

    def read(path):
        result = command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                          '-i', path, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'], capture_output=True)
        return np.frombuffer(result.stdout, dtype='<f4').copy()

    def scale(wave, level):
        rms = np.sqrt(np.mean(wave.astype(np.float64) ** 2))
        if rms < 1e-9:
            raise ValueError('Ambient sample is silent.')
        return wave * (10 ** (level / 20) / rms)

    def taper(wave, seconds=.035):
        wave = np.array(wave, dtype=np.float32, copy=True)
        n = min(round(seconds * sr), len(wave) // 3)
        ramp = np.sin(np.linspace(0, np.pi / 2, n)) ** 2
        if n:
            wave[:n] *= ramp
            wave[-n:] *= ramp[::-1]
        return wave

    def slices(path, ranges):
        wave = read(path)
        if not ranges or any(a < 0 or b <= a or b > len(wave) / sr + .01 for a, b in ranges):
            raise ValueError('Invalid local room-tone or breath selection.')
        return [wave[round(a * sr):round(b * sr)] for a, b in ranges]

    count = round((plan['show_seconds'] + .3) * sr)
    rng = np.random.default_rng(1083)
    room = slices(profile['room_audio'], profile['room_ranges'])
    bed = np.zeros(count, dtype=np.float32)
    at = 0
    while at < count:
        sample = room[int(rng.integers(len(room)))]
        sample = resample(sample, max(16, round(len(sample) * rng.uniform(.92, 1.09)))).astype(np.float32)
        sample = taper(scale(sample, -51 + float(rng.uniform(-.5, .5))), .08)
        end = min(count, at + len(sample))
        bed[at:end] += sample[:end - at]
        at += max(1, len(sample) - round(.08 * sr))
    bed = sosfilt(butter(2, [110, 6200], btype='bandpass', fs=sr, output='sos'), bed).astype(np.float32)
    bed = scale(bed, -51)
    breaths = [sosfilt(butter(2, [150, 6500], btype='bandpass', fs=sr, output='sos'), x).astype(np.float32)
               for x in slices(profile['breath_audio'], profile['breath_ranges'])]
    events = []

    def place(start, sample, level, slide, kind):
        sample = scale(taper(sample), level)
        i = max(0, round(start * sr))
        end = min(count, i + len(sample))
        bed[i:end] += sample[:end - i]
        events.append(dict(slide=slide, kind=kind, start=i / sr, seconds=(end - i) / sr, level_dbfs=level))

    for i, slide in enumerate(plan['slides']):
        if i == 0:
            sample = resample(breaths[min(1, len(breaths) - 1)], round(.28 * sr)).astype(np.float32)
            place(.025, sample, -35.5, 1, 'opening')
        else:
            sample = breaths[(i - 1) % len(breaths)]
            sample = resample(sample, round(len(sample) * rng.uniform(.94, 1.04))).astype(np.float32)
            place(slide['start'] + .27 - len(sample) / sr, sample,
                  -33.5 + float(rng.uniform(-1.2, 1.2)), slide['slide'], 'page_inhale')
        voice = read(slide['audio_file'])
        frame = round(.01 * sr)
        energy = np.sqrt(np.mean(voice[:len(voice) // frame * frame].reshape(-1, frame) ** 2, axis=1))
        changes = np.diff(np.r_[False, energy < 10 ** (-44 / 20), False].astype(int))
        starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
        gaps = [(a * .01, b * .01) for a, b in zip(starts, ends)
                if b - a >= 38 and a * .01 > 7 and b * .01 < len(voice) / sr - 6]
        if gaps and len(voice) / sr > 30:
            a, b = min(gaps, key=lambda x: abs(sum(x) / 2 - len(voice) / sr / 2))
            length = min(.34, b - a - .08)
            sample = resample(breaths[min(1, len(breaths) - 1)], round(length * sr)).astype(np.float32)
            place(slide['start'] + .35 + (a + b) / 2 - length / 2, sample,
                  -37 + float(rng.uniform(-.6, .6)), slide['slide'], 'phrase_inhale')
    end = round(plan['show_seconds'] * sr)
    fade = min(round(.7 * sr), end)
    bed[end - fade:end] *= np.linspace(1, 0, fade)
    bed[end:] = 0
    fade_in = min(round(.12 * sr), len(bed))
    bed[:fade_in] *= np.linspace(0, 1, fade_in)
    if float(np.max(np.abs(bed))) >= .9:
        raise ValueError('Ambient level unexpectedly high.')
    wav, mp3 = work / 'air.wav', work / 'air.mp3'
    sf.write(wav, bed, sr, subtype='PCM_16')
    command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
             '-i', wav, '-c:a', 'libmp3lame', '-b:a', '64k', mp3])
    save(work / 'air_events.json', dict(room_rms_dbfs=-51, events=events, show_seconds=plan['show_seconds']))
    return mp3
