foreach($taskEntry in @{TEMP='.tmp';TMP='.tmp';TMPDIR='.tmp';UV_CACHE_DIR='.cache/uv';PIP_CACHE_DIR='.cache/pip';PYTHONPYCACHEPREFIX='.cache/python';NUMBA_CACHE_DIR='.cache/numba'}.GetEnumerator()){
    $taskPath=Join-Path $PSScriptRoot $taskEntry.Value
    [IO.Directory]::CreateDirectory($taskPath) | Out-Null
    [Environment]::SetEnvironmentVariable($taskEntry.Key,$taskPath,'Process')
}
$taskConfig=Join-Path $PSScriptRoot '.python-environment.json'
if(Test-Path -LiteralPath $taskConfig){
    $taskSettings=Get-Content -LiteralPath $taskConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    . (Join-Path $taskSettings.environmentRoot 'Activate-ProjectPython.ps1') -ConfigPath $taskConfig
}
