[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$ProjectKey,
    [Parameter(Mandatory = $true)][string]$Model,
    [Parameter(Mandatory = $true)][string]$UserMessage,
    [Parameter(Mandatory = $true)][string]$AssistantMessage,
    [string]$SessionId = [guid]::NewGuid().ToString(),
    [string]$TurnId = [guid]::NewGuid().ToString(),
    [ValidatePattern('^http://(127\.0\.0\.1|localhost)(:\d+)?$')]
    [string]$PortalBaseUrl = 'http://127.0.0.1:8010'
)

$ErrorActionPreference = 'Stop'
$payload = @{
    session_id = $SessionId
    turn_id = $TurnId
    project_key = $ProjectKey
    model = $Model
    user_message = $UserMessage
    assistant_message = $AssistantMessage
    metadata = @{
        capture_adapter = 'capture-local-llm-chat.ps1'
        reported_result_is_evidence = $false
    }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Method Post `
    -Uri "$PortalBaseUrl/api/v1/local-llm/hooks/chat" `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($payload))
