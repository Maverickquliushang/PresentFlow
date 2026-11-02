"""Opt-in integration check: desktop PowerPoint, SAPI and FFmpeg; no cloud/data upload."""
import array
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from presentflow.authoring import compose,native
from presentflow.cli import build_parser,execute
from presentflow.ingest import inspect_deck,markdown_script
from presentflow.storage import read_json,sha,write_json
from presentflow.voice import assemble

pytestmark=pytest.mark.skipif(os.name!='nt' or os.environ.get('PRESENTFLOW_NATIVE_TESTS')!='1',reason='Explicit Windows PowerPoint integration run only.')


def initialize(directory,**inputs):
    args=['init',str(directory),'--set','voice_mode=none','--set','language=en']
    for key,value in inputs.items():args.extend(['--'+key,str(value)])
    return execute(build_parser().parse_args(args))


def test_native_workflow(tmp_path):
    example=Path(__file__).resolve().parents[1]/'examples/talk.json'
    spec=read_json(example)
    script=tmp_path/'script.md';script.write_text(markdown_script(spec['slides']),encoding='utf-8')
    job=tmp_path/'new';initialize(job,script=script)
    original=tmp_path/'builtin.pptx'
    compose(job,example,original)
    assert inspect_deck(original)['slide_count']==3

    # A finished PPT gets only notes changed; the implementation checks every rendered pixel.
    preserved=tmp_path/'preserved';initialize(preserved,deck=original)
    changed=json.loads(json.dumps(spec));changed['slides'][1]['notes']='Here is an updated explanation of the same editable table. These values remain synthetic examples.'
    changed_file=tmp_path/'changed.json';write_json(changed_file,changed)
    updated=tmp_path/'updated.pptx';compose(preserved,changed_file,updated)
    assert inspect_deck(updated)['slides'][1]['notes']==changed['slides'][1]['notes']

    # Clone a real template slide, explicitly replacing every text shape.
    template_job=tmp_path/'templated';initialize(template_job,template=original,script=script)
    objects=inspect_deck(original)['slides'][0]['objects']
    text_names=[o['name'] for o in objects if o['text']]
    templated=dict(slides=[])
    for i in range(2):
        updates={name:(f'Template Example {i+1}' if n==0 else 'New content' if n==1 else str(i+1)) for n,name in enumerate(text_names)}
        templated['slides'].append(dict(title=f'Template Example {i+1}',notes='This is newly written content in the supplied template.',template_slide=1,updates=updates,retain_text_shapes=[]))
    template_spec=tmp_path/'template.json';write_json(template_spec,templated)
    composed=tmp_path/'template_result.pptx';compose(template_job,template_spec,composed)
    assert inspect_deck(composed)['slide_count']==2
    assert 'A Small Research Presentation' not in json.dumps(inspect_deck(composed))

    # Use local installed SAPI speech only for the integration sample; no personal voice is needed.
    audio_dir=tmp_path/'audio';audio_dir.mkdir()
    speech_file=tmp_path/'sapi.json'
    speech=[dict(text=s['notes'],output=str(audio_dir/f'slide_{i:02}.wav')) for i,s in enumerate(changed['slides'],1)]
    write_json(speech_file,speech)
    sapi_script=tmp_path/'sapi.ps1'
    sapi_script.write_text('''param([string]$PlanPath)
$ErrorActionPreference='Stop'
$items=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$speaker=New-Object -ComObject SAPI.SpVoice
foreach($item in $items){
    $stream=New-Object -ComObject SAPI.SpFileStream
    $stream.Open([string]$item.output,3,$false)
    $speaker.AudioOutputStream=$stream
    $speaker.Speak([string]$item.text) | Out-Null
    $stream.Close()
}
''',encoding='utf-8')
    native(sapi_script,['-PlanPath',speech_file])
    manifest=[]
    for i,item in enumerate(speech,1):
        mp3=audio_dir/f'slide_{i:02}.mp3'
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-i',item['output'],'-c:a','libmp3lame',str(mp3)],check=True)
        manifest.append(dict(slide=i,spoken=item['text'],file=str(mp3),sha256=sha(mp3)))
    manifest_file=tmp_path/'audio_manifest.json';write_json(manifest_file,manifest)
    final=tmp_path/'autoplay.pptx'
    assemble(preserved,final,manifest_file=manifest_file)
    validation=read_json(preserved/'private/assembly/validation.json')
    assert validation['macro_free'] and validation['all_slides_pixel_identical']
    assert validation['notes_match'] and validation['all_audio_embedded']

    # A native video export verifies the actual media start and timed progression.
    export_plan=tmp_path/'export.json';video=tmp_path/'autoplay.mp4'
    write_json(export_plan,dict(source=str(final),video=str(video)))
    export_script=tmp_path/'export.ps1'
    export_script.write_text('''param([string]$PlanPath)
$ErrorActionPreference='Stop'
$plan=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$app=New-Object -ComObject PowerPoint.Application
$initialCount=$app.Presentations.Count
$deck=$null
try{
    $deck=$app.Presentations.Open([string]$plan.source,-1,0,0)
    $deck.CreateVideo([string]$plan.video,-1,1,240,15,50)
    $started=Get-Date
    do{
        Start-Sleep -Milliseconds 500
        if(((Get-Date)-$started).TotalSeconds -gt 120){throw 'Video export timeout.'}
    }while($deck.CreateVideoStatus -in @(1,2) -or -not (Test-Path -LiteralPath $plan.video))
    if($deck.CreateVideoStatus -eq 4){throw 'Video export failed.'}
}finally{
    if($null -ne $deck){try{$deck.Saved=-1;$deck.Close()}catch{}}
    if($initialCount -eq 0 -and $app.Presentations.Count -eq 0){$app.Quit()}
}
''',encoding='utf-8')
    native(export_script,['-PlanPath',export_plan])
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(video)],capture_output=True,text=True,check=True)
    duration=float(json.loads(result.stdout)['format']['duration'])
    assert abs(duration-validation['show_seconds'])<.5
    measured=read_json(preserved/'private/assembly/measured_plan.json')
    levels=[]
    for slide in measured['slides']:
        at=slide['start']+1.0
        result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-ss',str(at),'-i',str(video),'-t','1','-ac','1','-ar','16000','-f','s16le','-'],capture_output=True,check=True)
        samples=array.array('h',result.stdout)
        rms=math.sqrt(sum(x*x for x in samples)/max(1,len(samples)))
        levels.append(rms)
        assert rms>100
    write_json(tmp_path/'native_validation.json',dict(native_compose=True,template_replacement=True,notes_only_pixel_identical=True,
        embedded_autoplay=True,native_export_seconds=duration,expected_seconds=validation['show_seconds'],audio_rms_each_slide=levels,
        personal_recordings_used=False,cloud_requests=0,final=str(final)))
