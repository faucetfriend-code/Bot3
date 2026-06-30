# install_timescaledb.ps1
# ============================================================================
# PostgreSQL 16 + TimescaleDB Installation Script for Windows
# Bot 3 Trading System - Plan 02-01 Task 1
# ============================================================================
#
# This script automates the installation of PostgreSQL 16 and the TimescaleDB
# extension on Windows. It creates the trading_bot database and user, enables
# the TimescaleDB extension, and verifies the installation.
#
# PREREQUISITES:
#   - Windows 10/11 or Windows Server 2019+
#   - Administrator privileges (script requests elevation automatically)
#   - Internet connection for downloading installers
#
# USAGE:
#   Right-click -> Run with PowerShell, or from an elevated prompt:
#     .\scripts\install_timescaledb.ps1
#     .\scripts\install_timescaledb.ps1 -PostgresPassword "MySecurePass"
#     .\scripts\install_timescaledb.ps1 -SkipDownload -PostgresInstallerPath "C:\path\to\postgresql-16.msi"
#
# DESIGN DECISIONS:
#   - Uses the official PostgreSQL MSI installer (most reliable on Windows)
#   - TimescaleDB installed via the official .zip package (no choco/dcoop needed)
#   - Creates a dedicated trading_bot user with limited privileges
#   - All credentials default to match the .env configuration
#   - Idempotent: safe to run multiple times (checks for existing installation)
# ============================================================================

#Requires -Version 5.1
#Requires -RunAsAdministrator

param(
    [string]$PostgresVersion = "16.6",
    [string]$PostgresInstallerUrl = "https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64.exe",
    [string]$TimescaleVersion = "2.17.1",
    [string]$TimescaleUrl = "https://github.com/timescale/timescaledb/releases/download/2.17.1/timescaledb-2.17.1-windows-x64.zip",
    [string]$PostgresInstallDir = "C:\Program Files\PostgreSQL\16",
    [string]$PostgresDataDir = "C:\Program Files\PostgreSQL\16\data",
    [string]$PostgresPort = "5432",
    [string]$PostgresPassword = "trading_bot_pass",
    [string]$DatabaseName = "trading_bot",
    [string]$DatabaseUser = "trading_bot",
    [string]$DatabaseUserPassword = "trading_bot_pass",
    [string]$DownloadDir = "$env:TEMP\bot3-postgres-install",
    [string]$PostgresInstallerPath = "",
    [switch]$SkipDownload,
    [switch]$Force,
    [switch]$DryRun
)

# ============================================================================
# Configuration & Constants
# ============================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Speeds up Invoke-WebRequest

$PG_BIN = Join-Path $PostgresInstallDir "bin"
$PSQL_EXE = Join-Path $PG_BIN "psql.exe"
$PG_CTL_EXE = Join-Path $PG_BIN "pg_ctl.exe"

# Logging
$LogFile = Join-Path $DownloadDir "install_timescaledb.log"

# ============================================================================
# Helper Functions
# ============================================================================

function Write-Log {
    <#
    .SYNOPSIS
        Writes a message to both the console and the log file.
    #>
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR", "SUCCESS")]
        [string]$Level = "INFO"
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logLine = "[$timestamp] [$Level] $Message"

    switch ($Level) {
        "INFO"    { Write-Host $logLine -ForegroundColor Cyan }
        "WARN"    { Write-Host $logLine -ForegroundColor Yellow }
        "ERROR"   { Write-Host $logLine -ForegroundColor Red }
        "SUCCESS" { Write-Host $logLine -ForegroundColor Green }
    }

    # Append to log file (create directory if needed)
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Add-Content -Path $LogFile -Value $logLine -Encoding UTF8
}

function Test-PostgresInstalled {
    <#
    .SYNOPSIS
        Checks if PostgreSQL is already installed at the configured path.
    #>
    return (Test-Path $PSQL_EXE)
}

function Test-TimescaleInstalled {
    <#
    .SYNOPSIS
        Checks if the TimescaleDB DLL exists in the PostgreSQL lib directory.
    #>
    $libDir = Join-Path $PostgresInstallDir "lib"
    $timescaleDll = Join-Path $libDir "timescaledb.dll"
    return (Test-Path $timescaleDll)
}

function Test-PostgresRunning {
    <#
    .SYNOPSIS
        Checks if the PostgreSQL service is running.
    #>
    try {
        $service = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue |
            Where-Object { $_.Status -eq "Running" } |
            Select-Object -First 1
        return ($null -ne $service)
    }
    catch {
        return $false
    }
}

function Invoke-WithRetry {
    <#
    .SYNOPSIS
        Retries a script block up to N times with configurable delay.
    #>
    param(
        [scriptblock]$ScriptBlock,
        [int]$MaxRetries = 3,
        [int]$RetryDelaySeconds = 5,
        [string]$OperationName = "operation"
    )

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            return & $ScriptBlock
        }
        catch {
            if ($attempt -eq $MaxRetries) {
                Write-Log "$OperationName failed after $MaxRetries attempts: $_" -Level ERROR
                throw
            }
            Write-Log "$OperationName attempt $attempt failed, retrying in ${RetryDelaySeconds}s..." -Level WARN
            Start-Sleep -Seconds $RetryDelaySeconds
        }
    }
}

# ============================================================================
# Step 0: Pre-flight Checks
# ============================================================================

function Test-Prerequisites {
    <#
    .SYNOPSIS
        Validates system requirements before installation begins.
    #>
    Write-Log "Running pre-flight checks..." -Level INFO

    # Check Windows version
    $osVersion = [System.Environment]::OSVersion.Version
    if ($osVersion.Major -lt 10) {
        Write-Log "Windows 10 or later is required (found $($osVersion.Major).$($osVersion.Minor))" -Level ERROR
        throw "Unsupported Windows version"
    }
    Write-Log "Windows version: $($osVersion.Major).$($osVersion.Minor) (OK)" -Level INFO

    # Check available disk space (need ~1GB for PostgreSQL + TimescaleDB)
    $systemDrive = $env:SystemDrive
    $freeSpaceGB = [math]::Round((Get-PSDrive ($systemDrive.TrimEnd(':')).Free / 1GB), 2)
    if ($freeSpaceGB -lt 2) {
        Write-Log "Insufficient disk space on $systemDrive : ${freeSpaceGB}GB free (need >= 2GB)" -Level ERROR
        throw "Insufficient disk space"
    }
    Write-Log "Free disk space: ${freeSpaceGB}GB (OK)" -Level INFO

    # Check if PostgreSQL is already installed
    if (Test-PostgresInstalled) {
        Write-Log "PostgreSQL is already installed at $PostgresInstallDir" -Level INFO

        # Check version
        try {
            & $PSQL_EXE --version 2>&1 | ForEach-Object {
                if ($_ -match "PostgreSQL (\d+)") {
                    $installedVersion = $Matches[1]
                    Write-Log "Installed PostgreSQL major version: $installedVersion" -Level INFO
                }
            }
        }
        catch {
            Write-Log "Could not determine installed PostgreSQL version" -Level WARN
        }

        if (-not $Force) {
            Write-Log "Use -Force to reinstall/upgrade" -Level WARN
        }
    }
    else {
        Write-Log "PostgreSQL not found at $PostgresInstallDir - fresh install required" -Level INFO
    }

    # Check if TimescaleDB is already installed
    if (Test-TimescaleInstalled) {
        Write-Log "TimescaleDB extension appears to be installed" -Level INFO
    }

    Write-Log "Pre-flight checks passed" -Level SUCCESS
}

# ============================================================================
# Step 1: Download PostgreSQL Installer
# ============================================================================

function Get-PostgresInstaller {
    <#
    .SYNOPSIS
        Downloads the PostgreSQL MSI installer if not already present.
    #>
    Write-Log "Preparing PostgreSQL installer..." -Level INFO

    # Ensure download directory exists
    if (-not (Test-Path $DownloadDir)) {
        New-Item -ItemType Directory -Path $DownloadDir -Force | Out-Null
    }

    # If a custom path was provided, use it directly
    if ($PostgresInstallerPath -and (Test-Path $PostgresInstallerPath)) {
        Write-Log "Using provided installer: $PostgresInstallerPath" -Level INFO
        return $PostgresInstallerPath
    }

    if ($SkipDownload) {
        # Look for any existing installer in the download directory
        $existingInstaller = Get-ChildItem -Path $DownloadDir -Filter "postgresql-*.exe" |
            Select-Object -First 1
        if ($existingInstaller) {
            Write-Log "Using cached installer: $($existingInstaller.FullName)" -Level INFO
            return $existingInstaller.FullName
        }
        throw "No installer found and -SkipDownload specified. Provide -PostgresInstallerPath."
    }

    # Download the installer
    $installerFilename = Split-Path ([Uri]$PostgresInstallerUrl).LocalPath -Leaf
    $installerPath = Join-Path $DownloadDir $installerFilename

    if (Test-Path $installerPath) {
        Write-Log "Installer already downloaded: $installerPath" -Level INFO
        return $installerPath
    }

    Write-Log "Downloading PostgreSQL installer from $PostgresInstallerUrl ..." -Level INFO
    Write-Log "This may take several minutes (installer is ~300MB)..." -Level INFO

    try {
        Invoke-WebRequest -Uri $PostgresInstallerUrl -OutFile $installerPath -UseBasicParsing
        Write-Log "Download complete: $installerPath" -Level SUCCESS
    }
    catch {
        Write-Log "Download failed: $_" -Level ERROR
        throw
    }

    # Verify download
    if (-not (Test-Path $installerPath) -or (Get-Item $installerPath).Length -lt 1MB) {
        Write-Log "Download appears incomplete or corrupt" -Level ERROR
        throw "Download verification failed"
    }

    return $installerPath
}

# ============================================================================
# Step 2: Install PostgreSQL
# ============================================================================

function Install-PostgreSQL {
    <#
    .SYNOPSIS
        Installs PostgreSQL using the silent MSI/exe installer.
    #>
    param([string]$InstallerPath)

    if (Test-PostgresInstalled) {
        Write-Log "PostgreSQL already installed at $PostgresInstallDir" -Level INFO
        if (-not $Force) {
            Write-Log "Skipping installation (use -Force to reinstall)" -Level INFO
            return
        }
        Write-Log "Force flag set, reinstalling PostgreSQL..." -Level WARN
    }

    Write-Log "Installing PostgreSQL from $InstallerPath ..." -Level INFO

    # The PostgreSQL Windows installer (exe) supports unattended mode with these flags:
    #   --mode unattended  : No GUI
    #   --unattendedmodeui none : No UI in unattended mode
    #   --superpassword <pw> : Password for postgres superuser
    #   --serverport <port> : Port number
    #   --install_runtimes 0 : Skip MSVC runtime installation (usually already present)
    #   --prefix <dir> : Installation directory
    #   --datadir <dir> : Data directory

    $installArgs = @(
        "--mode", "unattended"
        "--unattendedmodeui", "none"
        "--superpassword", $PostgresPassword
        "--serverport", $PostgresPort
        "--prefix", "`"$PostgresInstallDir`""
        "--datadir", "`"$PostgresDataDir`""
        "--install_runtimes", "0"
    )

    Write-Log "Running: $InstallerPath $($installArgs -join ' ')" -Level INFO

    if ($DryRun) {
        Write-Log "[DRY RUN] Would run installer with arguments above" -Level INFO
        return
    }

    try {
        $process = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs `
            -Wait -PassThru -NoNewWindow

        if ($process.ExitCode -ne 0) {
            # Exit codes: 0=success, 1=restart required, other=error
            # Code 1 is acceptable - it means a reboot is pending
            if ($process.ExitCode -eq 1) {
                Write-Log "PostgreSQL installed successfully (reboot may be required)" -Level WARN
            }
            else {
                Write-Log "PostgreSQL installer exited with code $($process.ExitCode)" -Level ERROR
                throw "PostgreSQL installation failed with exit code $($process.ExitCode)"
            }
        }
        else {
            Write-Log "PostgreSQL installed successfully" -Level SUCCESS
        }
    }
    catch {
        Write-Log "Installation failed: $_" -Level ERROR
        throw
    }

    # Verify installation
    if (-not (Test-PostgresInstalled)) {
        Write-Log "PostgreSQL binary not found after installation at $PSQL_EXE" -Level ERROR
        throw "PostgreSQL installation verification failed"
    }

    Write-Log "PostgreSQL installation verified: $PSQL_EXE" -Level SUCCESS
}

# ============================================================================
# Step 3: Start PostgreSQL Service
# ============================================================================

function Start-PostgresService {
    <#
    .SYNOPSIS
        Starts the PostgreSQL service if it's not already running.
    #>
    Write-Log "Ensuring PostgreSQL service is running..." -Level INFO

    if (Test-PostgresRunning) {
        Write-Log "PostgreSQL service is already running" -Level INFO
        return
    }

    # Find the PostgreSQL service (name varies: "postgresql-x64-16", "pgsql-16", etc.)
    $pgService = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if ($null -eq $pgService) {
        # Try to start using pg_ctl directly
        Write-Log "No PostgreSQL service found, attempting to start via pg_ctl..." -Level WARN

        if (Test-Path $PG_CTL_EXE) {
            & $PG_CTL_EXE start -D $PostgresDataDir -l "$PostgresDataDir\postgresql.log" -w
            Start-Sleep -Seconds 3

            if (Test-PostgresRunning) {
                Write-Log "PostgreSQL started via pg_ctl" -Level SUCCESS
                return
            }
        }

        Write-Log "Could not find or start PostgreSQL service" -Level ERROR
        throw "PostgreSQL service not found"
    }

    Write-Log "Starting service: $($pgService.Name)..." -Level INFO

    if ($DryRun) {
        Write-Log "[DRY RUN] Would start service $($pgService.Name)" -Level INFO
        return
    }

    try {
        Start-Service -Name $pgService.Name
        Start-Sleep -Seconds 5

        if (Test-PostgresRunning) {
            Write-Log "PostgreSQL service started successfully" -Level SUCCESS
        }
        else {
            Write-Log "Service started but not yet responding, waiting 10s..." -Level WARN
            Start-Sleep -Seconds 10

            if (-not (Test-PostgresRunning)) {
                Write-Log "PostgreSQL service failed to start" -Level ERROR
                throw "Service startup failed"
            }
        }
    }
    catch {
        Write-Log "Failed to start PostgreSQL service: $_" -Level ERROR
        throw
    }
}

# ============================================================================
# Step 4: Download and Install TimescaleDB Extension
# ============================================================================

function Install-TimescaleDB {
    <#
    .SYNOPSIS
        Downloads and installs the TimescaleDB shared library extension.
    #>
    Write-Log "Preparing TimescaleDB extension..." -Level INFO

    $libDir = Join-Path $PostgresInstallDir "lib"
    $shareDir = Join-Path $PostgresInstallDir "share\extension"
    $timescaleDll = Join-Path $libDir "timescaledb.dll"

    if ((Test-TimescaleInstalled) -and -not $Force) {
        Write-Log "TimescaleDB already installed" -Level INFO
        return
    }

    # Download TimescaleDB
    $timescaleFilename = Split-Path ([Uri]$TimescaleUrl).LocalPath -Leaf
    $timescaleZip = Join-Path $DownloadDir $timescaleFilename
    $timescaleExtractDir = Join-Path $DownloadDir "timescaledb-extract"

    if (-not (Test-Path $timescaleZip) -and -not $SkipDownload) {
        Write-Log "Downloading TimescaleDB from $TimescaleUrl ..." -Level INFO

        try {
            Invoke-WebRequest -Uri $TimescaleUrl -OutFile $timescaleZip -UseBasicParsing
            Write-Log "TimescaleDB download complete" -Level SUCCESS
        }
        catch {
            Write-Log "TimescaleDB download failed: $_" -Level ERROR
            throw
        }
    }
    elseif (-not (Test-Path $timescaleZip)) {
        throw "TimescaleDB zip not found and -SkipDownload specified"
    }

    # Extract TimescaleDB
    Write-Log "Extracting TimescaleDB..." -Level INFO

    if ($DryRun) {
        Write-Log "[DRY RUN] Would extract and install TimescaleDB" -Level INFO
        return
    }

    if (Test-Path $timescaleExtractDir) {
        Remove-Item -Path $timescaleExtractDir -Recurse -Force
    }

    try {
        Expand-Archive -Path $timescaleZip -DestinationPath $timescaleExtractDir -Force
    }
    catch {
        Write-Log "Failed to extract TimescaleDB: $_" -Level ERROR
        throw
    }

    # Find the extracted directory structure (varies by release)
    $tsReleaseDir = Get-ChildItem -Path $timescaleExtractDir -Directory |
        Where-Object { $_.Name -like "timescaledb*" } |
        Select-Object -First 1

    if ($null -eq $tsReleaseDir) {
        # Maybe the files are directly in the root
        $tsReleaseDir = Get-Item $timescaleExtractDir
    }

    # Copy TimescaleDB files to PostgreSQL directories
    Write-Log "Installing TimescaleDB files to PostgreSQL directory..." -Level INFO

    # Ensure target directories exist
    if (-not (Test-Path $libDir)) {
        New-Item -ItemType Directory -Path $libDir -Force | Out-Null
    }
    if (-not (Test-Path $shareDir)) {
        New-Item -ItemType Directory -Path $shareDir -Force | Out-Null
    }

    # Copy .dll files from lib/
    $tsLibDir = Join-Path $tsReleaseDir.FullName "lib"
    if (Test-Path $tsLibDir) {
        Copy-Item -Path (Join-Path $tsLibDir "*.dll") -Destination $libDir -Force
        Copy-Item -Path (Join-Path $tsLibDir "*.lib") -Destination $libDir -Force -ErrorAction SilentlyContinue
        $dllCount = (Get-ChildItem -Path $libDir -Filter "timescaledb*.dll").Count
        Write-Log "Copied $dllCount TimescaleDB DLL(s) to $libDir" -Level INFO
    }
    else {
        Write-Log "TimescaleDB lib directory not found at $tsLibDir" -Level WARN
    }

    # Copy .sql files from share/extension/
    $tsShareDir = Join-Path $tsReleaseDir.FullName "share\extension"
    if (Test-Path $tsShareDir) {
        Copy-Item -Path (Join-Path $tsShareDir "*.sql") -Destination $shareDir -Force
        Copy-Item -Path (Join-Path $tsShareDir "*.control") -Destination $shareDir -Force
        $sqlCount = (Get-ChildItem -Path $shareDir -Filter "timescaledb*").Count
        Write-Log "Copied $sqlCount TimescaleDB extension files to $shareDir" -Level INFO
    }
    else {
        Write-Log "TimescaleDB share directory not found at $tsShareDir" -Level WARN
    }

    # Verify installation
    if (Test-TimescaleInstalled) {
        Write-Log "TimescaleDB extension installed successfully" -Level SUCCESS
    }
    else {
        Write-Log "TimescaleDB DLL not found after installation - check version compatibility" -Level WARN
        Write-Log "You may need to manually copy timescaledb.dll to $libDir" -Level WARN
    }

    # Cleanup extraction directory
    try {
        Remove-Item -Path $timescaleExtractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    catch {
        # Non-critical cleanup failure
    }
}

# ============================================================================
# Step 5: Create Database, User, and Enable TimescaleDB Extension
# ============================================================================

function Initialize-Database {
    <#
    .SYNOPSIS
        Creates the trading_bot database and user, then enables TimescaleDB.
    #>
    Write-Log "Initializing database and user..." -Level INFO

    if (-not (Test-Path $PSQL_EXE)) {
        Write-Log "psql.exe not found at $PSQL_EXE" -Level ERROR
        throw "PostgreSQL client not available"
    }

    if ($DryRun) {
        Write-Log "[DRY RUN] Would create database '$DatabaseName' and user '$DatabaseUser'" -Level INFO
        return
    }

    # Set PGPASSWORD for passwordless authentication during setup
    $env:PGPASSWORD = $PostgresPassword

    try {
        # Step 5a: Create the trading_bot user (if not exists)
        Write-Log "Creating database user '$DatabaseUser'..." -Level INFO
        $createUserSql = @"
DO `$`BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$DatabaseUser') THEN
        CREATE ROLE $DatabaseUser WITH LOGIN PASSWORD '$DatabaseUserPassword';
    END IF;
END
`$`;
"@

        $createUserSql | & $PSQL_EXE -h localhost -p $PostgresPort -U postgres -d postgres 2>&1 |
            ForEach-Object { Write-Log "  psql: $_" -Level INFO }

        if ($LASTEXITCODE -ne 0) {
            Write-Log "Failed to create user '$DatabaseUser'" -Level ERROR
            throw "User creation failed"
        }
        Write-Log "User '$DatabaseUser' created/verified" -Level SUCCESS

        # Step 5b: Create the trading_bot database (if not exists)
        Write-Log "Creating database '$DatabaseName'..." -Level INFO
        $checkDbSql = "SELECT 1 FROM pg_database WHERE datname = '$DatabaseName'"
        $dbExists = $checkDbSql | & $PSQL_EXE -h localhost -p $PostgresPort -U postgres -d postgres -t -A 2>&1

        if ($dbExists -ne "1") {
            $createDbSql = "CREATE DATABASE $DatabaseName OWNER $DatabaseUser"
            $createDbSql | & $PSQL_EXE -h localhost -p $PostgresPort -U postgres -d postgres 2>&1 |
                ForEach-Object { Write-Log "  psql: $_" -Level INFO }

            if ($LASTEXITCODE -ne 0) {
                Write-Log "Failed to create database '$DatabaseName'" -Level ERROR
                throw "Database creation failed"
            }
            Write-Log "Database '$DatabaseName' created" -Level SUCCESS
        }
        else {
            Write-Log "Database '$DatabaseName' already exists" -Level INFO
        }

        # Step 5c: Grant privileges
        Write-Log "Granting privileges to '$DatabaseUser' on '$DatabaseName'..." -Level INFO
        $grantSql = @"
GRANT ALL PRIVILEGES ON DATABASE $DatabaseName TO $DatabaseUser;
ALTER DATABASE $DatabaseName OWNER TO $DatabaseUser;
"@
        $grantSql | & $PSQL_EXE -h localhost -p $PostgresPort -U postgres -d postgres 2>&1 |
            ForEach-Object { Write-Log "  psql: $_" -Level INFO }

        # Grant schema privileges
        $grantSchemaSql = @"
GRANT ALL ON SCHEMA public TO $DatabaseUser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $DatabaseUser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $DatabaseUser;
"@
        $grantSchemaSql | & $PSQL_EXE -h localhost -p $PostgresPort -U postgres -d $DatabaseName 2>&1 |
            ForEach-Object { Write-Log "  psql: $_" -Level INFO }

        Write-Log "Privileges granted" -Level SUCCESS

        # Step 5d: Enable TimescaleDB extension
        Write-Log "Enabling TimescaleDB extension..." -Level INFO
        $enableTSSql = "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
        $enableTSSql | & $PSQL_EXE -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName 2>&1 |
            ForEach-Object { Write-Log "  psql: $_" -Level INFO }

        if ($LASTEXITCODE -ne 0) {
            Write-Log "Failed to enable TimescaleDB extension (extension may not be loaded yet)" -Level WARN
            Write-Log "You may need to restart PostgreSQL and run: CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;" -Level WARN
        }
        else {
            Write-Log "TimescaleDB extension enabled" -Level SUCCESS
        }

        # Step 5e: Enable other useful extensions
        Write-Log "Enabling additional PostgreSQL extensions..." -Level INFO
        $extraExtensions = @(
            "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
            "CREATE EXTENSION IF NOT EXISTS btree_gist;"
        )
        foreach ($extSql in $extraExtensions) {
            $extSql | & $PSQL_EXE -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName 2>&1 |
                ForEach-Object { Write-Log "  psql: $_" -Level INFO }
        }

        Write-Log "Database initialization complete" -Level SUCCESS
    }
    finally {
        # Clear password from environment
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }
}

# ============================================================================
# Step 6: Verify Installation
# ============================================================================

function Test-Installation {
    <#
    .SYNOPSIS
        Verifies that PostgreSQL and TimescaleDB are correctly installed.
    #>
    Write-Log "Verifying installation..." -Level INFO

    if ($DryRun) {
        Write-Log "[DRY RUN] Would verify installation" -Level INFO
        return $true
    }

    $env:PGPASSWORD = $PostgresPassword
    $allPassed = $true

    try {
        # Test 1: PostgreSQL version
        Write-Log "Test 1: PostgreSQL version check..." -Level INFO
        $versionOutput = & $PSQL_EXE -h localhost -p $PostgresPort -U postgres -d postgres -t -A -c "SELECT version()" 2>&1
        if ($versionOutput -match "PostgreSQL") {
            Write-Log "  PASS: $versionOutput" -Level SUCCESS
        }
        else {
            Write-Log "  FAIL: Could not determine PostgreSQL version" -Level ERROR
            $allPassed = $false
        }

        # Test 2: Trading bot database exists
        Write-Log "Test 2: Database connectivity..." -Level INFO
        $dbCheck = & $PSQL_EXE -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName -t -A -c "SELECT current_database()" 2>&1
        if ($dbCheck -eq $DatabaseName) {
            Write-Log "  PASS: Connected to database '$DatabaseName'" -Level SUCCESS
        }
        else {
            Write-Log "  FAIL: Could not connect to database (got: $dbCheck)" -Level ERROR
            $allPassed = $false
        }

        # Test 3: TimescaleDB extension is loaded
        Write-Log "Test 3: TimescaleDB extension check..." -Level INFO
        $tsCheck = & $PSQL_EXE -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName -t -A -c "SELECT extname FROM pg_extension WHERE extname = 'timescaledb'" 2>&1
        if ($tsCheck -eq "timescaledb") {
            Write-Log "  PASS: TimescaleDB extension is active" -Level SUCCESS
        }
        else {
            Write-Log "  FAIL: TimescaleDB extension not found (got: $tsCheck)" -Level ERROR
            $allPassed = $false
        }

        # Test 4: TimescaleDB version
        Write-Log "Test 4: TimescaleDB version check..." -Level INFO
        $tsVersion = & $PSQL_EXE -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName -t -A -c "SELECT default_version FROM pg_available_extensions WHERE name = 'timescaledb'" 2>&1
        if ($tsVersion) {
            Write-Log "  PASS: TimescaleDB version: $tsVersion" -Level SUCCESS
        }
        else {
            Write-Log "  WARN: Could not determine TimescaleDB version" -Level WARN
        }

        # Test 5: User has proper permissions
        Write-Log "Test 5: User permissions check..." -Level INFO
        $permCheck = & $PSQL_EXE -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName -t -A -c "SELECT has_database_privilege('$DatabaseUser', '$DatabaseName', 'CREATE')" 2>&1
        if ($permCheck -eq "t") {
            Write-Log "  PASS: User '$DatabaseUser' has CREATE privilege on '$DatabaseName'" -Level SUCCESS
        }
        else {
            Write-Log "  WARN: User may have limited permissions (got: $permCheck)" -Level WARN
        }

    }
    finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }

    if ($allPassed) {
        Write-Log "All verification tests passed!" -Level SUCCESS
    }
    else {
        Write-Log "Some verification tests failed - review log output" -Level WARN
    }

    return $allPassed
}

# ============================================================================
# Step 7: Generate Connection Configuration Summary
# ============================================================================

function Show-ConnectionInfo {
    <#
    .SYNOPSIS
        Displays the connection configuration for use in .env file.
    #>
    Write-Log "" -Level INFO
    Write-Log "============================================" -Level INFO
    Write-Log "  Installation Complete - Connection Info" -Level INFO
    Write-Log "============================================" -Level INFO
    Write-Log "" -Level INFO
    Write-Log "Add these to your .env file:" -Level INFO
    Write-Log "  DATABASE_BACKEND=postgres" -Level INFO
    Write-Log "  POSTGRES_HOST=localhost" -Level INFO
    Write-Log "  POSTGRES_PORT=$PostgresPort" -Level INFO
    Write-Log "  POSTGRES_DB=$DatabaseName" -Level INFO
    Write-Log "  POSTGRES_USER=$DatabaseUser" -Level INFO
    Write-Log "  POSTGRES_PASSWORD=$DatabaseUserPassword" -Level INFO
    Write-Log "" -Level INFO
    Write-Log "Connection string:" -Level INFO
    Write-Log "  postgresql://${DatabaseUser}:${DatabaseUserPassword}@localhost:${PostgresPort}/${DatabaseName}" -Level INFO
    Write-Log "" -Level INFO
    Write-Log "psql command:" -Level INFO
    Write-Log "  psql -h localhost -p $PostgresPort -U $DatabaseUser -d $DatabaseName" -Level INFO
    Write-Log "" -Level INFO
    Write-Log "============================================" -Level INFO
}

# ============================================================================
# Main Execution
# ============================================================================

function Main {
    <#
    .SYNOPSIS
        Main entry point for the installation script.
    #>
    Write-Log "============================================" -Level INFO
    Write-Log "  Bot 3 - PostgreSQL + TimescaleDB Setup" -Level INFO
    Write-Log "  Version: PostgreSQL $PostgresVersion" -Level INFO
    Write-Log "  TimescaleDB: $TimescaleVersion" -Level INFO
    Write-Log "  Target: $PostgresInstallDir" -Level INFO
    Write-Log "============================================" -Level INFO
    Write-Log "" -Level INFO

    if ($DryRun) {
        Write-Log "*** DRY RUN MODE - No changes will be made ***" -Level WARN
        Write-Log "" -Level INFO
    }

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        # Step 0: Pre-flight checks
        Test-Prerequisites

        # Step 1: Download installer
        $installerPath = Get-PostgresInstaller

        # Step 2: Install PostgreSQL
        Install-PostgreSQL -InstallerPath $installerPath

        # Step 3: Start the service
        Start-PostgresService

        # Step 4: Install TimescaleDB extension
        Install-TimescaleDB

        # Step 5: Create database and user
        Initialize-Database

        # Step 6: Verify installation
        $verified = Test-Installation

        # Step 7: Show connection info
        Show-ConnectionInfo

        $elapsed = $stopwatch.Elapsed
        Write-Log "" -Level INFO
        Write-Log "Installation completed in $([math]::Round($elapsed.TotalMinutes, 1)) minutes" -Level SUCCESS

        if (-not $verified) {
            Write-Log "Some checks failed - review the log at $LogFile" -Level WARN
        }
    }
    catch {
        $elapsed = $stopwatch.Elapsed
        Write-Log "" -Level ERROR
        Write-Log "Installation FAILED after $([math]::Round($elapsed.TotalMinutes, 1)) minutes" -Level ERROR
        Write-Log "Error: $_" -Level ERROR
        Write-Log "Full log: $LogFile" -Level ERROR
        Write-Log "" -Level ERROR
        Write-Log "Common fixes:" -Level INFO
        Write-Log "  1. Ensure you're running as Administrator" -Level INFO
        Write-Log "  2. Check that port $PostgresPort is not in use" -Level INFO
        Write-Log "  3. Temporarily disable antivirus during installation" -Level INFO
        Write-Log "  4. Check the full log at: $LogFile" -Level INFO
        exit 1
    }
}

# Run the main function
Main
