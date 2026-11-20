<#
.SYNOPSIS
  SSH-туннели к серверу бэкенда и быстрые проверки API через туннель.

.EXAMPLE
  .\tunnel.ps1 up                       # пробросить порты (по умолчанию 8000 -> api)
  .\tunnel.ps1 up -Forward 8000,8123    # несколько портов: локальный = удалённому
  .\tunnel.ps1 up -Forward 18000:8000   # локальный 18000 -> удалённый 8000
  .\tunnel.ps1 status                   # какие туннели подняты
  .\tunnel.ps1 down                     # закрыть все туннели этого скрипта
  .\tunnel.ps1 health                   # GET /health
  .\tunnel.ps1 smoke                    # вход -> разбор 2 PDF из dev -> статья -> submission.zip
  .\tunnel.ps1 smoke -Pdf a.pdf,b.pdf   # свои PDF
  .\tunnel.ps1 token                    # access-токен в буфер обмена (для Swagger / Postman)
  .\tunnel.ps1 logs [-Service api]      # хвост логов контейнеров на сервере
  .\tunnel.ps1 ps                       # состояние контейнеров на сервере

  Адрес сервера не выводится: работаем через ssh-алиас и localhost.
  Пароль админа берётся с сервера (deploy/.env) в память и не печатается.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'health', 'smoke', 'token', 'logs', 'ps')]
    [string]$Command = 'up',
    [string]$SshHost = 'hima-gpu',
    [string[]]$Forward = @('8000'),
    [int]$ApiPort = 0,
    [string[]]$Pdf = @(),
    [string]$Service = '',
    [int]$Tail = 100
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$RemoteDir = 'garden/backend/deploy'
$StateFile = Join-Path $env:TEMP 'faktura-tunnels.json'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

function Write-Ok([string]$m) { Write-Host "[ok] $m" -ForegroundColor Green }
function Write-Bad([string]$m) { Write-Host "[!!] $m" -ForegroundColor Red }
function Write-Info([string]$m) { Write-Host "     $m" -ForegroundColor Gray }

function Get-Pairs {
    $pairs = @()
    foreach ($f in $Forward) {
        foreach ($item in ($f -split ',')) {
            $item = $item.Trim()
            if (-not $item) { continue }
            if ($item -match '^(\d+):(\d+)$') { $pairs += , @([int]$Matches[1], [int]$Matches[2]) }
            elseif ($item -match '^\d+$') { $pairs += , @([int]$item, [int]$item) }
            else { throw "Неверный формат -Forward: '$item' (нужно 8000 или 18000:8000)" }
        }
    }
    return , $pairs
}

function Read-State {
    if (-not (Test-Path $StateFile)) { return @() }
    try { return @(Get-Content $StateFile -Raw | ConvertFrom-Json) } catch { return @() }
}

function Save-State($items) {
    if (-not $items -or @($items).Count -eq 0) { Remove-Item $StateFile -ErrorAction SilentlyContinue; return }
    ConvertTo-Json -InputObject @($items) -Depth 3 | Set-Content -Path $StateFile -Encoding utf8
}

function Test-LocalPort([int]$port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(500)) { return $false }
        $c.EndConnect($iar); return $true
    } catch { return $false } finally { $c.Close() }
}

function Get-Alive {
    $alive = @()
    foreach ($t in (Read-State)) {
        $p = Get-Process -Id $t.pid -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -like 'ssh*') { $alive += $t }
    }
    Save-State $alive
    return , $alive
}

function Invoke-Up {
    $alive = Get-Alive
    foreach ($pair in (Get-Pairs)) {
        $local = $pair[0]; $remote = $pair[1]
        $mine = $alive | Where-Object { $_.remote -eq $remote -and $_.host -eq $SshHost } | Select-Object -First 1
        if ($mine) { Write-Ok "localhost:$($mine.local) -> ${SshHost}:$remote уже проброшен"; continue }
        if (Test-LocalPort $local) {
            $wanted = $local
            $local = 10000 + $remote
            while ($local -le 65535 -and (Test-LocalPort $local)) { $local++ }
            if ($local -gt 65535) { Write-Bad "нет свободного локального порта для $remote"; continue }
            Write-Info "локальный порт $wanted занят — беру $local"
        }
        $sshArgs = @('-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                  '-o', 'BatchMode=yes', '-L', "127.0.0.1:${local}:localhost:${remote}", $SshHost)
        $proc = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -WindowStyle Hidden -PassThru
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 300
            if ($proc.HasExited) { break }
            if (Test-LocalPort $local) { $ok = $true; break }
        }
        if ($ok) {
            $alive += [pscustomobject]@{ pid = $proc.Id; local = $local; remote = $remote; host = $SshHost }
            Write-Ok "localhost:$local -> ${SshHost}:$remote"
            if ($remote -eq 8000) { Write-Info "API:     http://localhost:$local/api/v1"; Write-Info "Swagger: http://localhost:$local/api/v1/docs" }
        } else {
            if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
            Write-Bad "не удалось пробросить $local -> $remote (порт на сервере не слушается или ssh не пустил)"
        }
    }
    Save-State $alive
}

function Invoke-Down {
    $alive = Get-Alive
    if ($alive.Count -eq 0) { Write-Info 'туннелей нет'; return }
    foreach ($t in $alive) {
        Stop-Process -Id $t.pid -Force -ErrorAction SilentlyContinue
        Write-Ok "закрыт localhost:$($t.local) -> $($t.host):$($t.remote)"
    }
    Save-State @()
}

function Invoke-Status {
    $alive = Get-Alive
    if ($alive.Count -eq 0) { Write-Info 'туннелей нет  (.\tunnel.ps1 up)'; return }
    foreach ($t in $alive) {
        $state = 'не отвечает'
        if (Test-LocalPort $t.local) { $state = 'работает' }
        Write-Host ("  localhost:{0,-6} -> {1}:{2,-6} pid {3,-7} {4}" -f $t.local, $t.host, $t.remote, $t.pid, $state)
    }
}

function Get-Json([string]$uri, $headers = @{}) {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $uri -Headers $headers -TimeoutSec 30
    $ms = New-Object System.IO.MemoryStream
    $r.RawContentStream.CopyTo($ms)
    return ([System.Text.Encoding]::UTF8.GetString($ms.ToArray()) | ConvertFrom-Json)
}

function Get-ApiBase {
    $port = $ApiPort
    if ($port -eq 0) {
        $t = Get-Alive | Where-Object { $_.remote -eq 8000 } | Select-Object -First 1
        if (-not $t) {
            Write-Info 'туннель к api не поднят — поднимаю'
            $script:Forward = @('8000'); Invoke-Up
            $t = Get-Alive | Where-Object { $_.remote -eq 8000 } | Select-Object -First 1
            if (-not $t) { throw 'нет туннеля к api' }
        }
        $port = $t.local
    }
    return "http://localhost:$port/api/v1"
}

function Get-AdminCreds {
    $raw = ssh -o BatchMode=yes $SshHost "bash -c 'grep ^FAKTURA_ADMIN_ ~/$RemoteDir/.env'"
    $c = @{}
    foreach ($line in $raw) { if ($line -match '^FAKTURA_ADMIN_(EMAIL|PASSWORD)=(.*)$') { $c[$Matches[1]] = $Matches[2].Trim() } }
    if (-not $c['EMAIL'] -or -not $c['PASSWORD']) { throw 'не нашёл учётку админа в deploy/.env на сервере' }
    return $c
}

function Get-Token([string]$base) {
    $c = Get-AdminCreds
    $body = @{ email = $c['EMAIL']; password = $c['PASSWORD'] } | ConvertTo-Json -Compress
    $r = Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
    return $r.access_token
}

function Invoke-Health {
    $base = Get-ApiBase
    try {
        $h = Get-Json "$base/health"
        Write-Ok ("health: status={0} db={1} storage={2} ml={3} ({4})" -f $h.status, $h.db, $h.storage, $h.ml, $h.ml_model_version)
    } catch { Write-Bad "health: $($_.Exception.Message)" }
}

function Invoke-Smoke {
    $base = Get-ApiBase
    Invoke-Health
    $tok = Get-Token $base
    Write-Ok 'вход: токен получен'
    $hdr = @{ Authorization = "Bearer $tok" }

    $files = @($Pdf)
    if ($files.Count -eq 0) {
        $dev = Join-Path $RepoRoot 'data\caseB_kejs5\dev'
        $files = @((Join-Path $dev 'D001.pdf'), (Join-Path $dev 'D006.pdf'))
    }
    $curlArgs = @('-s', '-X', 'POST', "$base/runs", '-H', "Authorization: Bearer $tok", '-F', 'domain=solar')
    foreach ($f in $files) {
        if (-not (Test-Path $f)) { throw "нет файла $f" }
        $curlArgs += @('-F', "file=@$((Resolve-Path $f).Path)")
    }
    $prevEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    try { $created = (& curl.exe @curlArgs) -join "`n" | ConvertFrom-Json } finally { [Console]::OutputEncoding = $prevEnc }
    if (-not $created.id) { throw "POST /runs: $($created | ConvertTo-Json -Depth 5)" }
    Write-Ok "POST /runs -> $($created.id), файлов $($created.counts.files), отклонено $(@($created.rejected).Count)"

    $run = $null
    for ($i = 0; $i -lt 150; $i++) {
        $run = Get-Json "$base/runs/$($created.id)" $hdr
        if ($run.status -eq 'done' -or $run.status -eq 'error') { break }
        Start-Sleep -Seconds 2
    }
    Write-Ok "разбор: $($run.status)"
    foreach ($f in $run.files) { Write-Info ("{0,-8} {1,-8} results={2} issues={3} {4}" -f $f.id, $f.status, $f.results, $f.issues, $f.reason) }

    $first = $run.files | Where-Object { $_.status -eq 'done' } | Select-Object -First 1
    if ($first) {
        $p = Get-Json "$base/papers/solar/$($first.id)?run=$($run.id)" $hdr
        Write-Ok ("статья {0}: {1}; materials={2} devices={3} layers={4} measurements={5} results={6} issues={7}" -f `
            $first.id, $p.paper_status, @($p.materials).Count, @($p.devices).Count, @($p.layers).Count,
            @($p.measurements).Count, @($p.results).Count, @($p.issues).Count)
        $code = & curl.exe -s -o NUL -w '%{http_code}' -H "Authorization: Bearer $tok" -H 'Range: bytes=0-99' "$base/papers/solar/$($first.id)/pdf?run=$($run.id)"
        Write-Ok "PDF с Range: $code"
    }

    $zip = Join-Path $env:TEMP "faktura-$($run.id).zip"
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$base/runs/$($run.id)/submission.zip" -Headers $hdr -OutFile $zip -PassThru
    $names = @()
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    try { $names = $z.Entries | ForEach-Object { $_.FullName } } finally { $z.Dispose() }
    Write-Ok ("submission.zip: {0} CSV, X-Export-Results={1}, X-Export-Papers={2}" -f $names.Count,
        $resp.Headers['X-Export-Results'], $resp.Headers['X-Export-Papers'])
    Write-Info "сохранён: $zip"
}

function Invoke-TokenToClipboard {
    $base = Get-ApiBase
    $tok = Get-Token $base
    Set-Clipboard -Value $tok
    Write-Ok 'access-токен (15 мин) скопирован в буфер обмена — в Swagger: Authorize -> вставить'
}

switch ($Command) {
    'up' { Invoke-Up }
    'down' { Invoke-Down }
    'status' { Invoke-Status }
    'health' { Invoke-Health }
    'smoke' { Invoke-Smoke }
    'token' { Invoke-TokenToClipboard }
    'logs' {
        $svc = $Service
        ssh -o BatchMode=yes $SshHost "bash -c 'cd ~/$RemoteDir && docker compose logs --tail $Tail $svc'"
    }
    'ps' { ssh -o BatchMode=yes $SshHost "bash -c 'cd ~/$RemoteDir && docker compose ps -a'" }
}
