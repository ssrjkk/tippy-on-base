param(
    [switch]$Once,
    [switch]$Check,
    [switch]$AsLibrary,
    [string]$Python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    [string]$Cloudflared = "$PSScriptRoot\cloudflared.exe"
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $Repo 'data\runtime'
$Health = 'http://127.0.0.1:8000/api/health'
$PublicOrigin = 'https://tippy-egi.pages.dev'
$PublicHealth = "$PublicOrigin/api/health"
# The witness has to fail for the reason the measurement fails, so it lives on the
# hostname being measured and on a path the edge answers without this host: ASSET in
# `functions/[[path]].js` matches `.css` and returns the bundle file before the
# origin is asked anything — so a 200 proves this machine finished a TLS handshake
# with Pages, and a silence proves it did not, while the tunnel, the app and the
# database decide nothing about either answer.
#
# Measured with the supervisor's own question — is the site up? — while the origin
# answered every loopback request: TCP to both `tippy-egi.pages.dev` addresses
# (172.66.47.169, 172.66.44.87) opened in 9-33 ms, and every HTTPS request to that
# hostname then died inside the handshake (curl `Recv failure: Connection was reset`,
# `schannel: failed to receive handshake`), with bare `pages.dev` the same, while
# `www.cloudflare.com/cdn-cgi/trace` answered 200 in 0.05 s from a different address
# block (104.16.x). A control on that other block is what made the first version of
# this leg answer `down` about a site this host cannot complete a handshake with: it
# witnessed egress to Cloudflare, not egress to Pages.
$PublicControl = "$PublicOrigin/fonts.css"

# Two connector processes, each with its own edge connections and its own
# metrics port, because one process losing every connection is a page that
# answers the maintenance card. Measured on this host with one replica: the
# public health endpoint answered 200 for 6 of 19 probes (32%) while
# `127.0.0.1:20241/ready` answered 200 for 10 (53%) and the origin answered in
# 0.24 s on loopback the whole time. With a second replica the same walk read
# 9 of 20 (45%), including three samples where the first replica had no
# connection at all and the page still served.
$ConnectorReplicas = 2
$MetricsPortBase = 20241

# A connector that started four seconds ago has no connections yet by
# definition. Killing one for that is the watchdog manufacturing the outage it
# is meant to heal, so the first $StartupGraceSeconds are never a verdict.
$StartupGraceSeconds = 90
# The origin is a different animal: `run.py` runs the alembic chain before it
# binds a socket, and a contended lock can hold it there for minutes. Ten
# minutes of answering nothing on loopback is not a slow boot, it is the wedged
# process this host has actually produced (`WinError 64` on a pipe, uvicorn
# alive, every request into a black hole) — and while it sits there the tunnel
# has nothing to carry and every visitor reads the card.
$OriginGraceSeconds = 600
# `/ready` 503 is one sample of one instant, and connection sets turn over
# every minute or two on this host even when the page is serving. A replica is
# replaced only when every probe in this window failed.
$ConfirmProbes = 3
$ConfirmDelaySeconds = 15
# One replica is replaced per pass, deliberately. The pair exists so that at least
# one connector always holds a candidate edge connection; stopping both in one pass
# sends every connection through registration at the same moment, over the same
# egress that had just refused them. Measured in this host's own log: in the
# 00:08-00:22 window of 2026-11-18 four passes each stopped one replica and then
# the other about 40 s later, and `public=down` was written after every one of
# them, while the single replacement at 00:25:29 had the public health endpoint
# answering 200 five seconds later.
$MaxConnectorReplacementsPerPass = 1
# After starting or keeping replicas, wait for the pair to answer before
# judging the public path, so a pass that healed the tunnel reports the healed
# state instead of the hole it just filled.
$ReadyWaitSeconds = 45
# The public probe is the same measurement with the same problem: one request
# through one edge POP says nothing about the site.
$PublicProbes = 3
$PublicProbeDelaySeconds = 5

function Get-MetricsPorts {
    @(1..$ConnectorReplicas | ForEach-Object { $MetricsPortBase + $_ - 1 })
}

function Get-ConnectorLog([int]$Port) {
    Join-Path $Runtime ('cloudflared-{0}.log' -f $Port)
}

function Get-ReadyUrl([int]$Port) {
    'http://127.0.0.1:{0}/ready' -f $Port
}

# The whole restart rule, as an answer to evidence rather than as a walk past
# live processes: given how old a replica is and what its readiness probes
# said, should it be replaced? `tests/test_watchdog.py` runs this function
# against a truth table, because a rule nobody executes is a rule that can say
# anything and keep passing.
#
# A probe is one of three answers, and the third is the only one that condemns:
# `ok` (the endpoint said yes), `answered` (something is there and it refused —
# a connector with no edge yet, a database the app cannot reach) and `silent`
# (nothing answered at the HTTP layer at all). Replacing a process that is
# answering 503 is the healing; replacing one whose *database* is down is
# killing a working bot over a dependency it cannot fix.
function Test-ConnectorShouldRestart {
    param(
        [int]$AgeSeconds,
        [string[]]$ProbeOutcomes
    )
    if ($ProbeOutcomes.Count -lt 2) {
        throw 'a restart verdict needs at least two readiness probes'
    }
    if ($AgeSeconds -lt $StartupGraceSeconds) { return $false }
    foreach ($outcome in $ProbeOutcomes) {
        if ($outcome -eq 'ok') { return $false }
    }
    return $true
}

# The origin is replaced only when the HTTP layer never answered in the whole
# window. An `answered` — 200 with a failing db, a 5xx, a body that is not JSON
# — is a fault the process itself is reporting, and a restart hides the report
# without removing the cause.
function Test-OriginShouldRestart {
    param(
        [int]$AgeSeconds,
        [string[]]$ProbeOutcomes
    )
    if ($ProbeOutcomes.Count -lt $ConfirmProbes) {
        throw "an origin verdict needs all $ConfirmProbes health probes"
    }
    if ($AgeSeconds -lt $OriginGraceSeconds) { return $false }
    foreach ($outcome in $ProbeOutcomes) {
        if ($outcome -ne 'silent') { return $false }
    }
    return $true
}

# The public leg is the same measurement with one more failure mode: the site can
# be up while this host cannot see it at all. `answered` still condemns the site —
# an HTTP answer from the public hostname is a measurement, whatever the route to
# the rest of Cloudflare looks like — and so does a control that answered anything
# at all: a 404 from the bundle proves this host reached Pages, and then a silent
# health path is the fault the card is standing in for. Only a leg where nothing
# answered and the control answered nothing either is reported as not measured.
function Get-PublicOutcome {
    param(
        [string]$ControlOutcome,
        [string[]]$HealthOutcomes
    )
    if ($HealthOutcomes.Count -lt 1) {
        throw 'a public verdict needs at least one health probe'
    }
    if ($ControlOutcome -notin $EndpointOutcomes) {
        throw "unknown control outcome '$ControlOutcome'"
    }
    foreach ($outcome in $HealthOutcomes) {
        if ($outcome -eq 'ok') { return 'ok' }
    }
    foreach ($outcome in $HealthOutcomes) {
        if ($outcome -eq 'answered') { return 'down' }
    }
    if ($ControlOutcome -ne 'silent') { return 'down' }
    return 'unreachable'
}

# A pass is green when the two things this host owns answer. The public leg joins
# only when it was actually measured: `unreachable` is not a healed fault, but it
# is not a fault of this machine's either, and a scheduled task that exits 1 every
# few minutes forever says nothing on the occasion it should.
function Test-Readiness {
    param(
        [bool]$OriginReady,
        [bool]$ConnectorReady,
        [string]$PublicOutcome
    )
    if ($PublicOutcome -notin $PublicOutcomes) {
        throw "unknown public outcome '$PublicOutcome'"
    }
    return ($OriginReady -and $ConnectorReady -and ($PublicOutcome -ne 'down'))
}

function Get-PublicProbeOutcome {
    $health = @()
    for ($i = 0; $i -lt $PublicProbes; $i++) {
        if ($i -gt 0) { Start-Sleep -Seconds $PublicProbeDelaySeconds }
        $outcome = Get-EndpointOutcome $PublicHealth -Application
        $health += $outcome
        if ($outcome -eq 'ok') { break }
    }
    return (Get-PublicOutcome -ControlOutcome (Get-EndpointOutcome $PublicControl) -HealthOutcomes $health)
}

# The three answers `Get-EndpointOutcome` can give, named once so a predicate
# refuses a typo instead of reading it as a silent probe.
$EndpointOutcomes = @('ok', 'answered', 'silent')

# The three answers the public leg can give, named once for the same reason: the
# function that produces the verdict and the rule that composes the exit code read
# this one list, so a fourth answer cannot be returned by one and refused by the
# other. `down` is a measured failure of the site; `unreachable` is this host
# having no route to look, which is not evidence about a visitor's page.
$PublicOutcomes = @('ok', 'down', 'unreachable')

function Get-Connector {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($Runtime)
    }
}

function Get-ConnectorForPort([int]$Port) {
    $pattern = '--metrics\s+127\.0\.0\.1:{0}(?:\s|$)' -f $Port
    @(Get-Connector | Where-Object { $_.CommandLine -match $pattern })
}

function Get-Origin {
    $entry = Join-Path $PSScriptRoot 'run.py'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -and ($_.CommandLine.Contains($entry) -or $_.CommandLine -match '(?:^|\s)deploy[/\\]run\.py(?:\s|$)')
    }
}

function Get-EndpointOutcome([string]$Url, [switch]$Application) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
        if ($r.StatusCode -ne 200) { return 'answered' }
        if (-not $Application) { return 'ok' }
        $body = $r.Content | ConvertFrom-Json
        if ($body.status -eq 'ok' -and $body.db -eq 'ok') { return 'ok' }
        return 'answered'
    } catch {
        if ($_.Exception.Response -or $_.Exception.StatusCode) { return 'answered' }
        return 'silent'
    }
}

function Test-Endpoint([string]$Url, [switch]$Application) {
    return (Get-EndpointOutcome $Url -Application:$Application) -eq 'ok'
}

function Write-Log([string]$Message) {
    Add-Content -Path (Join-Path $Runtime 'watchdog.log') -Value ("{0:o} {1}" -f (Get-Date), $Message) -Encoding UTF8
    Write-Output $Message
}

function Stop-Connector($Connector, [string]$Reason) {
    Stop-Process -Id $Connector.ProcessId
    Wait-Process -Id $Connector.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    Write-Log ("Stopped connector {0}: {1}" -f $Connector.ProcessId, $Reason)
}

function Start-Origin {
    Start-Process -FilePath $Python -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList @('-u', ('"{0}"' -f (Join-Path $PSScriptRoot 'run.py'))) -RedirectStandardOutput (Join-Path $Runtime 'origin.out.log') -RedirectStandardError (Join-Path $Runtime 'origin.err.log') | Out-Null
}

function Stop-Origin($Process, [string]$Reason) {
    Stop-Process -Id $Process.ProcessId
    Wait-Process -Id $Process.ProcessId -Timeout 30 -ErrorAction SilentlyContinue
    Write-Log ("Stopped origin {0}: {1}" -f $Process.ProcessId, $Reason)
}

function Start-Connector([int]$Port) {
    $line = Get-Content (Join-Path $Repo '.env') | Where-Object { $_ -match '^CLOUDFLARE_TUNNEL_TOKEN=' } | Select-Object -First 1
    if (-not $line) { throw 'Tunnel credential is absent.' }
    $token = $line.Substring('CLOUDFLARE_TUNNEL_TOKEN='.Length).Trim().Trim('"').Trim("'")
    if (-not $token) { throw 'Tunnel credential is empty.' }
    $previous = $env:TUNNEL_TOKEN
    try {
        $env:TUNNEL_TOKEN = $token
        # http2, not auto: this host's VPN/TUN interface answers the QUIC
        # precheck with PASS and then kills the long-lived UDP streams, so an
        # auto connector registers four connections, loses them on 'no recent
        # network activity' and leaves /ready at 503 forever. Since the pin,
        # cloudflared has logged zero QUIC errors on this host.
        #
        # No --edge-bind-address, deliberately. The flag named the Wi-Fi
        # address this host already sends every tunnel connection from — 82 of
        # 82 established :443 sockets on it at the time this was measured — so
        # it selected nothing, while a connector that carried it lost its edge
        # connections every 22-173 s (median 101 s, 49 deaths in 30 min) and
        # the same version without it registered four and terminated none in
        # the 10 minutes the comparison ran.
        Start-Process -FilePath $Cloudflared -WindowStyle Hidden -ArgumentList @(
            'tunnel', '--no-autoupdate', '--protocol', 'http2',
            '--metrics', ('127.0.0.1:{0}' -f $Port),
            '--logfile', ('"{0}"' -f (Get-ConnectorLog $Port)), 'run'
        ) | Out-Null
    } finally { $env:TUNNEL_TOKEN = $previous }
    Write-Log ("Started connector replica on 127.0.0.1:{0}" -f $Port)
}

function Wait-ForConnector([int[]]$Ports, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($port in $Ports) {
            if (Test-Endpoint (Get-ReadyUrl $port)) { return $true }
        }
        if ((Get-Date) -ge $deadline) { return $false }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Repair-Connectors([int[]]$Ports, [int[]]$AlreadyReplaced = @()) {
    $replaced = @()
    foreach ($port in $Ports) {
        $running = @(Get-ConnectorForPort $port)
        if ($running.Count -gt 1) {
            foreach ($extra in $running | Select-Object -Skip 1) {
                Stop-Connector $extra 'a second process cannot own one metrics port'
            }
            $running = @($running[0])
        }
        if ($running.Count -eq 0) {
            Start-Connector $port
            continue
        }
        if ($AlreadyReplaced -contains $port) { continue }
        $connector = $running[0]
        $age = [int]((Get-Date) - $connector.CreationDate).TotalSeconds
        if ($age -lt $StartupGraceSeconds) { continue }
        $probes = @()
        for ($i = 0; $i -lt $ConfirmProbes; $i++) {
            if ($i -gt 0) { Start-Sleep -Seconds $ConfirmDelaySeconds }
            $probes += (Get-EndpointOutcome (Get-ReadyUrl $port))
        }
        if (Test-ConnectorShouldRestart -AgeSeconds $age -ProbeOutcomes $probes) {
            if ($replaced.Count -ge $MaxConnectorReplacementsPerPass) {
                Write-Log ("Deferred replica on 127.0.0.1:{0}: this pass already replaced {1} connector(s), and a pair with both halves mid-registration serves nobody" -f $port, $replaced.Count)
                continue
            }
            Stop-Connector $connector ("{0} readiness probes in a row answered no edge connection" -f $ConfirmProbes)
            Start-Connector $port
            $replaced += $port
        }
    }
    return $replaced
}

# The budget holds for as long as some replica is serving. When the wait expires
# with nothing ready, the deferred replica is not a working half of the pair — it
# is a second dead process, and leaving it dead costs the page another whole pass
# interval. Measured on this host in the minutes after the budget was introduced:
# passes wrote `connector=False public=down` and the next repair came 3-4 minutes
# later, while the replica that had just been replaced was still registering.
function Repair-ConnectorsUntilServing([int[]]$Ports) {
    $replaced = @(Repair-Connectors -Ports $Ports)
    if (Wait-ForConnector -Ports $Ports -Seconds $ReadyWaitSeconds) { return $true }
    if ($replaced.Count -eq 0) { return $false }
    Write-Log ("No replica answered within {0}s after replacing {1}; the rest of the pair is serving nothing either, so this pass replaces it now instead of on the next one." -f $ReadyWaitSeconds, ($replaced -join '/'))
    $replaced = @($replaced + @(Repair-Connectors -Ports $Ports -AlreadyReplaced $replaced))
    return (Wait-ForConnector -Ports $Ports -Seconds $ReadyWaitSeconds)
}


function Ensure-Services {
    $originOutcome = Get-EndpointOutcome $Health -Application
    if ($originOutcome -ne 'ok') {
        $origin = @(Get-Origin)
        if ($origin.Count -eq 0) {
            Start-Origin
            Write-Log 'Started origin; readiness will be checked on the next pass.'
        } else {
            $age = [int]((Get-Date) - $origin[0].CreationDate).TotalSeconds
            $outcomes = @($originOutcome)
            for ($i = $outcomes.Count; $i -lt $ConfirmProbes; $i++) {
                Start-Sleep -Seconds $ConfirmDelaySeconds
                $outcomes += (Get-EndpointOutcome $Health -Application)
            }
            if (Test-OriginShouldRestart -AgeSeconds $age -ProbeOutcomes $outcomes) {
                Stop-Origin $origin[0] ("{0} health probes in a row answered nothing" -f $ConfirmProbes)
                try {
                    Start-Origin
                    Write-Log 'Started origin after a silent-process verdict.'
                } catch {
                    Write-Log ("Origin is down and could not be restarted: {0}" -f $_.Exception.Message)
                }
            } else {
                Write-Log ("Origin answered '{0}' at {1}s old; preserved the process and database." -f $originOutcome, $age)
            }
        }
    }

    $connectorReady = Repair-ConnectorsUntilServing (Get-MetricsPorts)
    $originReady = Test-Endpoint $Health -Application
    $publicOutcome = Get-PublicProbeOutcome
    Write-Log ("origin={0} connector={1} public={2} replicas={3}" -f $originReady, $connectorReady, $publicOutcome, (@(Get-Connector).Count))
    return (Test-Readiness -OriginReady $originReady -ConnectorReady $connectorReady -PublicOutcome $publicOutcome)
}

# Dot-sourced by `tests/test_watchdog.py`, which wants the rules above and not
# a second supervisor writing to the live tunnel.
if ($AsLibrary) { return }

if ($Check) {
    $ports = Get-MetricsPorts
    $readyPorts = @($ports | Where-Object { Test-Endpoint (Get-ReadyUrl $_) })
    # One probe per fact. The origin used to be asked twice — once for the boolean
    # and once for the outcome — and the two answers in one report could disagree.
    $originOutcome = Get-EndpointOutcome $Health -Application
    $publicOutcome = Get-PublicProbeOutcome
    $state = [ordered]@{
        OriginReady = ($originOutcome -eq 'ok')
        OriginOutcome = $originOutcome
        ConnectorReady = ($readyPorts.Count -gt 0)
        PublicOutcome = $publicOutcome
        PublicReady = ($publicOutcome -eq 'ok')
        Connectors = @(Get-Connector).Count
        Origins = @(Get-Origin).Count
        ReplicaPorts = @($ports)
        ReadyPorts = $readyPorts
    }
    $state | ConvertTo-Json
    exit 0
}
if (-not $Once) { throw 'Use -Once from Task Scheduler or -Check for a read-only probe.' }
New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
$ready = @(Ensure-Services)
$ready | Where-Object { $_ -is [string] } | ForEach-Object { Write-Output $_ }
if ($ready[-1] -eq $true) { exit 0 }
exit 1
