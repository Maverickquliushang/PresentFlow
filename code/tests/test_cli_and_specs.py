from argparse import Namespace
import json

import pytest
from pypdf import PdfWriter

from presentflow.authoring import validate_spec
from presentflow.cli import build_parser,execute
from presentflow.planning import inspect_job
from presentflow.ingest import markdown_script
from presentflow.storage import read_json


def test_cli_paper_only_job_and_scanned_pdf(tmp_path):
    pdf=tmp_path/'paper.pdf'
    writer=PdfWriter();writer.add_blank_page(width=300,height=400)
    with pdf.open('wb') as stream:writer.write(stream)
    directory=tmp_path/'run'
    args=build_parser().parse_args(['init',str(directory),'--paper',str(pdf),'--set','voice_mode=none'])
    execute(args)
    _,job,materials=inspect_job(directory)
    assert materials['paper']['needs_ocr']
    assert job['answers']['voice_mode']=='none'
    with pytest.raises(FileExistsError):execute(args)


def test_spec_rejects_unspoken_notes_and_nonexistent_assets(tmp_path):
    with pytest.raises(ValueError,match='provenance'):
        validate_spec(dict(slides=[dict(title='Title',notes='Hello.\nSource: Paper')]),tmp_path)
    with pytest.raises(ValueError,match='missing image'):
        validate_spec(dict(slides=[dict(title='Title',notes='Hello.',image='missing.png')]),tmp_path)


def test_spec_rejects_wrong_evidence_pages_and_table_dimensions(tmp_path):
    with pytest.raises(ValueError,match='source page'):
        validate_spec(dict(slides=[dict(title='Title',notes='Hello.',sources=[dict(page=9)])]),tmp_path,pages=2)
    with pytest.raises(ValueError,match='table dimensions'):
        validate_spec(dict(slides=[dict(title='Title',notes='Hello.',table=dict(columns=['A','B'],rows=[['one']]))]),tmp_path)


def test_preserve_and_restyle_are_explicit_choices(tmp_path):
    with pytest.raises(ValueError,match='template_slide'):
        validate_spec(dict(slides=[dict(title='Title',notes='Hello.')]),tmp_path,template_slides=3)


def test_invalid_deployment_answer_is_not_silently_accepted(job):
    args=build_parser().parse_args(['answer',str(job),'--set','voice_provider=public-random-demo'])
    with pytest.raises(ValueError,match='Invalid voice_provider'):
        execute(args)


def test_template_duration_choice_is_recorded_as_an_inference_policy(job):
    args=build_parser().parse_args(['answer',str(job),'--set','duration_minutes=follow_template'])
    execute(args)
    assert read_json(job/'job.json')['answers']['duration_basis']=='template'


def test_authored_spec_can_export_script_before_native_rendering():
    text=markdown_script([dict(title='Intro',notes='Hello.'),dict(title='End',notes='Thank you.')])
    assert '## Slide 01: Intro' in text and '## Slide 02: End' in text
