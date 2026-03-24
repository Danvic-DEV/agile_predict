# pull_prod_data.ps1
# Restores a local pg_dump SQL backup into a throwaway Docker container,
# exports all relevant tables as CSVs, then removes the container.
#
# READ-ONLY against production: works entirely from the local backup file.
# The temp container is destroyed after the script completes.
#
# Usage:
#   cd c:\dev\agile_predict
#   .\research\pull_prod_data.ps1
#
# Prerequisites: Docker Desktop running locally.
# Place the backup at research/data/  e.g. agile_predict_backup_2026-03-24_03-00-00.sql

param(
    [string]$OutDir = "$PSScriptRoot\data",
    [string]$DbName = "agile_predict",
    [string]$DbUser = "postgres",
    [string]$TempContainerName = "agile-research-tmp"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "Output directory: $OutDir"

# ── Find the backup file ─────────────────────────────────────────────────────
$backupFile = Get-ChildItem -Path $OutDir -Filter "*.sql" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $backupFile) {
    Write-Error "No .sql backup file found in $OutDir. Copy the pg_dump file there first."
    exit 1
}
Write-Host "Using backup: $($backupFile.Name)"

# ── Clean up any leftover container from a previous run ──────────────────────
$existing = docker ps -a --filter "name=$TempContainerName" --format "{{.Names}}" 2>$null
if ($existing -eq $TempContainerName) {
    Write-Host "Removing leftover container $TempContainerName ..."
    docker rm -f $TempContainerName | Out-Null
}

# ── Start a fresh postgres container ────────────────────────────────────────
Write-Host "Starting temporary postgres container ..."
docker run -d `
    --name $TempContainerName `
    -e POSTGRES_USER=$DbUser `
    -e POSTGRES_PASSWORD=postgres `
    -e POSTGRES_DB=$DbName `
    postgres:17-alpine | Out-Null

# Wait for postgres to be ready (up to 30s)
Write-Host "Waiting for postgres to be ready ..." -NoNewline
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $check = docker exec $TempContainerName pg_isready -U $DbUser 2>&1
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Write-Host "." -NoNewline
}
Write-Host ""
if (-not $ready) {
    docker rm -f $TempContainerName | Out-Null
    Write-Error "Postgres container failed to become ready."
    exit 1
}

# ── Copy backup into container and restore ───────────────────────────────────
Write-Host "Copying backup into container ..."
docker cp $backupFile.FullName "${TempContainerName}:/tmp/backup.sql"

Write-Host "Restoring backup (this may take a minute) ..."
docker exec -e PGPASSWORD=postgres $TempContainerName `
    psql -U $DbUser -d $DbName -f /tmp/backup.sql -q
if ($LASTEXITCODE -ne 0) {
    docker rm -f $TempContainerName | Out-Null
    Write-Error "Restore failed."
    exit 1
}
Write-Host "Restore complete."

# ── Export tables as CSVs ────────────────────────────────────────────────────
function Export-Table {
    param([string]$Name, [string]$Query)
    $outFile = Join-Path $OutDir "$Name.csv"
    Write-Host "Exporting $Name ..." -NoNewline

    # Write to a file inside the container first (avoids stdout buffer overflow
    # on large tables like agile_data with 10M+ rows), then docker cp out.
    $tmpPath = "/tmp/${Name}.csv"
    docker exec -e PGPASSWORD=postgres $TempContainerName `
        psql -U $DbUser -d $DbName -c "\COPY ($Query) TO '$tmpPath' WITH CSV HEADER" 2>&1 | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Write-Host " FAILED"
        return
    }

    docker cp "${TempContainerName}:${tmpPath}" $outFile | Out-Null
    $rows = (Get-Content $outFile | Measure-Object -Line).Lines - 1
    $mb = [math]::Round((Get-Item $outFile).Length / 1MB, 1)
    Write-Host " $rows rows (${mb} MB) -> $Name.csv"
}

Export-Table "agile_actual" "SELECT date_time, region, agile_actual FROM prices_agile_actual ORDER BY date_time, region"
Export-Table "forecasts"    "SELECT id, name, created_at, mean, stdev FROM prices_forecasts ORDER BY created_at"
Export-Table "forecast_data" "SELECT forecast_id, date_time, bm_wind, solar, emb_wind, temp_2m, wind_10m, rad, demand FROM prices_forecastdata ORDER BY forecast_id, date_time"
Export-Table "price_history" "SELECT date_time, day_ahead, agile FROM prices_pricehistory ORDER BY date_time"
Export-Table "history"      "SELECT date_time, total_wind, bm_wind, solar, temp_2m, wind_10m, rad, demand FROM prices_history ORDER BY date_time"
Export-Table "agile_data"   "SELECT forecast_id, region, date_time, agile_pred, agile_low, agile_high FROM prices_agiledata ORDER BY forecast_id, date_time, region"

# ── Tear down the temp container ─────────────────────────────────────────────
Write-Host "Removing temp container ..."
docker rm -f $TempContainerName | Out-Null

Write-Host ""
Write-Host "Done. CSVs written to: $OutDir"
Write-Host "Next: open research/model_lab.ipynb and run Cell 1 to build joined.parquet"
