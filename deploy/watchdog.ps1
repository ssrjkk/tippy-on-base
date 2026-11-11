param(
    [switch]$Once,
    [switch]$Check,
    [string]$Python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    [string]$Cloudflared = "$PSScriptRoot\cloudflared.exe"
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $Repo 'data\runtime'
$CfLog = Join-Path $Runtime 'cloudflared.log'
$Health = 'http://127.0.0.1:8000/api/health'
$ConnectorHealth = 'http://127.0.0.1:20241/ready'
$PublicHealth = 'https://tippy-egi.pages.dev/api/health'

function Get-EdgeAddress {
    $physical = @(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -ExpandProperty InterfaceIndex)
    $routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Where-Object { $_.InterfaceIndex -in $physical } | Sort-Object { $_.RouteMetric + $_.InterfaceMetric })
    foreach ($route in $routes) {
        $ip = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 | Where-Object {
            $_.AddressState -eq 'Preferred' -and $_.IPAddress -notlike '169.254.*'
        } | Select-Object -First 1 -ExpandProperty IPAddress
        if ($ip) { return $ip }
    }
    throw 'No active physical default route; connector was not started.'
}

function Get-Connector {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($CfLog)
    }
}

function Get-Origin {
    $entry = Join-Path $PSScriptRoot 'run.py'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -and ($_.CommandLine.Contains($entry) -or $_.CommandLine -match '(?:^|\s)deploy[/\\]run\.py(?:\s|$)')
    }
}

function Test-Endpoint([string]$Url, [switch]$Application) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
        if ($r.StatusCode -ne 200) { return $false }
        if ($Application) {
            $body = $r.Content | ConvertFrom-Json
            return $body.status -eq 'ok' -and $body.db -eq 'ok'
        }
        return $true
    } catch { return $false }
}

function Write-Log([string]$Message) {
    Add-Content -Path (Join-Path $Runtime 'watchdog.log') -Value ("{0:o} {1}" -f (Get-Date), $Message) -Encoding UTF8
    Write-Output $Message
}

function Start-Connector([string]$Address) {
    $line = Get-Content (Join-Path $Repo '.env') | Where-Object { $_ -match '^CLOUDFLARE_TUNNEL_TOKEN=' } | Select-Object -First 1
    if (-not $line) { throw 'Tunnel credential is absent.' }
    $token = $line.Substring('CLOUDFLARE_TUNNEL_TOKEN='.Length).Trim().Trim('"').Trim("'")
    if (-not $token) { throw 'Tunnel credential is empty.' }
    $previous = $env:TUNNEL_TOKEN
    try {
        $env:TUNNEL_TOKEN = $token
        Start-Process -FilePath $Cloudflared -WindowStyle Hidden -ArgumentList @(
            'tunnel', '--no-autoupdate', '--protocol', 'auto',
            '--edge-bind-address', $Address, '--metrics', '127.0.0.1:20241',
            '--logfile', ('"{0}"' -f $CfLog), 'run'
        ) | Out-Null
    } finally { $env:TUNNEL_TOKEN = $previous }
    Write-Log "Started named connector bound to $Address"
}

function Ensure-Services {
    if (-not (Test-Endpoint $Health -Application)) {
        if (@(Get-Origin).Count -eq 0) {
            Start-Process -FilePath $Python -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList @('-u', ('"{0}"' -f (Join-Path $PSScriptRoot 'run.py'))) -RedirectStandardOutput (Join-Path $Runtime 'origin.out.log') -RedirectStandardError (Join-Path $Runtime 'origin.err.log') | Out-Null
            Write-Log 'Started origin; readiness will be checked on the next pass.'
        } else {
            Write-Log 'Origin is running but not ready; preserved the process and database.'
        }
    }

    $address = Get-EdgeAddress
    $connectors = @(Get-Connector)
    foreach ($connector in $connectors) {
        $wrongRoute = $connector.CommandLine -notmatch ('--edge-bind-address\s+' + [regex]::Escape($address) + '(?:\s|$)')
        $oldAndDisconnected = ((Get-Date) - $connector.CreationDate).TotalMinutes -ge 5 -and -not (Test-Endpoint $ConnectorHealth)
        $wrongBinary = -not $connector.CommandLine.Contains($Cloudflared)
        if ($wrongRoute -or $oldAndDisconnected -or $wrongBinary) {
            Stop-Process -Id $connector.ProcessId
            Wait-Process -Id $connector.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
            Write-Log "Stopped Tippy connector $($connector.ProcessId) for route or readiness recovery."
        }
    }
    if (@(Get-Connector).Count -eq 0) { Start-Connector $address }

    $originReady = Test-Endpoint $Health -Application
    $connectorReady = Test-Endpoint $ConnectorHealth
    $publicReady = Test-Endpoint $PublicHealth -Application
    Write-Log "origin=$originReady connector=$connectorReady public=$publicReady"
    return ($originReady -and $connectorReady -and $publicReady)
}

if ($Check) {
    [pscustomobject]@{
        EdgeAddress = Get-EdgeAddress
        OriginReady = Test-Endpoint $Health -Application
        ConnectorReady = Test-Endpoint $ConnectorHealth
        PublicReady = Test-Endpoint $PublicHealth -Application
        Connectors = @(Get-Connector).Count
    } | ConvertTo-Json
    exit 0
}
if (-not $Once) { throw 'Use -Once from Task Scheduler or -Check for a read-only probe.' }
New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
$ready = @(Ensure-Services)
$ready | Where-Object { $_ -is [string] } | ForEach-Object { Write-Output $_ }
if ($ready[-1] -eq $true) { exit 0 }
exit 1
