import argparse

import pytest

from presentflow.engine.clone_voice import speech_units,split_text,text_matches
from presentflow.engine.ppt_voice import get_profile,load_audio_manifest
from presentflow.storage import sha,write_json


def test_no_developer_machine_default_profile():
    with pytest.raises(ValueError,match='explicit local profile'):
        get_profile()


def test_stale_or_tampered_imported_audio_is_rejected(tmp_path):
    audio=tmp_path/'slide_01.mp3';audio.write_bytes(b'ID3-test-data')
    manifest=tmp_path/'manifest.json'
    write_json(manifest,[dict(slide=1,file=audio.name,spoken='Original speech.',sha256=sha(audio))])
    good=load_audio_manifest(manifest,[dict(slide=1,text='Original speech.')],{})
    assert good[0]['file']==str(audio)
    with pytest.raises(ValueError,match='differs'):
        load_audio_manifest(manifest,[dict(slide=1,text='Changed speech.')],{})
    audio.write_bytes(b'changed-audio')
    with pytest.raises(ValueError,match='hash'):
        load_audio_manifest(manifest,[dict(slide=1,text='Original speech.')],{})


def test_numbers_and_missing_speech_tail_fail_asr_verification():
    assert not text_matches('The score improved from 41.82 to 43.19.','The score improved from 41.82 to 41.19.')[0]
    assert not text_matches('Here is the idea. The final claim is very important.','Here is the idea.')[0]


def test_chinese_chunking_does_not_count_an_entire_paragraph_as_one_word():
    text='这是一个很长的中文演讲句子，需要根据语音长度拆分。'*10
    chunks=split_text(text)
    assert len(chunks)>1
    assert speech_units(text)>100
