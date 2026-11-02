. (Join-Path (Split-Path -Parent $PSScriptRoot) 'project-env.ps1')
& python -X utf8 -m presentflow @args
exit $LASTEXITCODE
