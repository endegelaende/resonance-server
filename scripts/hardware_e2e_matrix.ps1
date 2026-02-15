param(
    [string]$ServerUrl = "http://localhost:9000/jsonrpc.js",
    [string]$PlayerId,
    [string]$DeviceLabel = "device",
    [int[]]$TrackIds = @(),
    [int]$PollSeconds = 90,
    [double]$PollIntervalSeconds = 0.5,
    [int]$StatusWindow = 10,
    [int]$TransitionType = 1,
    [int]$TransitionDuration = 7,
    [int]$TransitionSmart = 0,
    [int]$ReplayGainMode = 1,
    [int]$NoRestartDecoder = 1,
    [Nullable[int]]$Volume = $null,
    [string]$OutputDir,
    [switch]$SkipPrefSetup,
    [switch]$SkipQueueSetup,
    [switch]$NoPlay,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PropertyValue {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }

    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) {
        return $Default
    }

    return $prop.Value
}

function Invoke-JsonRpc {
    param(
        [string]$Target,
        [object[]]$Command
    )

    $payload = @{
        id = [int](Get-Random -Minimum 100000 -Maximum 999999)
        method = "slim.request"
        params = @($Target, $Command)
    }

    $body = $payload | ConvertTo-Json -Depth 12 -Compress

    if ($DryRun) {
        Write-Host "[DRY-RUN] POST $ServerUrl :: $body" -ForegroundColor DarkGray
        return @{}
    }

    $response = Invoke-RestMethod -Uri $ServerUrl -Method Post -ContentType "application/json" -Body $body

    if ($null -eq $response) {
        throw "JSON-RPC returned no response for command: $($Command -join ' ')"
    }

    $responseError = Get-PropertyValue -Object $response -Name "error" -Default $null
    if ($null -ne $responseError) {
        $errText = ($responseError | ConvertTo-Json -Depth 8 -Compress)
        throw "JSON-RPC error for '$($Command -join ' ')': $errText"
    }

    return (Get-PropertyValue -Object $response -Name "result" -Default @{})
}

function Get-Players {
    $result = Invoke-JsonRpc -Target "-" -Command @("players", "0", "50")
    $players = @(Get-PropertyValue -Object $result -Name "players_loop" -Default @())
    return $players
}

function Resolve-Player {
    param([object[]]$Players)

    if ($Players.Count -eq 0) {
        throw "No players found. Start a player first and retry."
    }

    if (-not [string]::IsNullOrWhiteSpace($PlayerId)) {
        $match = $Players | Where-Object {
            ((Get-PropertyValue $_ "playerid" "") -eq $PlayerId) -or
            ((Get-PropertyValue $_ "name" "") -eq $PlayerId)
        } | Select-Object -First 1

        if ($null -eq $match) {
            Write-Host "Known players:" -ForegroundColor Yellow
            $Players | ForEach-Object {
                $pid = Get-PropertyValue $_ "playerid" ""
                $name = Get-PropertyValue $_ "name" ""
                Write-Host "- $name ($pid)"
            }
            throw "PlayerId '$PlayerId' not found."
        }

        return $match
    }

    if ($Players.Count -eq 1) {
        return $Players[0]
    }

    Write-Host "Multiple players found. Provide -PlayerId explicitly:" -ForegroundColor Yellow
    $Players | ForEach-Object {
        $pid = Get-PropertyValue $_ "playerid" ""
        $name = Get-PropertyValue $_ "name" ""
        Write-Host "- $name ($pid)"
    }
    throw "Cannot auto-select player."
}

function Set-PlayerPref {
    param(
        [string]$ResolvedPlayerId,
        [string]$Key,
        [string]$Value
    )

    Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("playerpref", $Key, $Value) | Out-Null
}

function Set-Volume {
    param(
        [string]$ResolvedPlayerId,
        [int]$Value
    )

    Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("mixer", "volume", [string]$Value) | Out-Null
}

function Prepare-Queue {
    param(
        [string]$ResolvedPlayerId,
        [int[]]$Ids
    )

    if ($Ids.Count -eq 0) {
        Write-Host "No TrackIds provided. Queue setup skipped." -ForegroundColor Yellow
        return
    }

    Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("playlist", "clear") | Out-Null

    $first = [string]$Ids[0]
    Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("playlistcontrol", "cmd:load", "track_id:$first") | Out-Null

    for ($i = 1; $i -lt $Ids.Count; $i++) {
        $tid = [string]$Ids[$i]
        Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("playlistcontrol", "cmd:add", "track_id:$tid") | Out-Null
    }

    if (-not $NoPlay) {
        Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("play") | Out-Null
    }
}

function Get-StatusSample {
    param([string]$ResolvedPlayerId)

    $status = Invoke-JsonRpc -Target $ResolvedPlayerId -Command @("status", "-", [string]$StatusWindow)

    $mode = [string](Get-PropertyValue -Object $status -Name "mode" -Default "")

    $timeRaw = Get-PropertyValue -Object $status -Name "time" -Default 0
    $timeSec = 0.0
    try {
        $timeSec = [double]$timeRaw
    }
    catch {
        $timeSec = 0.0
    }

    $idxRaw = Get-PropertyValue -Object $status -Name "playlist_cur_index" -Default $null
    $indexValue = $null
    if ($null -ne $idxRaw) {
        try {
            $indexValue = [int]$idxRaw
        }
        catch {
            $indexValue = [string]$idxRaw
        }
    }

    $trackId = Get-PropertyValue -Object $status -Name "playlist_cur_track_id" -Default $null

    if ($null -eq $trackId) {
        $playlistLoop = @(Get-PropertyValue -Object $status -Name "playlist_loop" -Default @())
        if ($playlistLoop.Count -gt 0 -and $indexValue -is [int]) {
            if ($indexValue -ge 0 -and $indexValue -lt $playlistLoop.Count) {
                $trackObj = $playlistLoop[$indexValue]
                $trackId = Get-PropertyValue -Object $trackObj -Name "id" -Default $null
                if ($null -eq $trackId) {
                    $trackId = Get-PropertyValue -Object $trackObj -Name "track_id" -Default $null
                }
            }
        }
    }

    return [pscustomobject]@{
        sample_time = (Get-Date).ToString("o")
        mode = $mode
        time = $timeSec
        index = $indexValue
        track_id = $trackId
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repoRoot "artifacts\hardware-e2e"
}

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

Write-Host "Fetching players from $ServerUrl ..." -ForegroundColor Cyan
$players = @(Get-Players)
$selected = Resolve-Player -Players $players
$resolvedPlayerId = [string](Get-PropertyValue -Object $selected -Name "playerid" -Default "")
$resolvedPlayerName = [string](Get-PropertyValue -Object $selected -Name "name" -Default $resolvedPlayerId)

Write-Host "Selected player: $resolvedPlayerName ($resolvedPlayerId)" -ForegroundColor Green

if (-not $SkipPrefSetup) {
    Write-Host "Applying runtime playback prefs ..." -ForegroundColor Cyan
    Set-PlayerPref -ResolvedPlayerId $resolvedPlayerId -Key "transitionType" -Value ([string]$TransitionType)
    Set-PlayerPref -ResolvedPlayerId $resolvedPlayerId -Key "transitionDuration" -Value ([string]$TransitionDuration)
    Set-PlayerPref -ResolvedPlayerId $resolvedPlayerId -Key "transitionSmart" -Value ([string]$TransitionSmart)
    Set-PlayerPref -ResolvedPlayerId $resolvedPlayerId -Key "replayGainMode" -Value ([string]$ReplayGainMode)
    Set-PlayerPref -ResolvedPlayerId $resolvedPlayerId -Key "noRestartDecoder" -Value ([string]$NoRestartDecoder)
}

if ($null -ne $Volume) {
    Write-Host "Setting volume to $Volume ..." -ForegroundColor Cyan
    Set-Volume -ResolvedPlayerId $resolvedPlayerId -Value $Volume
}

if (-not $SkipQueueSetup) {
    Write-Host "Preparing queue ..." -ForegroundColor Cyan
    Prepare-Queue -ResolvedPlayerId $resolvedPlayerId -Ids $TrackIds
}

Write-Host "Polling status for $PollSeconds seconds (interval $PollIntervalSeconds s) ..." -ForegroundColor Cyan

$samples = New-Object System.Collections.Generic.List[object]
$backsteps = New-Object System.Collections.Generic.List[object]
$modeNotPlay = 0
$indexTransitions = 0

$start = Get-Date
$deadline = $start.AddSeconds($PollSeconds)
$prev = $null
$counter = 0

while ((Get-Date) -lt $deadline) {
    $sample = Get-StatusSample -ResolvedPlayerId $resolvedPlayerId
    $samples.Add($sample)

    if ([string]$sample.mode -ne "play") {
        $modeNotPlay += 1
    }

    if ($null -ne $prev) {
        $sameTrack = ($null -ne $prev.track_id) -and ($null -ne $sample.track_id) -and ([string]$prev.track_id -eq [string]$sample.track_id)
        if ($sameTrack -and (($sample.time + 0.05) -lt $prev.time)) {
            $backsteps.Add([pscustomobject]@{
                at = $sample.sample_time
                track_id = $sample.track_id
                prev_time = $prev.time
                curr_time = $sample.time
            })
        }

        if ($null -ne $prev.index -and $null -ne $sample.index -and ([string]$prev.index -ne [string]$sample.index)) {
            $indexTransitions += 1
        }
    }

    $prev = $sample
    $counter += 1

    if (($counter % 10) -eq 0) {
        Write-Host ("  sample={0} mode={1} time={2:N2} idx={3} track={4}" -f $counter, $sample.mode, $sample.time, $sample.index, $sample.track_id)
    }

    Start-Sleep -Milliseconds ([int]([math]::Max(50, $PollIntervalSeconds * 1000.0)))
}

$end = Get-Date
$durationActual = [math]::Round(($end - $start).TotalSeconds, 3)
$firstSample = if ($samples.Count -gt 0) { $samples[0] } else { $null }
$lastSample = if ($samples.Count -gt 0) { $samples[$samples.Count - 1] } else { $null }

$summary = [pscustomobject]@{
    server_url = $ServerUrl
    device_label = $DeviceLabel
    player_name = $resolvedPlayerName
    player_id = $resolvedPlayerId
    dry_run = [bool]$DryRun
    started_at = $start.ToString("o")
    ended_at = $end.ToString("o")
    duration_seconds = $durationActual
    sample_count = $samples.Count
    backsteps_same_track = $backsteps.Count
    mode_not_play_samples = $modeNotPlay
    index_transitions = $indexTransitions
    first_sample = $firstSample
    last_sample = $lastSample
    runtime_prefs = [pscustomobject]@{
        transitionType = $TransitionType
        transitionDuration = $TransitionDuration
        transitionSmart = $TransitionSmart
        replayGainMode = $ReplayGainMode
        noRestartDecoder = $NoRestartDecoder
        volume = $Volume
    }
    track_ids = $TrackIds
}

$safeLabel = if ([string]::IsNullOrWhiteSpace($DeviceLabel)) { "device" } else { ($DeviceLabel -replace "[^A-Za-z0-9_-]", "_") }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$baseName = "$stamp-$safeLabel"
$jsonPath = Join-Path $OutputDir "$baseName.json"
$mdPath = Join-Path $OutputDir "$baseName.md"

$report = [pscustomobject]@{
    summary = $summary
    backsteps = $backsteps
    samples = $samples
}

$report | ConvertTo-Json -Depth 12 | Set-Content -Path $jsonPath -Encoding utf8

$backstepPreview = "none"
if ($backsteps.Count -gt 0) {
    $rows = @()
    foreach ($b in ($backsteps | Select-Object -First 8)) {
        $rows += "- $($b.at): track=$($b.track_id) prev=$([string]::Format('{0:N3}', [double]$b.prev_time)) curr=$([string]::Format('{0:N3}', [double]$b.curr_time))"
    }
    $backstepPreview = ($rows -join "`r`n")
}

$passBackstep = if ($backsteps.Count -eq 0) { "PASS" } else { "FAIL" }
$passMode = if ($modeNotPlay -eq 0) { "PASS" } else { "WARN" }

$markdown = @"
# Hardware E2E Result

- Device: $DeviceLabel
- Player: $resolvedPlayerName ($resolvedPlayerId)
- Server: $ServerUrl
- Started: $($start.ToString("u"))
- Duration: $durationActual s

## Runtime Prefs

- transitionType: $TransitionType
- transitionDuration: $TransitionDuration
- transitionSmart: $TransitionSmart
- replayGainMode: $ReplayGainMode
- noRestartDecoder: $NoRestartDecoder
- volume: $Volume
- track_ids: $(if ($TrackIds.Count -gt 0) { $TrackIds -join ", " } else { "(not set)" })

## Summary

- sample_count: $($samples.Count)
- backsteps_same_track: $($backsteps.Count) ($passBackstep)
- mode_not_play_samples: $modeNotPlay ($passMode)
- index_transitions: $indexTransitions

## Backsteps (same track)

$backstepPreview

## Artifacts

- json: $jsonPath
- md: $mdPath
"@

$markdown | Set-Content -Path $mdPath -Encoding utf8

Write-Host "Done." -ForegroundColor Green
Write-Host "JSON: $jsonPath"
Write-Host "MD:   $mdPath"
