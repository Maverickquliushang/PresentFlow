"""Explicit grants bound to a provider, purpose, and exact outgoing data."""
from datetime import datetime, timezone

from .storage import digest, load_job, read_json, write_json

PROVIDERS = {'elevenlabs': 'https://api.elevenlabs.io'}


def payload_scope(provider, purpose, payload):
    if provider not in PROVIDERS or purpose not in ('upload_recording', 'synthesize_speech'):
        raise ValueError('Unknown provider or consent purpose.')
    return dict(provider=provider, origin=PROVIDERS[provider], purpose=purpose, data_sha256=digest(payload))


def grant(directory, provider, purpose, payload, approved=False):
    if not approved:
        raise ValueError('Explicit user approval is required to record this grant.')
    path, job = load_job(directory)
    if job.get('answers', {}).get('privacy') == 'local_only':
        raise ValueError('This job is local_only.')
    scope = payload_scope(provider, purpose, payload)
    file = path.parent / 'private' / 'consents.json'
    grants = read_json(file) if file.exists() else []
    entry = dict(scope, approved=True, approved_at=datetime.now(timezone.utc).isoformat())
    if scope not in [{k: item[k] for k in scope} for item in grants]:
        grants.append(entry)
        write_json(file, grants)
    return entry


def require(directory, provider, purpose, payload):
    path, job = load_job(directory)
    if job.get('answers', {}).get('privacy') == 'local_only':
        raise ValueError('External upload is disabled by this job.')
    wanted = payload_scope(provider, purpose, payload)
    file = path.parent / 'private' / 'consents.json'
    grants = read_json(file) if file.exists() else []
    if not any(item.get('approved') and all(item.get(k) == v for k, v in wanted.items()) for item in grants):
        raise ValueError(f'Missing explicit approval for {provider}/{purpose} and the current outgoing data. Review consent-summary first.')
