"""ElevenLabs official API adapter. Every request is gated before opening a socket."""
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import consent
from .storage import digest, read_json, sha, write_json

ORIGIN = 'https://api.elevenlabs.io'


def request(route, body, content_type, *, transport=None):
    if not route.startswith('/v1/') or '://' in route:
        raise ValueError('Unsupported API route.')
    if transport:
        return transport(route, body, content_type)
    token = os.environ.get('PRESENTFLOW_ELEVENLABS_API_KEY')
    if not token:
        raise ValueError('Set PRESENTFLOW_ELEVENLABS_API_KEY in this process; keys are not stored in job files.')
    req = urllib.request.Request(ORIGIN + route, data=body, method='POST', headers={
        'xi-api-key': token, 'Content-Type': content_type})
    # Reject redirects so authorization headers and personal data cannot move to a different origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ValueError(f'ElevenLabs API returned HTTP {exc.code}; no automatic paid retry was attempted.') from None
    except urllib.error.URLError:
        raise ValueError('ElevenLabs request failed; check the connection/account. No automatic paid retry was attempted.') from None


def recording_payload(recording, name):
    if not name.strip() or len(name) > 100 or '\r' in name or '\n' in name:
        raise ValueError('Use a nonempty single-line voice name of at most 100 characters.')
    return dict(recording_sha256=sha(recording), bytes=Path(recording).stat().st_size, voice_name=name)


def create_voice(directory, recording, name, *, transport=None):
    from .storage import load_job
    job_file, job = load_job(directory)
    if job.get('answers', {}).get('voice_rights') not in ('own_voice', 'speaker_permission'):
        raise ValueError('Confirm the speaker permission before cloning.')
    payload = recording_payload(recording, name)
    consent.require(directory, 'elevenlabs', 'upload_recording', payload)
    boundary = 'PresentFlow' + uuid.uuid4().hex
    file = Path(recording)
    mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
    # Use a neutral transport filename; local usernames and original file names are not sent.
    chunks = [f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{name}\r\n'.encode(),
              f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="reference{file.suffix.lower()}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
              file.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()]
    result = json.loads(request('/v1/voices/add', b''.join(chunks), f'multipart/form-data; boundary={boundary}', transport=transport))
    if not result.get('voice_id'):
        raise ValueError('Service did not return voice_id.')
    write_json(job_file.parent / 'private' / 'cloud_voice.json',
               dict(provider='elevenlabs', voice_id=result['voice_id'], requires_verification=result.get('requires_verification', False)))
    return result


def speech_payload(slides, profile, *, preview=False):
    texts = [s['spoken'] for s in slides]
    if preview:
        texts = [preview_text(texts[0])]
    return dict(model_id=profile.get('cloud_model', 'eleven_multilingual_v2'), voice_id=profile.get('voice_id'),
                voice_settings=profile.get('voice_settings', {}), texts=texts, preview=preview)


def preview_text(text):
    import re
    return text[:140] if re.search(r'[\u3400-\u9fff]', text) else ' '.join(text.split()[:80])


def synthesize(directory, slides, profile, cache, *, preview=False, transport=None):
    payload = speech_payload(slides, profile, preview=preview)
    if not payload['voice_id']:
        raise ValueError('Select a stock voice_id or create an authorized clone before synthesis.')
    consent.require(directory, 'elevenlabs', 'synthesize_speech', payload)
    from .storage import load_job
    _, job = load_job(directory)
    try:
        budget = int(job.get('answers', {})['cloud_budget_chars'])
    except (ValueError, KeyError, TypeError):
        raise ValueError('Choose a positive cloud_budget_chars limit before API use.') from None
    if budget <= 0:
        raise ValueError('cloud_budget_chars must be positive.')
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    records, pending = [], []
    for i, text in enumerate(payload['texts']):
        request_body = dict(text=text, model_id=payload['model_id'])
        if payload['voice_settings']:
            request_body['voice_settings'] = payload['voice_settings']
        identity = dict(provider='elevenlabs', voice=payload['voice_id'], request=request_body)
        key = digest(identity)
        file, meta = cache / (key + '.mp3'), cache / (key + '.json')
        record = dict(slide=slides[i]['slide'], spoken=text, file=str(file), signature=key)
        if file.exists() and meta.exists() and read_json(meta).get('sha256') == sha(file):
            record['sha256'] = sha(file)
        else:
            pending.append((record, request_body, meta))
        records.append(record)
    if sum(len(body['text']) for _, body, _ in pending) > budget:
        raise ValueError('Uncached speech exceeds the selected character budget. No request was sent.')
    # Keep a cumulative ledger across previews and reruns, not a fresh limit for every invocation.
    job_file, _ = load_job(directory)
    ledger_file = job_file.parent / 'private' / 'cloud_usage.json'
    ledger = read_json(ledger_file) if ledger_file.exists() else dict(reserved_characters=0, requests=[])
    new_chars = sum(len(body['text']) for _, body, _ in pending)
    if ledger['reserved_characters'] + new_chars > budget:
        raise ValueError('Cumulative requested characters exceed this job budget; explicitly increase the budget to continue.')
    for record, body, metadata in pending:
        # Reserve before sending: a lost response must not silently trigger another paid request.
        chars = len(body['text'])
        ledger['reserved_characters'] += chars
        ledger['requests'].append(dict(signature=record['signature'], characters=chars, status='reserved'))
        write_json(ledger_file, ledger)
        route = '/v1/text-to-speech/' + urllib.parse.quote(str(payload['voice_id']), safe='') + '?output_format=mp3_44100_128'
        audio = request(route, json.dumps(body).encode(), 'application/json', transport=transport)
        if not audio:
            raise ValueError('Empty audio response; no automatic retry.')
        file = Path(record['file'])
        temporary = file.with_suffix('.part')
        temporary.write_bytes(audio)
        temporary.replace(file)
        record['sha256'] = sha(file)
        write_json(metadata, record)
        ledger['requests'][-1]['status'] = 'received'
        write_json(ledger_file, ledger)
    return records
