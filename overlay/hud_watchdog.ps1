$ErrorActionPreference = 'SilentlyContinue'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$overlay = Join-Path $here 'hud_overlay.py'
$profile = Join-Path $env:USERPROFILE '.dsh\profiles\desktop\cordis.patch.yml'
$stateDir = Join-Path $env:LOCALAPPDATA 'dsh-hud'
$log = Join-Path $stateDir 'watchdog.log'
$disabled = Join-Path $stateDir 'watchdog-disabled'

$mutex = New-Object System.Threading.Mutex($false, 'Local\DSH-HUD-Watchdog')
if (-not $mutex.WaitOne(0)) { exit 0 }

function Write-Log([string]$message) {
  try {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 200KB)) {
      Move-Item -LiteralPath $log -Destination "$log.1" -Force
    }
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message)
    [IO.File]::AppendAllText($log, $line + "`r`n", (New-Object Text.UTF8Encoding($false)))
  } catch { }
}

function Ensure-ProfileEntry {
  if (-not (Test-Path $profile)) { return }
  try {
    $text = [IO.File]::ReadAllText($profile)
    if ($text -match 'ui-floating-hud') { return }
    $block = @"

# Floating execution HUD (desktop overlay)
- insert:
    - id: ui-floating-hud
      name: "dsh-client-ui-hud"
"@
    [IO.File]::AppendAllText($profile, $block, (New-Object Text.UTF8Encoding($false)))
    Write-Log 'profile entry restored'
  } catch {
    Write-Log ("profile append failed: " + $_.Exception.Message)
  }
}

function Get-Python {
  $bundled = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe'
  if (Test-Path $bundled) { return $bundled }
  return 'pythonw.exe'
}

Write-Log ("watchdog started pid=" + $PID)

while (-not (Test-Path $disabled)) {
  Ensure-ProfileEntry

  $dsh = Get-Process | Where-Object {
    $_.ProcessName -eq 'DeepSeek Harness' -and $_.MainWindowTitle
  } | Select-Object -First 1

  if ($dsh) {
    $listening = Get-NetTCPConnection -LocalPort 38471 -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
      try {
        $python = Get-Python
        Start-Process -FilePath $python -ArgumentList @(
          $overlay, '--host-pid', $dsh.Id, '--port', '38471'
        )
        Write-Log ("overlay started hostPid=" + $dsh.Id)
      } catch {
        Write-Log ("overlay start failed: " + $_.Exception.Message)
      }
    }
  }

  Start-Sleep -Seconds 20
}

Write-Log 'watchdog stopped (disabled marker found)'
$mutex.ReleaseMutex()
$mutex.Dispose()
