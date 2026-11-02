import pytest

from presentflow.ingest import inspect_deck, inspect_script
from presentflow.planning import make_plan


def deck(notes='none'):
    return dict(notes_state=notes,slide_count=2,language='en',slides=[
        dict(slide=1,notes='Hello.' if notes!='none' else '',has_speech=notes!='none',needs_notes_cleanup=False),
        dict(slide=2,notes='Thank you.' if notes=='complete' else '',has_speech=notes=='complete',needs_notes_cleanup=False)])


@pytest.mark.parametrize('materials,scenario',[
    ({'paper':{}},'paper_only'),
    ({'paper':{},'template':deck()},'paper_template'),
    ({'paper':{},'deck':deck()},'paper_deck_no_script'),
    ({'deck':deck()},'deck_no_script'),
    ({'deck':deck('partial')},'deck_partial_notes'),
    ({'paper':{},'deck':deck('complete')},'paper_deck_notes'),
    ({'deck':deck('complete')},'deck_notes'),
    ({'script':dict(aligned=True,slides=[{'slide':1,'text':'Hello.'}],language='en')},'script_only'),
    ({'template':deck()},'insufficient_content'),
    ({},'insufficient_content'),
])
def test_routes(materials,scenario):
    assert make_plan(materials)['scenario']==scenario


def test_only_pdf_asks_brief_before_voice():
    plan=make_plan({'paper':dict(language='en')})
    assert plan['current_stage']=='brief'
    assert all(q['stage']=='brief' for q in plan['questions_now'])
    assert len(plan['questions_now'])<=3
    assert 'reference_recording' not in [q['id'] for q in plan['questions']]


def test_no_narration_does_not_collect_recording_or_provider():
    plan=make_plan({'deck':deck('complete')},dict(voice_mode='none'))
    assert not plan['questions']
    assert not any(t['id'] in ('narrate','preview_voice','assemble_autoplay') for t in plan['tasks'])


def test_preserves_known_answers_and_existing_branding():
    plan=make_plan({'deck':deck()},dict(language='zh',duration_minutes=15,voice_mode='stock'))
    ids={q['id'] for q in plan['questions']}
    assert not ids.intersection({'language','duration_minutes','branding','reference_recording'})
    assert plan['inferred']['deck_action']=='preserve'


def test_conflicting_script_and_two_decks_need_choices():
    script=dict(aligned=True,slides=[dict(slide=1,text='Changed.'),dict(slide=2,text='Changed conclusion.')])
    plan=make_plan({'deck':deck('complete'),'template':deck(),'script':script})
    assert {'deck_action','script_source'} <= {q['id'] for q in plan['questions']}


def test_wrong_script_count_requires_alignment():
    script=dict(aligned=True,slides=[dict(slide=1,text='One page only.')])
    plan=make_plan({'deck':deck(),'script':script})
    assert any(t['id']=='align_script' for t in plan['tasks'])


def test_pptx_uses_presentation_order_and_body_notes_only(minimal_deck):
    path=minimal_deck(['First speaker script.','Second speaker script.'],order=[2,1])
    actual=inspect_deck(path)
    assert [s['notes'] for s in actual['slides']]==['Second speaker script.','First speaker script.']
    assert actual['notes_state']=='complete'
    assert '99' not in actual['slides'][0]['notes']


def test_source_only_notes_are_not_treated_as_speech(minimal_deck):
    actual=inspect_deck(minimal_deck(['Source: Paper Section 2','A useful spoken explanation.']))
    assert actual['notes_state']=='partial'
    assert actual['slides'][0]['has_speech'] is False


def test_nonempty_notes_with_metadata_need_cleanup(minimal_deck):
    actual=inspect_deck(minimal_deck(['A spoken explanation.\nSource: Page 2']))
    assert actual['notes_state']=='needs_cleanup'


def test_script_alignment_does_not_guess_paragraphs(tmp_path):
    path=tmp_path/'speech.md'
    path.write_text('One paragraph.\n\nAnother paragraph.',encoding='utf-8')
    assert not inspect_script(path)['aligned']
    path.write_text('## Slide 01: Intro\n\nHello.\n\n## Slide 02: End\n\nThank you.',encoding='utf-8')
    assert inspect_script(path)['aligned']
    path.write_text('## 第 1 页：开场\n\n大家好。\n\n## 第 2 页：结束\n\n谢谢。',encoding='utf-8')
    assert inspect_script(path)['aligned']


def test_template_duration_counts_chinese_characters():
    template=deck('complete')
    for slide in template['slides']:slide['notes']='这是中文演讲稿。'*100
    plan=make_plan({'paper':{},'template':template},dict(duration_basis='template'))
    assert plan['inferred']['duration_minutes']>4
