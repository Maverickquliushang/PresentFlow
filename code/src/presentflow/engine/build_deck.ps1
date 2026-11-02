param(
    [Parameter(Mandatory=$true)][string]$PlanPath,
    [Parameter(Mandatory=$true)][ValidateSet('prepare','finish')][string]$Phase
)
$ErrorActionPreference='Stop'
$plan=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskWork=[IO.Path]::GetFullPath($plan.work)
$stage=Join-Path $taskWork 'pptvoice_stage.pptx'
$privateSource=Join-Path $taskWork 'pptvoice_source_copy.pptx'
$candidate=[IO.Path]::GetFullPath($plan.candidate)
function Get-LocalHash([string]$file){
    $algorithm=[Security.Cryptography.SHA256]::Create()
    $stream=[IO.File]::OpenRead($file)
    try{return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','').ToLower()}
    finally{$stream.Dispose();$algorithm.Dispose()}
}
if($candidate -eq [IO.Path]::GetFullPath($plan.source) -or $stage -eq [IO.Path]::GetFullPath($plan.source)){
    throw 'Task copies must differ from source.'
}
if((Get-LocalHash $plan.source) -ne $plan.source_sha256){
    throw 'Source changed.'
}
function Test-OwnedMedia($shape){
    return ($shape.Name -like 'PresentFlow Narration *' -or $shape.Name -eq 'PresentFlow Air' -or
            @($plan.profile.replace_audio_names) -contains $shape.Name)
}
function Export-Slides($deck,$directory){
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $imageHeight=[int][Math]::Round(1280*$deck.PageSetup.SlideHeight/$deck.PageSetup.SlideWidth)
    foreach($slide in $deck.Slides){
        $slide.Export((Join-Path $directory ('slide_{0:D2}.png' -f $slide.SlideIndex)),'PNG',1280,$imageHeight)
    }
}
function Add-Clip($deck,$slide,$file,$name,$delay,$stopSlides){
    $shape=$slide.Shapes.AddMediaObject2($file,0,-1,[single]($deck.PageSetup.SlideWidth+10),
        [single]($deck.PageSetup.SlideHeight+10),[single]1,[single]1)
    $shape.Name=$name
    $play=$shape.AnimationSettings.PlaySettings
    $play.PlayOnEntry=-1
    $play.PauseAnimation=0
    $play.StopAfterSlides=$stopSlides
    $play.LoopUntilStopped=0
    $play.HideWhileNotPlaying=-1
    for($n=$slide.TimeLine.MainSequence.Count;$n -ge 1;$n--){
        if($slide.TimeLine.MainSequence.Item($n).Shape.Id -eq $shape.Id){$slide.TimeLine.MainSequence.Item($n).Delete()}
    }
    $effect=$slide.TimeLine.MainSequence.AddEffect($shape,83,0,2)
    $effect.Timing.TriggerType=2
    $effect.Timing.TriggerDelayTime=[single]$delay
    return $shape
}
$app=New-Object -ComObject PowerPoint.Application
$initialCount=$app.Presentations.Count
$initialPaths=@($app.Presentations | ForEach-Object {$_.FullName})
foreach($path in @($stage,$privateSource,$candidate)){
    if($initialPaths -contains $path){throw 'A task copy is open. Close that task copy before rebuilding.'}
}
$deck=$null
try{
    if($Phase -eq 'prepare'){
        Copy-Item -LiteralPath $plan.source -Destination $privateSource -Force
        $deck=$app.Presentations.Open($privateSource,0,0,0)
        if($deck.Slides.Count -ne $plan.slides.Count){throw 'Script/slide count mismatch.'}
        Export-Slides $deck (Join-Path $taskWork 'render_source')
        $measured=@()
        $position=[double]0
        foreach($item in $plan.slides){
            $slide=$deck.Slides.Item([int]$item.slide)
            $priorVoiceBounds=$null
            for($n=$slide.Shapes.Count;$n -ge 1;$n--){
                $shape=$slide.Shapes.Item($n)
                if(Test-OwnedMedia $shape){
                    if($shape.Name -like '* Narration *'){
                        $priorVoiceBounds=@([single]$shape.Left,[single]$shape.Top,[single]$shape.Width,[single]$shape.Height)
                    }
                    $shape.Delete()
                }
                elseif($shape.Type -eq 16 -and $shape.MediaType -eq 1){
                    throw ('Unmanaged audio on slide {0}: {1}. Identify it before replacing narration.' -f $item.slide,$shape.Name)
                }
            }
            $body=@($slide.NotesPage.Shapes | Where-Object {$_.Type -eq 14 -and $_.PlaceholderFormat.Type -eq 2})
            if($body.Count -ne 1){throw 'Missing or ambiguous speaker notes placeholder.'}
            $body[0].TextFrame.TextRange.Text=[string]$item.text
            if((Get-LocalHash $item.audio_file) -ne $item.audio_sha256){throw 'Narration hash changed.'}
            $clip=Add-Clip $deck $slide $item.audio_file ('PresentFlow Narration {0:D2}' -f [int]$item.slide) $item.narration_delay 0
            if($null -ne $priorVoiceBounds){
                $clip.Left=$priorVoiceBounds[0];$clip.Top=$priorVoiceBounds[1]
                $clip.Width=$priorVoiceBounds[2];$clip.Height=$priorVoiceBounds[3]
            }
            $seconds=[double]$clip.MediaFormat.Length/1000
            if($seconds -le 0){throw 'PowerPoint could not read narration duration.'}
            $duration=[Math]::Round($seconds+[double]$item.narration_delay+[double]$item.pause_seconds,3)
            $slide.SlideShowTransition.AdvanceOnTime=-1
            $slide.SlideShowTransition.AdvanceOnClick=0
            $slide.SlideShowTransition.AdvanceTime=[single]$duration
            $measured+=[pscustomobject]@{
                slide=[int]$item.slide;title=$item.title;text=$item.text;audio_file=$item.audio_file;
                audio_sha256=$item.audio_sha256;audio_seconds=$seconds;narration_delay=[double]$item.narration_delay;
                pause_seconds=[double]$item.pause_seconds;slide_seconds=$duration;start=[Math]::Round($position,3)
            }
            $position+=$duration
        }
        $plan.slides=$measured
        $plan | Add-Member -NotePropertyName show_seconds -NotePropertyValue ([Math]::Round($position,3)) -Force
        $deck.SlideShowSettings.RangeType=1
        $deck.SlideShowSettings.AdvanceMode=2
        $deck.SlideShowSettings.LoopUntilStopped=0
        $deck.SlideShowSettings.ShowWithNarration=-1
        $deck.SlideShowSettings.ShowWithAnimation=-1
        $deck.SlideShowSettings.ShowMediaControls=0
        $deck.SlideShowSettings.ShowPresenterView=0
        $deck.Tags.Add('PresentFlow','Local narration; macro-free; preselected page pauses.')
        $deck.SaveAs($stage,24)
        $plan | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskWork 'measured_plan.json') -Encoding UTF8
    }else{
        $deck=$app.Presentations.Open($stage,0,0,0)
        $ambient=$null
        if($plan.PSObject.Properties.Name -contains 'air_file'){
            $first=$deck.Slides.Item(1)
            $ambient=Add-Clip $deck $first $plan.air_file 'PresentFlow Air' 0 $deck.Slides.Count
            $ambientEffect=@($first.TimeLine.MainSequence | Where-Object {$_.Shape.Id -eq $ambient.Id})
            $ambientEffect[0].MoveTo(1)
            # Moving an effect can change adjacent trigger types. Reset explicitly after MoveTo.
            $ambientEffect[0].Timing.TriggerType=2
            $ambientEffect[0].Timing.TriggerDelayTime=[single]0
            $ambient.AnimationSettings.PlaySettings.PlayOnEntry=-1
            $ambient.AnimationSettings.PlaySettings.PauseAnimation=0
            $ambient.AnimationSettings.PlaySettings.StopAfterSlides=$deck.Slides.Count
        }
        $records=@()
        foreach($item in $plan.slides){
            $slide=$deck.Slides.Item([int]$item.slide)
            $voice=@($slide.Shapes | Where-Object {$_.Name -like 'PresentFlow Narration *'})
            $effects=@($slide.TimeLine.MainSequence | Where-Object {$_.Shape.Id -eq $voice[0].Id})
            if($voice.Count -ne 1 -or $effects.Count -ne 1){throw 'Expected one narration and one playback effect.'}
            $effects[0].Timing.TriggerType=2
            $effects[0].Timing.TriggerDelayTime=[single]$item.narration_delay
            $records+=[pscustomobject]@{slide=[int]$item.slide;slide_seconds=[double]$slide.SlideShowTransition.AdvanceTime;
                audio_seconds=[double]$voice[0].MediaFormat.Length/1000;trigger=2;delay=[double]$effects[0].Timing.TriggerDelayTime}
        }
        $deck.SaveAs($candidate,24)
        Export-Slides $deck (Join-Path $taskWork 'render_final')
        $record=[ordered]@{candidate=$candidate;slides=$records;ambient_stop_after_slides=0}
        if($null -ne $ambient){$record.ambient_stop_after_slides=$ambient.AnimationSettings.PlaySettings.StopAfterSlides}
        $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskWork 'native_result.json') -Encoding UTF8
    }
}finally{
    if($null -ne $deck){try{$deck.Saved=-1;$deck.Close()}catch{}}
    if($initialCount -eq 0 -and $app.Presentations.Count -eq 0){$app.Quit()}
}
