# Passive verifier and private-runtime cache helper. Never connects to an account.
$ErrorActionPreference='Stop'

function Get-QmtFileHash([string]$Path) {
    $sha=[Security.Cryptography.SHA256]::Create()
    $stream=[IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose();$sha.Dispose() }
}

function Assert-QmtNoReparse([string]$Path) {
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if([IO.File]::Exists($p) -or [IO.Directory]::Exists($p)){
            if(([IO.File]::GetAttributes($p) -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'linked_path_refused'}
        }
        $parent=[IO.Directory]::GetParent($p)
        if($null -eq $parent){break}
        $p=$parent.FullName
    }
}

function Assert-QmtRelativeName([string]$Name) {
    if(-not $Name -or $Name.Contains('\') -or $Name -match '[:<>"|?*\x00-\x1f]' -or $Name.StartsWith('/')){throw 'unsafe_manifest_name'}
    foreach($part in $Name.Split('/')){
        if(-not $part -or $part -in @('.','..') -or $part.EndsWith(' ') -or $part.EndsWith('.') -or $part -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$'){throw 'unsafe_manifest_name'}
    }
}

function Get-QmtRegularFiles([string]$Root) {
    $stack=New-Object 'Collections.Generic.Stack[string]'
    $stack.Push($Root)
    while($stack.Count){
        $dir=$stack.Pop()
        foreach($item in [IO.Directory]::EnumerateFileSystemEntries($dir)){
            $a=[IO.File]::GetAttributes($item)
            if(($a -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'linked_package_entry_refused'}
            if(($a -band [IO.FileAttributes]::Directory) -ne 0){$stack.Push($item)}else{Write-Output $item}
        }
    }
}

function Assert-QmtTree([string]$Root,$Files,[string[]]$Excluded=@()) {
    $Root=[IO.Path]::GetFullPath($Root).TrimEnd([char[]]'\/')
    Assert-QmtNoReparse $Root
    if(-not [IO.Directory]::Exists($Root)){throw 'package_directory_missing'}
    $expected=@{};$total=[long]0
    foreach($prop in $Files.PSObject.Properties){
        $name=[string]$prop.Name;Assert-QmtRelativeName $name
        if($expected.ContainsKey($name)){throw 'duplicate_manifest_name'}
        $record=$prop.Value
        if($record.sha256 -cnotmatch '^[a-f0-9]{64}$' -or ($record.size -isnot [int] -and $record.size -isnot [long]) -or [long]$record.size -lt 0 -or [long]$record.size -gt 134217728){throw 'invalid_manifest_hash_or_size'}
        $total+=[long]$record.size;$expected[$name]=$record
    }
    if($expected.Count -gt 12000 -or $total -gt 805306368){throw 'package_size_limit'}
    $seen=@{}
    foreach($path in @(Get-QmtRegularFiles $Root)){
        $name=$path.Substring($Root.Length+1).Replace('\','/')
        if($Excluded -contains $name){continue}
        if(-not $expected.ContainsKey($name) -or $seen.ContainsKey($name)){throw 'unlisted_or_duplicate_package_file'}
        $seen[$name]=$true;$record=$expected[$name]
        if(([IO.FileInfo]::new($path)).Length -ne [long]$record.size -or (Get-QmtFileHash $path) -cne $record.sha256){throw 'package_file_hash_mismatch'}
    }
    if($seen.Count -ne $expected.Count){throw 'package_files_missing'}
    return $seen.Count
}

function Read-QmtJson([string]$Path) {
    if(([IO.FileInfo]::new($Path)).Length -gt 4194304){throw 'metadata_too_large'}
    return ([IO.File]::ReadAllText($Path,[Text.Encoding]::UTF8)|ConvertFrom-Json)
}

function Test-QmtDistribution([string]$PackageRoot) {
    Assert-QmtNoReparse $PackageRoot
    $manifest=Read-QmtJson (Join-Path $PackageRoot 'release/manifest.json')
    if($manifest.schema_version -ne 3 -or $manifest.artifact_kind -cne 'windows_offline_distribution' -or $manifest.version -notmatch '^\d+\.\d+\.\d+(?:-[a-z0-9.]+)?$'){throw 'distribution_manifest_invalid'}
    $count=Assert-QmtTree $PackageRoot $manifest.files @('release/manifest.json')
    $version=Read-QmtJson (Join-Path $PackageRoot 'release/version.json')
    if($version.version -cne $manifest.version -or -not $version.python_bundled -or -not $version.offline_dependencies_bundled -or $version.publisher_signature_included){throw 'distribution_identity_mismatch'}
    foreach($name in @('setup.ps1','verify.ps1','payload/setup.ps1','payload/install.ps1','payload/dependencies.lock.json','runtime/python/python.exe','runtime/python/pythonw.exe','release/runtime-manifest.json')){
        if(-not $manifest.files.PSObject.Properties[$name]){throw 'required_distribution_file_missing'}
    }
    return @{ok=$true;code='distribution_integrity_verified';version=$version.version;file_count=$count+1;publisher_authenticity_verified=$false;account_connected=$false;message_sent=$false;submission_calls=0}
}

function Get-QmtPrivateRuntime([string]$PackageRoot,[string]$CacheParent) {
    # Call only after Test-QmtDistribution. Explicit New/ResumeNew is the write authorization.
    $metadata=Join-Path $PackageRoot 'release/runtime-manifest.json'
    $identity=Get-QmtFileHash $metadata
    $manifest=Read-QmtJson $metadata
    if($manifest.python_version -cne '3.11.16' -or $manifest.target -cne 'x86_64-pc-windows-msvc'){throw 'private_runtime_identity_invalid'}
    Assert-QmtNoReparse $CacheParent
    $root=Join-Path $CacheParent $identity
    $mutex=[Threading.Mutex]::new($false,('Local\QmtIpoRuntime-'+$identity))
    $locked=$false;$createdStage=$null
    try{
        try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
        if(-not $locked){throw 'runtime_cache_busy'}
        if(Test-Path -LiteralPath $root){
            Assert-QmtNoReparse $root
            $saved=Read-QmtJson (Join-Path $root 'CACHE_IDENTITY.json')
            if($saved.runtime_manifest_sha256 -cne $identity){throw 'existing_runtime_cache_identity_mismatch'}
            [void](Assert-QmtTree (Join-Path $root 'python') $manifest.files)
            $extras=@([IO.Directory]::EnumerateFileSystemEntries($root)|Where-Object {[IO.Path]::GetFileName($_) -notin @('python','CACHE_IDENTITY.json')})
            if($extras.Count){throw 'existing_runtime_cache_has_unlisted_files'}
            return (Join-Path $root 'python/python.exe')
        }
        [void][IO.Directory]::CreateDirectory($CacheParent)
        $createdStage=Join-Path $CacheParent ($identity+'.staging-'+[guid]::NewGuid().ToString('N'))
        [void][IO.Directory]::CreateDirectory($createdStage)
        $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        & icacls.exe $createdStage /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
        if($LASTEXITCODE -ne 0){throw 'runtime_cache_acl_failed'}
        foreach($prop in $manifest.files.PSObject.Properties){
            $name=[string]$prop.Name;Assert-QmtRelativeName $name
            $dest=Join-Path (Join-Path $createdStage 'python') $name
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
            [IO.File]::Copy((Join-Path (Join-Path $PackageRoot 'runtime/python') $name),$dest,$false)
        }
        [void](Assert-QmtTree (Join-Path $createdStage 'python') $manifest.files)
        $record=@{schema_version=1;runtime_manifest_sha256=$identity;python_version=$manifest.python_version;purpose='private_python_runtime_only_no_account_data'}
        [IO.File]::WriteAllText((Join-Path $createdStage 'CACHE_IDENTITY.json'),($record|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
        [IO.Directory]::Move($createdStage,$root);$createdStage=$null
        return (Join-Path $root 'python/python.exe')
    }finally{
        # Only this invocation's unpublished staging tree can be removed. Never remove
        # an existing cache; installed virtual environments may reference it.
        if($createdStage -and [IO.Directory]::Exists($createdStage)){[IO.Directory]::Delete($createdStage,$true)}
        if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()
    }
}
