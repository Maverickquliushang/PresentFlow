from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def absolute(path):
    return Path(os.path.abspath(path)).resolve()


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _hash_stream(handle)


def _hash_stream(handle):
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if exclusive:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(text)
    else:
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(path)


def job_path(directory):
    directory = absolute(directory)
    return directory if directory.name == 'job.json' else directory / 'job.json'


def load_job(directory):
    path = job_path(directory)
    data = read_json(path)
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported job schema_version.')
    return path, data


def resolve_input(job_file, value):
    return absolute(job_file.parent / value)


def input_files(job_file, job):
    return {key: resolve_input(job_file, value) for key, value in job.get('inputs', {}).items() if value}


def set_answers(directory, values):
    path, job = load_job(directory)
    job.setdefault('answers', {}).update(values)
    write_json(path, job)
    return job
