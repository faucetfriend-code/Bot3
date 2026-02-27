# kill_port_8000.ps1
# Script to identify and shut down processes using port 8000 on Windows

param(
    [switch]$Force  # Optional parameter to force kill without confirmation
)

# Function to display usage
function Show-Usage {
    Write-Host "Usage: .\kill_port_8000.ps1 [-Force]"
    Write-Host "  -Force: Kill processes without confirmation"
    exit 1
}

# Function to get process information
function Get-PortProcesses {
    try {
        # Run netstat to find processes on port 8000
        $netstatOutput = netstat -ano | Select-String ":8000"
        
        if (-not $netstatOutput) {
            Write-Host "No processes found using port 8000."
            return @()
        }
        
        $processes = @()
        
        foreach ($line in $netstatOutput) {
            # Parse the line: TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       1234
            $parts = $line -split '\s+'
            $pid = $parts[-1]
            
            # Get process details
            $processInfo = tasklist /FI "PID eq $pid" /FO CSV | ConvertFrom-Csv
            if ($processInfo) {
                $processes += [PSCustomObject]@{
                    PID = $pid
                    Name = $processInfo.'Image Name'
                    Memory = $processInfo.'Mem Usage'
                }
            }
        }
        
        return $processes
    }
    catch {
        Write-Error "Error retrieving process information: $_"
        return @()
    }
}

# Function to kill processes
function Kill-Processes {
    param([array]$processes)
    
    foreach ($proc in $processes) {
        Write-Host "Attempting to kill process: $($proc.Name) (PID: $($proc.PID))"
        
        try {
            # Try graceful kill first
            taskkill /PID $proc.PID /T 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Successfully terminated process $($proc.Name) (PID: $($proc.PID))"
            } else {
                # If graceful fails, try force kill
                Write-Warning "Graceful termination failed, attempting force kill..."
                taskkill /PID $proc.PID /F /T 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "Force killed process $($proc.Name) (PID: $($proc.PID))"
                } else {
                    Write-Error "Failed to kill process $($proc.Name) (PID: $($proc.PID)). You may need elevated privileges."
                }
            }
        }
        catch {
            Write-Error "Error killing process $($proc.Name) (PID: $($proc.PID)): $_"
        }
    }
}

# Main script logic
$processes = Get-PortProcesses

if ($processes.Count -eq 0) {
    exit 0
}

Write-Host "Processes using port 8000:"
$processes | Format-Table -AutoSize

if (-not $Force) {
    $confirmation = Read-Host "Do you want to kill these processes? (y/N)"
    if ($confirmation -ne 'y' -and $confirmation -ne 'Y') {
        Write-Host "Operation cancelled."
        exit 0
    }
}

Kill-Processes -processes $processes

# Verify port is free
Start-Sleep -Seconds 2
$verify = netstat -ano | Select-String ":8000"
if ($verify) {
    Write-Warning "Some processes may still be using port 8000. You may need to run as Administrator."
} else {
    Write-Host "Port 8000 is now free."
}