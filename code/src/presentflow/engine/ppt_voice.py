"""Local, resumable PPT narration. No network calls; no python-pptx dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
ROOT = Path(__file__).resolve().parent


def absolute(path):
    # Normalize '..' before resolving Windows reparse points / sandbox path mappings.
    return Path(os.path.abspath(path)).resolve()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def command(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)


def get_profile(path=None):
    
    if not path:
        raise ValueError('Supply an explicit local profile; no personal machine defaults are bundled.')
    profile = load(path)
    base = absolute(path).parent if path else Path.cwd()
    for key in ('project_root', 'project_env', 'model', 'asr_model', 'reference_audio',
                'room_audio', 'breath_audio', 'seed_manifest'):
        if profile.get(key):
            profile[key] = str(absolute(base / profile[key]))
    return profile


def relationship_target(package, part, suffix):
    rel_part = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if rel_part not in package.namelist():
        return None
    for rel in ET.fromstring(package.read(rel_part)):
        if rel.attrib['Type'].endswith('/' + suffix):
            return posixpath.normpath(posixpath.join(posixpath.dirname(part), rel.attrib['Target']))
    return None


def read_deck(path):
    with ZipFile(path) as z:
        if z.testzip():
            raise ValueError('Corrupted PPTX')
        pres = ET.fromstring(z.read('ppt/presentation.xml'))
        rels = {r.attrib['Id']: r.attrib['Target']
                for r in ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))}
        result = []
        for index, node in enumerate(pres.findall('p:sldIdLst/p:sldId', NS), 1):
            target = rels[node.attrib['{' + NS['r'] + '}id']]
            part = posixpath.normpath('ppt/' + target) if not target.startswith('/') else target.lstrip('/')
            slide = ET.fromstring(z.read(part))
            title = ''
            for shape in slide.findall('.//p:sp', NS):
                ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
                if ph is not None and ph.attrib.get('type') in ('title', 'ctrTitle'):
                    title = ' '.join(t.text or '' for t in shape.findall('.//a:t', NS))
                    break
            note_part = relationship_target(z, part, 'notesSlide')
            text = ''
            if note_part:
                notes = ET.fromstring(z.read(note_part))
                for shape in notes.findall('.//p:sp', NS):
                    ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
                    if ph is not None and ph.attrib.get('type') == 'body':
                        text = '\n'.join(''.join(t.text or '' for t in p.findall('.//a:t', NS))
                                         for p in shape.findall('.//a:p', NS)).strip()
                        break
            result.append(dict(slide=index, title=title or f'Slide {index}', text=text))
        return result


def read_script(path, slides):
    path = Path(path)
    if path.suffix.lower() == '.docx':
        with ZipFile(path) as z:
            root = ET.fromstring(z.read('word/document.xml'))
            wn = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', wn))
                             for p in root.findall('.//w:body/w:p', wn))
    elif path.suffix.lower() in ('.md', '.txt'):
        text = path.read_text(encoding='utf-8-sig')
    else:
        raise ValueError('Speech must be .md, .txt or .docx with Slide headings.')
    pattern = re.compile(r'(?im)^\s*(?:#{1,6}\s*)?Slide\s+(\d+)\s*(?::[^\n]*)?\s*$')
    matches = list(pattern.finditer(text))
    if [int(m[1]) for m in matches] != list(range(1, len(slides) + 1)):
        raise ValueError('Expected exactly one ordered Slide 01..N heading per PPT slide. Split the script first.')
    result = []
    for i, m in enumerate(matches):
        body = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
        if not body or re.search(r'(?im)^\s*(?:Source:|来源[:：]|```|#{1,6}\s)', body):
            raise ValueError(f'Slide {i + 1} has empty speech, a source label, or unprocessed markup.')
        result.append(dict(slides[i], text=body))
    return result


def spoken(text, profile):
    for source, target in sorted(profile.get('pronunciations', {}).items(), key=lambda x: -len(x[0])):
        text = re.sub(r'(?<!\w)' + re.escape(source) + r'(?!\w)', lambda _: target, text)
    return ' '.join(text.split())


def reuse_key(text):
    # Exact lexical equivalence after the configured pronunciation map, not fuzzy ASR matching.
    return re.sub(r'\s+', ' ', text).strip().casefold()


def load_audio_manifest(path, slides, profile):
    path = absolute(path)
    values = load(path)
    if [x['slide'] for x in values] != list(range(1, len(slides) + 1)):
        raise ValueError('Audio manifest slide numbers do not match deck.')
    result = []
    for item, slide in zip(values, slides):
        file = absolute(path.parent / item['file'])
        if file.suffix.lower() != '.mp3' or sha(file) != item.get('sha256'):
            raise ValueError(f'Audio missing, wrong format or hash: slide {slide["slide"]}')
        if reuse_key(spoken(item['spoken'], profile)) != reuse_key(spoken(slide['text'], profile)):
            raise ValueError(f'Old audio text differs from current script: slide {slide["slide"]}')
        result.append(dict(item, file=str(file)))
    return result


def powershell(plan, phase, profile):
    def quote(s):
        return "'" + str(s).replace("'", "''") + "'"
    if os.name != "nt":
        raise ValueError("Native PPTX assembly requires Windows desktop PowerPoint.")
    invocation = "$ErrorActionPreference=\'Stop\'\n"
    if profile.get("project_env"):
        invocation += f". {quote(profile['project_env'])}\n"
    invocation += f"& {quote(ROOT / 'build_deck.ps1')} -PlanPath {quote(plan)} -Phase {quote(phase)}"
    command([shutil.which('pwsh') or 'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', invocation])


def validate(source, candidate, plan, work):
    from PIL import Image, ImageChops
    if sha(source) != plan['source_sha256']:
        raise ValueError('Source changed during processing.')
    with ZipFile(candidate) as z:
        if z.testzip() or any('vbaproject' in name.casefold() for name in z.namelist()):
            raise ValueError('Corrupt or macro-bearing output.')
        if b'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml' not in z.read('[Content_Types].xml'):
            raise ValueError('Output is not a normal PPTX.')
        hashes = [hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.lower().endswith('.mp3')]
        expected = [s['audio_sha256'] for s in plan['slides']]
        if plan.get('air_file'):
            expected.append(sha(plan['air_file']))
        from collections import Counter
        if Counter(hashes) != Counter(expected):
            raise ValueError('Unexpected audio: missing, duplicate, or pre-existing unmanaged narration.')
        for name in z.namelist():
            if name.endswith('.rels'):
                for rel in ET.fromstring(z.read(name)):
                    if rel.attrib['Type'].endswith(('/audio', '/media')) and rel.attrib.get('TargetMode') == 'External':
                        raise ValueError('Linked audio is not portable.')
    final_notes = read_deck(candidate)
    if len(final_notes) != len(plan['slides']):
        raise ValueError('Slide count changed.')
    for wanted, actual in zip(plan['slides'], final_notes):
        if reuse_key(wanted['text']) != reuse_key(actual['text']):
            raise ValueError(f'Wrong speaker notes: slide {wanted["slide"]}')
        name = f'slide_{wanted["slide"]:02}.png'
        before = Image.open(work / 'render_source' / name).convert('RGB')
        after = Image.open(work / 'render_final' / name).convert('RGB')
        if before.size != after.size or ImageChops.difference(before, after).getbbox():
            raise ValueError(f'Visible layout changed: slide {wanted["slide"]}')
    return dict(macro_free=True, all_audio_embedded=True, notes_match=True,
                all_slides_pixel_identical=True, slides=len(plan['slides']),
                show_seconds=plan['show_seconds'], sha256=sha(candidate))


def run(args):
    profile = get_profile(args.profile)
    source, work, output = absolute(args.source), absolute(args.work), absolute(args.output)
    if source.suffix.lower() != '.pptx' or output.suffix.lower() != '.pptx':
        raise ValueError('Source and output must be .pptx.')
    if output == source or output.exists():
        raise ValueError('Use a new output filename; existing files are preserved.')
    if args.min_pause < 0 or args.max_pause < args.min_pause:
        raise ValueError('Invalid pause range.')
    work.mkdir(parents=True, exist_ok=True)
    slides = read_deck(source)
    if args.script:
        slides = read_script(args.script, slides)
    if not slides or any(not x['text'].strip() for x in slides):
        raise ValueError('Every slide needs a spoken script.')
    if any(re.search(r'(?im)^\s*(?:Source:|来源[:：])', x['text']) for x in slides):
        raise ValueError('Remove source labels from spoken notes before narration.')
    if args.audio_manifest:
        audio = load_audio_manifest(args.audio_manifest, slides, profile)
    else:
        from .clone_voice import narrate
        audio = narrate(slides, profile, work)
    save(work / 'audio_manifest.json', audio)
    if args.stop_after_audio:
        print(f'Audio ready: {work / "audio_manifest.json"}', flush=True)
        return
    # Preserve a task's random pause selections across edits; changing the range creates new draws.
    selections_file = work / 'pause_selections.json'
    selections = load(selections_file) if selections_file.exists() else {}
    bounds = [args.min_pause, args.max_pause]
    rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
    if selections.get('bounds') != bounds:
        selections = dict(bounds=bounds, values=[])
    while len(selections['values']) < len(slides):
        selections['values'].append(round(rng.uniform(*bounds), 2))
    save(selections_file, selections)
    items = [dict(s, audio_file=a['file'], audio_sha256=a['sha256'],
                  pause_seconds=selections['values'][i], narration_delay=.35)
             for i, (s, a) in enumerate(zip(slides, audio))]
    plan = dict(source=str(source), source_sha256=sha(source), work=str(work), slides=items,
                profile=profile, candidate=str(work / 'candidate.pptx'))
    save(work / 'input_plan.json', plan)
    print('Measuring audio and preparing slides in native PowerPoint...', flush=True)
    powershell(work / 'input_plan.json', 'prepare', profile)
    plan = load(work / 'measured_plan.json')
    if not args.no_air:
        from .air_track import make_air
        plan['air_file'] = str(make_air(plan, profile, work))
    save(work / 'measured_plan.json', plan)
    print('Embedding audio and checking all slide renders...', flush=True)
    powershell(work / 'measured_plan.json', 'finish', profile)
    native = load(work / 'native_result.json')
    for expected, actual in zip(plan['slides'], native['slides']):
        if expected['slide'] != actual['slide'] or abs(expected['slide_seconds'] - actual['slide_seconds']) > .02:
            raise ValueError('Native slide timing does not match audio plan.')
    receipt = validate(source, Path(plan['candidate']), plan, work)
    output.parent.mkdir(parents=True, exist_ok=True)
    with Path(plan['candidate']).open('rb') as src, output.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    if sha(output) != receipt['sha256']:
        raise ValueError('Delivery hash differs.')
    save(work / 'validation.json', dict(receipt, output=str(output), native=native))
    print(f'COMPLETE: {output}\n{len(slides)} slides, {plan["show_seconds"]:.1f} seconds.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    doctor = sub.add_parser('doctor', help='Check local dependencies; optionally export a private profile.')
    doctor.add_argument('--profile')
    doctor.add_argument('--write-profile')
    extract = sub.add_parser('extract', help='Export spoken notes in slide order to Markdown.')
    extract.add_argument('--source', required=True)
    extract.add_argument('--output', required=True)
    job = sub.add_parser('run', help='Generate changed narration, embed it, and validate PPTX.')
    for key in ('source', 'work', 'output'):
        job.add_argument('--' + key, required=True)
    for key in ('script', 'profile', 'audio-manifest'):
        job.add_argument('--' + key)
    job.add_argument('--min-pause', type=float, default=1)
    job.add_argument('--max-pause', type=float, default=3)
    job.add_argument('--seed', type=int)
    job.add_argument('--no-air', action='store_true')
    job.add_argument('--stop-after-audio', action='store_true')
    args = parser.parse_args()
    if args.action == 'doctor':
        p = get_profile(args.profile)
        if args.write_profile:
            target = Path(args.write_profile)
            if target.exists():
                raise ValueError('Private profile already exists; choose a new filename.')
            save(target, p)
        import importlib.util
        checks = {key: Path(p[key]).exists() for key in
                  ('project_env', 'model', 'asr_model', 'reference_audio', 'room_audio', 'breath_audio')}
        checks.update({key: bool(shutil.which(key)) for key in ('ffmpeg', 'ffprobe', 'powershell.exe')})
        checks.update({key: importlib.util.find_spec(key) is not None
                       for key in ('torch', 'qwen_tts', 'faster_whisper', 'soundfile', 'scipy', 'PIL')})
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        if not all(checks.values()):
            raise SystemExit(2)
    elif args.action == 'extract':
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = '# Speaker Script\n\n' + '\n\n'.join(
            f'## Slide {s["slide"]:02}: {s["title"]}\n\n{s["text"]}' for s in read_deck(args.source)) + '\n'
        with target.open('x', encoding='utf-8') as f:
            f.write(text)
        print(target.resolve())
    else:
        run(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
