# No registration or execution. Exact entrypoint ownership for the four tasks.
function Get-IpoTaskPrefix([string]$Root) {
    $prefix='QmtIPO3'
    $path=Join-Path $Root 'config.json'
    if(Test-Path -LiteralPath $path){
        $config=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json
        if($null -ne $config.task_prefix){$prefix=[string]$config.task_prefix}
    }
    if($prefix -cnotmatch '^QmtIPO[A-Za-z0-9-]{0,40}$'){throw 'Invalid task namespace'}
    return $prefix
}
function Get-IpoTaskSpecs([string]$Root) {
    $prefix=Get-IpoTaskPrefix $Root
    return @{ ($prefix+'-Cycle')='cycle'; ($prefix+'-Notify')='notify'; ($prefix+'-Monitor')='monitor'; ($prefix+'-Backup')='backup' }
}
function Convert-IpoPath([string]$Value) {
    return ([IO.Path]::GetFullPath($Value)).TrimEnd([char]92,[char]47).Replace([char]47,[char]92)
}
function Get-IpoArguments([string]$Root,[string]$Action) {
    $rootPath=Convert-IpoPath $Root
    if($rootPath.Contains('"') -or $rootPath.Contains("`n") -or $rootPath.Contains("`r")){throw 'Unsupported root quoting characters'}
    return '-B "'+(Join-Path $rootPath 'code/launcher.py')+'" '+$Action+' --root "'+$rootPath+'"'
}
function Assert-IpoOwnedTask($Task,[string]$Root,[string]$Action) {
    if(@($Task.Actions).Count -ne 1){throw 'Task must have one owned action'}
    $entry=$Task.Actions[0]
    $rootPath=Convert-IpoPath $Root
    if((Convert-IpoPath $entry.WorkingDirectory) -ne $rootPath){throw 'Task working directory belongs to another installation'}
    $exe=Convert-IpoPath $entry.Execute
    $arguments=([string]$entry.Arguments).Replace([char]47,[char]92)
    $modern=(Get-IpoArguments $Root $Action).Replace([char]47,[char]92)
    $legacy=('//B "'+(Join-Path $rootPath 'hidden.vbs')+'" "'+(Join-Path $rootPath 'run_scheduled.ps1')+'" '+$Action).Replace([char]47,[char]92)
    $python=Convert-IpoPath (Join-Path $rootPath '.venv/Scripts/pythonw.exe')
    $wscript=Convert-IpoPath (Join-Path $env:SystemRoot 'System32/wscript.exe')
    if(-not (($exe -eq $python -and $arguments -ceq $modern) -or ($exe -eq $wscript -and $arguments -ceq $legacy))){
        throw 'Task action is not an exact owned new/3.2 entrypoint; refuse mutation'
    }
}
