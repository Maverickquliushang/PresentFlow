"""PresentFlow CLI: intake -> plan -> agent authoring -> optional narration."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .storage import absolute, input_files, load_job, read_json, set_answers, write_json


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def answer_values(items):
    result = {}
    allowed = {
        'voice_mode': {'none', 'stock', 'clone', 'import'},
        'privacy': {'local_only', 'ask_per_provider'},
        'voice_provider': {'qwen-local', 'qwen-local-1.7b', 'elevenlabs', 'import'},
        'script_source': {'external_script', 'deck_notes', 'merge_review'},
        'deck_action': {'preserve', 'restyle'},
        'voice_rights': {'own_voice', 'speaker_permission'},
        'local_install': {'reuse_existing', 'allow_setup', 'choose_another'},
        'branding': {'provided_assets', 'official_lookup', 'text_only'},
        'template_preference': {'provide_template', 'builtin_academic'},
    }
    for item in items:
        key, sep, value = item.partition('=')
        if not sep or not key:
            raise ValueError('Answers use key=value.')
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        if key in allowed and value not in allowed[key]:
            raise ValueError(f'Invalid {key}; choose from {sorted(allowed[key])}.')
        if key == 'duration_minutes' and value == 'follow_template':
            result['duration_basis'] = 'template'
            continue
        if key in ('duration_minutes', 'cloud_budget_chars'):
            value = float(value) if key == 'duration_minutes' else int(value)
            if value <= 0:
                raise ValueError(f'{key} must be positive.')
        result[key] = value
    return result


def build_parser():
    parser = argparse.ArgumentParser(prog='presentflow', description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Create a private job from available materials.')
    init.add_argument('job')
    for role in ('paper', 'template', 'deck', 'script', 'recording'):
        init.add_argument('--' + role)
    init.add_argument('--set', action='append', default=[])
    plan = sub.add_parser('plan', help='Inspect inputs; return current questions and a production plan.')
    plan.add_argument('job')
    plan.add_argument('--hardware', help='Optional hardware JSON instead of local detection.')
    plan.add_argument('--output')
    answers = sub.add_parser('answer', help='Record answers already provided by the user.')
    answers.add_argument('job')
    answers.add_argument('--set', action='append', required=True)
    attach = sub.add_parser('attach', help='Add/replace an input reference without modifying its file.')
    attach.add_argument('job')
    for role in ('paper', 'template', 'deck', 'script', 'recording'):
        attach.add_argument('--' + role)
    extract = sub.add_parser('extract', help='Write extracted source text and slide inventories into the job.')
    extract.add_argument('job')
    compose = sub.add_parser('compose', help='Render an agent-authored talk spec; preserve finished decks by default.')
    compose.add_argument('job')
    compose.add_argument('--spec', required=True)
    compose.add_argument('--output', required=True)
    doctor = sub.add_parser('doctor', help='Detect hardware and suggest options without installing anything.')
    doctor.add_argument('--privacy', choices=['local_only', 'ask_per_provider'], default='ask_per_provider')
    doctor.add_argument('--voice-mode', choices=['none', 'stock', 'clone', 'import'], default='clone')
    for name in ('preview', 'narrate'):
        cmd = sub.add_parser(name, help='Generate a short voice sample.' if name == 'preview' else 'Generate changed narration after preview approval.')
        cmd.add_argument('job')
        cmd.add_argument('--profile', required=True)
    approve = sub.add_parser('approve-preview', help='Record explicit approval of the exact preview heard by the user.')
    approve.add_argument('job')
    approve.add_argument('--sha256', required=True)
    for name in ('consent-summary', 'grant-consent'):
        cmd = sub.add_parser(name, help='Show outgoing-data scope.' if name == 'consent-summary' else 'Record approval after the user authorizes the displayed scope.')
        cmd.add_argument('job')
        cmd.add_argument('--purpose', choices=['upload_recording', 'synthesize_speech'], required=True)
        cmd.add_argument('--profile')
        cmd.add_argument('--voice-name', default='My voice')
        cmd.add_argument('--preview', action='store_true')
        if name == 'grant-consent':
            cmd.add_argument('--approve', action='store_true', required=True)
    clone = sub.add_parser('create-voice', help='Create a cloud clone only after recording-upload approval.')
    clone.add_argument('job')
    clone.add_argument('--name', default='My voice')
    assemble = sub.add_parser('assemble', help='Embed per-slide audio and authored timings into a new PPTX.')
    assemble.add_argument('job')
    assemble.add_argument('--output', required=True)
    assemble.add_argument('--audio-manifest')
    assemble.add_argument('--profile')
    assemble.add_argument('--air', action='store_true', help='Opt in to separately configured room tone/breath samples.')
    assemble.add_argument('--min-pause', type=float, default=1)
    assemble.add_argument('--max-pause', type=float, default=3)
    return parser


def outgoing(args):
    from .voice import current_slides, load_profile
    from .cloud import recording_payload, speech_payload
    path, job = load_job(args.job)
    if args.purpose == 'upload_recording':
        recording = input_files(path, job).get('recording')
        if not recording:
            raise ValueError('Attach the reference recording first.')
        return recording_payload(recording, args.voice_name)
    profile = load_profile(args.profile, args.job)
    slides, _ = current_slides(args.job, profile)
    if not profile.get('voice_id'):
        raise ValueError('Choose a voice_id before approving speech generation.')
    return speech_payload(slides, profile, preview=args.preview)


def execute(args):
    if args.command == 'init':
        directory = absolute(args.job)
        inputs = {}
        for role in ('paper', 'template', 'deck', 'script', 'recording'):
            value = getattr(args, role)
            if value:
                file = absolute(value)
                if not file.is_file():
                    raise ValueError(f'Missing {role}: {file}')
                inputs[role] = str(file)
        data = dict(schema_version=1, inputs=inputs, answers=answer_values(args.set), artifacts={})
        write_json(directory / 'job.json', data, exclusive=True)
        return dict(job=str(directory / 'job.json'), next_command='presentflow plan ' + str(directory))
    if args.command == 'answer':
        job = set_answers(args.job, answer_values(args.set))
        return dict(answers=job['answers'])
    if args.command == 'attach':
        path, job = load_job(args.job)
        for role in ('paper', 'template', 'deck', 'script', 'recording'):
            value = getattr(args, role)
            if value:
                file = absolute(value)
                if not file.is_file():
                    raise ValueError(f'Missing {role}: {file}')
                job.setdefault('inputs', {})[role] = str(file)
        # New source material invalidates generated artifact selection; old files remain on disk.
        job['artifacts'] = {}
        write_json(path, job)
        return dict(inputs=job['inputs'])
    if args.command == 'plan':
        from .planning import inspect_job, make_plan
        from .hardware import detect
        _, job, materials = inspect_job(args.job)
        hardware = read_json(args.hardware) if args.hardware else detect()
        plan = make_plan(materials, job.get('answers'), hardware)
        plan['hardware'] = hardware
        if args.output:
            write_json(args.output, plan)
        return plan
    if args.command == 'doctor':
        from .hardware import detect, recommend
        from importlib.util import find_spec
        hardware = detect()
        return dict(hardware=hardware, optional_local_voice_installed=bool(find_spec('qwen_tts')),
                    recommendations=recommend(hardware, privacy=args.privacy, voice_mode=args.voice_mode))
    if args.command == 'extract':
        from .planning import inspect_job
        from .ingest import markdown_script
        path, _, materials = inspect_job(args.job)
        target = path.parent / 'private' / 'extracted'
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / 'inventory.json', materials)
        if materials.get('paper', {}).get('pages'):
            text = '\n\n'.join(f'## Page {p["page"]}\n\n{p["text"]}' for p in materials['paper']['pages'])
            (target / 'paper.md').write_text(text, encoding='utf-8')
        if materials.get('deck', {}).get('slides'):
            (target / 'existing_script.md').write_text(markdown_script(materials['deck']['slides']), encoding='utf-8')
        return dict(extracted_to=str(target), input_errors={k: v['error'] for k, v in materials.items() if v.get('error')})
    if args.command == 'compose':
        from .authoring import compose
        return compose(args.job, args.spec, args.output)
    if args.command in ('preview', 'narrate'):
        from .voice import narrate
        return narrate(args.job, args.profile, preview=args.command == 'preview')
    if args.command == 'approve-preview':
        from .voice import approve_preview
        return approve_preview(args.job, args.sha256)
    if args.command in ('consent-summary', 'grant-consent'):
        from . import consent
        payload = outgoing(args)
        scope = consent.payload_scope('elevenlabs', args.purpose, payload)
        if args.command == 'grant-consent':
            return consent.grant(args.job, 'elevenlabs', args.purpose, payload, approved=args.approve)
        summary = dict(scope, does_not_upload=True)
        if args.purpose == 'upload_recording':
            summary.update(bytes=payload['bytes'], data_categories=['reference_recording', 'voice_name'])
        else:
            summary.update(characters=sum(map(len, payload['texts'])), slides=len(payload['texts']),
                           data_categories=['spoken_script', 'selected_voice_and_model'], preview=payload['preview'])
        return summary
    if args.command == 'create-voice':
        from .cloud import create_voice
        path, job = load_job(args.job)
        recording = input_files(path, job).get('recording')
        if not recording:
            raise ValueError('Attach the reference recording first.')
        return create_voice(args.job, recording, args.name)
    if args.command == 'assemble':
        from .voice import assemble
        return assemble(args.job, args.output, args.profile, args.audio_manifest,
                        air=args.air, min_pause=args.min_pause, max_pause=args.max_pause)
    raise ValueError('Unsupported command.')


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        emit(execute(args))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'PresentFlow: {exc}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
