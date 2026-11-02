param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference='Stop'
$plan=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
if(Test-Path -LiteralPath $plan.output){throw 'Output already exists.'}
$app=New-Object -ComObject PowerPoint.Application
$initialCount=$app.Presentations.Count
$deck=$null
function Text-Box($slide,$text,$left,$top,$width,$height,$size,$bold=0){
    $shape=$slide.Shapes.AddTextbox(1,$left,$top,$width,$height)
    $shape.TextFrame.TextRange.Text=[string]$text
    $shape.TextFrame.WordWrap=-1
    $shape.TextFrame.TextRange.Font.Name='Arial'
    $shape.TextFrame.TextRange.Font.Size=$size
    if($bold -eq -1){$shape.TextFrame.TextRange.Font.Bold=-1}else{$shape.TextFrame.TextRange.Font.Bold=0}
    return $shape
}
function Render-All($deck,$folder){
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $height=[int][Math]::Round(1280*$deck.PageSetup.SlideHeight/$deck.PageSetup.SlideWidth)
    foreach($s in $deck.Slides){$s.Export((Join-Path $folder ('slide_{0:D2}.png' -f $s.SlideIndex)),'PNG',1280,$height)}
}
try{
    if($plan.mode -in @('notes','template')){
        $copy=Join-Path $plan.work 'source_copy.pptx'
        if(@($app.Presentations | ForEach-Object {$_.FullName}) -contains $copy){throw 'Task copy is open.'}
        Copy-Item -LiteralPath $plan.source -Destination $copy -Force
        $deck=$app.Presentations.Open($copy,0,0,0)
        if($plan.mode -eq 'notes'){Render-All $deck (Join-Path $plan.work 'before')}
    }else{
        $deck=$app.Presentations.Add(0)
        $deck.PageSetup.SlideWidth=960
        $deck.PageSetup.SlideHeight=540
    }
    $originalCount=$deck.Slides.Count
    $index=0
    foreach($item in $plan.spec.slides){
        $index++
        if($plan.mode -eq 'notes'){$slide=$deck.Slides.Item($index)}
        elseif($plan.mode -eq 'template'){
            $slide=$deck.Slides.Item([int]$item.template_slide).Duplicate().Item(1)
            $slide.MoveTo($deck.Slides.Count)
            $mapped=@($item.updates.PSObject.Properties.Name)
            $retained=@($item.retain_text_shapes)
            foreach($shape in $slide.Shapes){
                if($shape.HasTextFrame -and $shape.TextFrame.HasText){
                    $idKey='id:'+[string]$shape.Id
                    if($mapped -contains $idKey){$shape.TextFrame.TextRange.Text=[string]$item.updates.($idKey)}
                    elseif($mapped -contains $shape.Name){$shape.TextFrame.TextRange.Text=[string]$item.updates.($shape.Name)}
                    elseif($retained -notcontains $shape.Name -and $retained -notcontains $idKey){throw ('Template text shape must be updated or explicitly retained: '+$shape.Name+' ('+$idKey+')')}
                }
            }
            foreach($name in $mapped){
                if($name -match '^id:(\d+)$'){
                    $shape=@($slide.Shapes | Where-Object {$_.Id -eq [int]$Matches[1]})[0]
                    if($null -eq $shape){throw ('Unknown template shape ID: '+$name)}
                }else{$shape=$slide.Shapes.Item($name)}
                if(-not $shape.HasTextFrame){throw ('Not a text shape: '+$name)}
                $shape.TextFrame.TextRange.Text=[string]$item.updates.($name)
            }
        }else{
            $slide=$deck.Slides.Add($deck.Slides.Count+1,12)
            $slide.FollowMasterBackground=0
            $slide.Background.Fill.Solid()
            $slide.Background.Fill.ForeColor.RGB=16777215
            $title=Text-Box $slide $item.title 48 36 864 94 30 -1
            $title.TextFrame.TextRange.Font.Color.RGB=7873310
            if($item.bullets){
                $text=(@($item.bullets) -join "`r")
                $body=Text-Box $slide $text 54 155 $(if($item.image){480}else{850}) 310 22
                $body.TextFrame.TextRange.ParagraphFormat.SpaceAfter=12
            }
            if($item.table){
                $rows=@($item.table.rows)
                $columns=@($item.table.columns)
                $table=$slide.Shapes.AddTable($rows.Count+1,$columns.Count,54,160,850,260).Table
                for($c=1;$c -le $columns.Count;$c++){
                    $table.Cell(1,$c).Shape.TextFrame.TextRange.Text=[string]$columns[$c-1]
                    for($r=1;$r -le $rows.Count;$r++){$table.Cell($r+1,$c).Shape.TextFrame.TextRange.Text=[string]$rows[$r-1][$c-1]}
                }
            }
            $footer=Text-Box $slide ([string]$index) 880 500 40 20 12
        }
        if($plan.mode -ne 'notes' -and $item.image){
            $picture=$slide.Shapes.AddPicture([string]$item.image,0,-1,550,155,-1,-1)
            $picture.LockAspectRatio=-1
            $picture.Width=355
            if($picture.Height -gt 300){$picture.Height=300}
        }
        if($plan.mode -eq 'builtin' -and $index -eq 1){
            foreach($key in @('conference_logo','institution_logo')){
                if($plan.spec.($key)){
                    $picture=$slide.Shapes.AddPicture([string]$plan.spec.($key),0,-1,$(if($key -eq 'conference_logo'){48}else{840}),455,-1,-1)
                    $picture.LockAspectRatio=-1
                    $picture.Width=72
                    if($picture.Height -gt 48){$picture.Height=48}
                }
            }
        }
        $body=@($slide.NotesPage.Shapes | Where-Object {$_.Type -eq 14 -and $_.PlaceholderFormat.Type -eq 2})
        if($body.Count -ne 1){throw 'Expected one notes body placeholder.'}
        $body[0].TextFrame.TextRange.Text=[string]$item.notes
    }
    if($plan.mode -eq 'template'){
        for($i=1;$i -le $originalCount;$i++){$deck.Slides.Item(1).Delete()}
    }
    $outputParent=Split-Path -Parent $plan.output
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    $deck.SaveAs([string]$plan.output,24)
    Render-All $deck (Join-Path $plan.work 'after')
    Write-Output ('PPTX saved: '+$plan.output)
}finally{
    if($null -ne $deck){try{$deck.Saved=-1;$deck.Close()}catch{}}
    if($initialCount -eq 0 -and $app.Presentations.Count -eq 0){$app.Quit()}
}
