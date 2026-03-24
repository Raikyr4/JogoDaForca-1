param(
    [switch]$Wait,
    [int]$TimeoutSeconds = 90
)

$urls = @(
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:9090",
    "http://localhost:5540",
    "http://localhost:8001/health",
    "http://localhost:8002/health"
)

function Test-UrlReady {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 3 -UseBasicParsing
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

if ($Wait) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    foreach ($url in $urls) {
        while ((Get-Date) -lt $deadline) {
            if (Test-UrlReady -Url $url) {
                break
            }
            Start-Sleep -Seconds 1
        }
    }
}

foreach ($url in $urls) {
    Start-Process $url
}

Write-Host "URLs abertas com sucesso."
