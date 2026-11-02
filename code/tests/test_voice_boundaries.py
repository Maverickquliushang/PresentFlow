import json
from pathlib import Path

import pytest

from presentflow import cloud,consent
from presentflow.hardware import recommend
from presentflow.storage import read_json,set_answers,write_json
from presentflow.voice import narrate


def test_local_only_never_recommends_cloud_even_without_gpu():
    result=recommend(dict(ram_gb=8,gpus=[]),privacy='local_only',voice_mode='clone')
    assert not any(o['id']=='elevenlabs' for o in result['options'])
    assert result['requires_user_choice']


def test_capable_hardware_is_only_a_recommendation():
    result=recommend(dict(ram_gb=32,gpus=[dict(vram_gb=12,backend='cuda')]),voice_mode='clone')
    assert result['requires_user_choice'] and result['thresholds_are_heuristics']
    assert result['options'][0]['id']=='qwen-local'


def test_local_only_blocks_even_previously_granted_upload(job):
    payload=dict(text='Original speech')
    consent.grant(job,'elevenlabs','synthesize_speech',payload,approved=True)
    set_answers(job,dict(privacy='local_only'))
    with pytest.raises(ValueError,match='disabled'):
        consent.require(job,'elevenlabs','synthesize_speech',payload)


def test_recording_grant_cannot_authorize_text_or_changed_data(job):
    payload=dict(recording_sha256='a')
    consent.grant(job,'elevenlabs','upload_recording',payload,approved=True)
    with pytest.raises(ValueError,match='Missing'):
        consent.require(job,'elevenlabs','synthesize_speech',payload)
    with pytest.raises(ValueError,match='Missing'):
        consent.require(job,'elevenlabs','upload_recording',dict(recording_sha256='b'))


def test_cloud_does_not_open_transport_without_approval(job,tmp_path):
    calls=[]
    with pytest.raises(ValueError,match='Missing'):
        cloud.synthesize(job,[dict(slide=1,spoken='Hello.')],dict(voice_id='voice'),tmp_path/'cache',transport=lambda *args:calls.append(args))
    assert not calls


def test_cloud_cache_changed_script_and_cumulative_budget(job,tmp_path):
    set_answers(job,dict(cloud_budget_chars=12))
    profile=dict(voice_id='voice');slides=[dict(slide=1,spoken='Hello.')]
    payload=cloud.speech_payload(slides,profile)
    consent.grant(job,'elevenlabs','synthesize_speech',payload,approved=True)
    calls=[]
    def transport(*args):
        calls.append(args)
        return b'ID3-mocked-audio-response'
    first=cloud.synthesize(job,slides,profile,tmp_path/'cache',transport=transport)
    again=cloud.synthesize(job,slides,profile,tmp_path/'cache',transport=transport)
    assert first[0]['sha256']==again[0]['sha256'] and len(calls)==1
    slides[0]['spoken']='Changed.'
    with pytest.raises(ValueError,match='Missing'):
        cloud.synthesize(job,slides,profile,tmp_path/'cache',transport=transport)
    consent.grant(job,'elevenlabs','synthesize_speech',cloud.speech_payload(slides,profile),approved=True)
    with pytest.raises(ValueError,match='Cumulative'):
        cloud.synthesize(job,slides,profile,tmp_path/'cache',transport=transport)
    assert len(calls)==1


def test_failed_request_reserves_budget_without_automatic_retry(job,tmp_path):
    set_answers(job,dict(cloud_budget_chars=6))
    slides=[dict(slide=1,spoken='Hello.')];profile=dict(voice_id='voice')
    consent.grant(job,'elevenlabs','synthesize_speech',cloud.speech_payload(slides,profile),approved=True)
    calls=[]
    def failure(*args):
        calls.append(args)
        raise ValueError('Network failed')
    with pytest.raises(ValueError,match='Network failed'):
        cloud.synthesize(job,slides,profile,tmp_path/'cache',transport=failure)
    assert len(calls)==1
    with pytest.raises(ValueError,match='Cumulative'):
        cloud.synthesize(job,slides,profile,tmp_path/'cache',transport=failure)
    assert len(calls)==1


def test_cloud_clone_requires_recording_approval_and_neutral_filename(job,tmp_path):
    sample=tmp_path/'private-personal-filename.wav';sample.write_bytes(b'fake-reference')
    set_answers(job,dict(voice_rights='own_voice'))
    calls=[]
    def transport(route,body,kind):
        calls.append(body)
        return json.dumps(dict(voice_id='new-voice',requires_verification=True)).encode()
    with pytest.raises(ValueError,match='Missing'):
        cloud.create_voice(job,sample,'My voice',transport=transport)
    consent.grant(job,'elevenlabs','upload_recording',cloud.recording_payload(sample,'My voice'),approved=True)
    result=cloud.create_voice(job,sample,'My voice',transport=transport)
    assert result['requires_verification']
    assert b'private-personal-filename' not in calls[0]


def test_generation_requires_model_choice_before_loading_libraries(job):
    set_answers(job,dict(voice_mode='clone'))
    with pytest.raises(ValueError,match='Choose voice_provider'):
        narrate(job)
