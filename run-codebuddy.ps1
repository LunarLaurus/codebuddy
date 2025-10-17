# -------------------------------
# Common variables
# -------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "main.py"
$VenvDir = Join-Path $ScriptDir ".venv"

# -------------------------------
# Functions
# -------------------------------
function Activate-Venv($VenvPath) {
    $activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
    if (-Not (Test-Path $activateScript)) {
        Write-Host "No virtual environment found at '$VenvPath'. Creating..."
        python -m venv $VenvPath
        $activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
    }

    Write-Host "Activating virtual environment: $VenvPath"
    & $activateScript
}

# -------------------------------
# Main
# -------------------------------
Activate-Venv $VenvDir

# Upgrade pip and install requirements if present
$reqFile = Join-Path $ScriptDir "requirements.txt"
if (Test-Path $reqFile) {
    python -m pip install --upgrade pip
    python -m pip install -r $reqFile
}

# Verify python exists
if (-Not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found in virtual environment at '$VenvDir'."
    exit 1
}

# Launch script
Write-Host "Launching script: $PythonScript"
python $PythonScript
