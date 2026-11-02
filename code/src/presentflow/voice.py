"""Provider-neutral voice selection, previews, and incremental narration."""
from pathlib import Path

from .ingest import inspect_deck, inspect_script
from .storage import absolute, digest, input_files, load_job, read_json, resolve_input, sha, write_json


def load_profile(file, directory):
    path, job = load_job(directory)
    value = read_json(file) if file else {}
    base = absolute(file).parent if file else path.parent
    for key in ('model', 'asr_model', 'reference_audio', 'room_audio', 'breath_audio', 'project_env'):
        if value.get(key):
            value[key] = str(absolute(base / value[key]))
    value.setdefault('project_root', str(path.parent))
    value.setdefault('language', 'English' if job.get('answers', {}).get('language', 'en') == 'en' else 'Chinese')
    value.setdefault('asr_language', 'en' if value['language'] == 'English' else 'zh' if value['language'] == 'Chinese' else None)
    value.setdefault('pronunciations', {})
    cloud_voice = path.parent / 'private' / 'cloud_voice.json'
    if not value.get('voice_id') and cloud_voice.exists():
        cloud = read_json(cloud_voice)
        if cloud.get('requires_verification'):
            raise ValueError('Complete the provider-required speaker verification before synthesis.')
        value['voice_id'] = cloud['voice_id']
    return value


def current_slides(directory, profile=None):
    path, job = load_job(directory)
    files = input_files(path, job)
    artifact = job.get('artifacts', {})
    deck_path = resolve_input(path, artifact['deck']) if artifact.get('deck') else files.get('deck')
    deck = inspect_deck(deck_path) if deck_path else None
    script_path = resolve_input(path, artifact['script']) if artifact.get('script') else files.get('script')
    prefer_notes = job.get('answers', {}).get('script_source') == 'deck_notes'
    if script_path and not prefer_notes:
        script = inspect_script(script_path)
        if not script['aligned'] or deck and len(script['slides']) != deck['slide_count']:
            raise ValueError('Align script to slide numbers before narration.')
        if deck and any(s['has_speech'] for s in deck['slides']) and not artifact.get('script'):
            if [' '.join(s['notes'].split()) for s in deck['slides']] != [' '.join(s['text'].split()) for s in script['slides']]:
                if job.get('answers', {}).get('script_source') != 'external_script':
                    raise ValueError('Select script_source before replacing different existing notes.')
        slides = [dict(slide=s['slide'], title=deck['slides'][i]['title'] if deck else f'Slide {s["slide"]}', text=s['text'])
                  for i, s in enumerate(script['slides'])]
    elif deck:
        if deck['notes_state'] != 'complete':
            raise ValueError('Speaker notes are missing or contain metadata. Complete the spoken script first.')
        slides = [dict(slide=s['slide'], title=s['title'], text=s['notes']) for s in deck['slides']]
    else:
        raise ValueError('Generate the deck/script first.')
    from .engine.ppt_voice import spoken
    for slide in slides:
        slide['spoken'] = spoken(slide['text'], profile or {})
    return slides, deck_path


def fingerprint(profile, provider):
    identity = {key: profile.get(key) for key in ('model', 'language', 'voice_id', 'cloud_model', 'voice_settings', 'reference_text')}
    identity['provider'] = provider
    if profile.get('reference_audio'):
        identity['reference_sha256'] = sha(profile['reference_audio'])
    if profile.get('model') and (Path(profile['model']) / 'config.json').is_file():
        identity['model_config_sha256'] = sha(Path(profile['model']) / 'config.json')
    return digest(identity)


def narrate(directory, profile_file=None, *, preview=False):
    path, job = load_job(directory)
    answers = job.get('answers', {})
    provider = answers.get('voice_provider')
    if provider not in ('qwen-local', 'qwen-local-1.7b', 'elevenlabs'):
        raise ValueError('Choose voice_provider explicitly from the recommendations first.')
    if answers.get('voice_mode') not in ('stock', 'clone'):
        raise ValueError('Choose stock or clone voice_mode before generated narration.')
    if answers['voice_mode'] == 'clone' and answers.get('voice_rights') not in ('own_voice', 'speaker_permission'):
        raise ValueError('Confirm the speaker permission for this voice.')
    profile = load_profile(profile_file, directory)
    slides, _ = current_slides(directory, profile)
    identity = fingerprint(profile, provider)
    if not preview and job.get('artifacts', {}).get('approved_voice') != identity:
        raise ValueError('Generate a preview and get the user approval before full narration.')
    work = path.parent / 'private' / ('preview' if preview else 'narration')
    work.mkdir(parents=True, exist_ok=True)
    if provider == 'elevenlabs':
        from .cloud import synthesize
        audio = synthesize(directory, slides, profile, path.parent / 'private' / 'cloud_audio', preview=preview)
    else:
        if answers.get('local_install') not in ('reuse_existing', 'allow_setup'):
            raise ValueError('Confirm reuse/setup of the selected local model before running it.')
        if answers['voice_mode'] != 'clone':
            raise ValueError('This local Qwen Base adapter requires a reference voice; use clone mode or another provider.')
        for key in ('model', 'asr_model', 'reference_audio', 'reference_text'):
            if not profile.get(key):
                raise ValueError(f'Configure local voice profile field: {key}')
        if profile['language'] != 'English' and Path(profile['asr_model']).name.endswith('.en'):
            raise ValueError('Choose a multilingual ASR model for non-English speech.')
        requested = slides
        if preview:
            from .cloud import preview_text
            requested = [dict(slides[0], text=preview_text(slides[0]['spoken']))]
        from .engine.clone_voice import narrate as local_narrate
        audio = local_narrate(requested, profile, work)
    manifest = work / 'audio_manifest.json'
    write_json(manifest, audio)
    if preview:
        result = dict(voice_fingerprint=identity, file=audio[0]['file'], sha256=audio[0]['sha256'])
        write_json(path.parent / 'private' / 'preview_receipt.json', result)
    else:
        job.setdefault('artifacts', {})['audio_manifest'] = str(manifest)
        write_json(path, job)
        result = dict(audio_manifest=str(manifest), slides=len(audio))
    return result


def approve_preview(directory, audio_sha256):
    path, job = load_job(directory)
    receipt = read_json(path.parent / 'private' / 'preview_receipt.json')
    if receipt['sha256'] != audio_sha256 or sha(receipt['file']) != audio_sha256:
        raise ValueError('Preview hash differs; approve the preview that the user actually heard.')
    job.setdefault('artifacts', {})['approved_voice'] = receipt['voice_fingerprint']
    write_json(path, job)
    return dict(approved=True)


def assemble(directory, output, profile_file=None, manifest_file=None, *, air=False, min_pause=1, max_pause=3):
    import argparse
    path, job = load_job(directory)
    profile = load_profile(profile_file, directory)
    slides, deck = current_slides(directory, profile)
    if not deck:
        raise ValueError('Compose or import a finished PPTX before assembly.')
    manifest_file = manifest_file or job.get('artifacts', {}).get('audio_manifest')
    if not manifest_file:
        raise ValueError('Provide a complete per-slide audio manifest.')
    manifest_file = absolute(manifest_file)
    work = path.parent / 'private' / 'assembly'
    work.mkdir(parents=True, exist_ok=True)
    from .ingest import markdown_script
    script = work / 'spoken.md'
    script.write_text(markdown_script(slides), encoding='utf-8')
    engine_profile = work / 'profile.local.json'
    write_json(engine_profile, profile)
    from .engine.ppt_voice import run
    run(argparse.Namespace(source=str(deck), work=str(work), output=str(absolute(output)), script=str(script),
        profile=str(engine_profile), audio_manifest=str(manifest_file), stop_after_audio=False,
        no_air=not air, min_pause=min_pause, max_pause=max_pause, seed=None))
    job.setdefault('artifacts', {})['autoplay_pptx'] = str(absolute(output))
    write_json(path, job)
    return dict(output=str(absolute(output)), slides=len(slides))
