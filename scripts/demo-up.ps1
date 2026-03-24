$root = Resolve-Path "$PSScriptRoot/.."
Set-Location $root

docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Error "Falha ao subir o ambiente com Docker Compose."
    exit $LASTEXITCODE
}

& "$PSScriptRoot/open-urls.ps1" -Wait
