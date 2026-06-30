param(
    [string]$BaseUrl = "http://127.0.0.1:8000/api/v1",
    [string]$TenantUiUrl = "http://127.0.0.1:5173/",
    [string]$AdminUiUrl = "http://127.0.0.1:5174/admin",
    [string]$Username = "admin",
    [string]$Password = "admin123",
    [int]$WorkspaceId = 0,
    [int]$TaskId = 0
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "=== $Title ==="
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Get-PropertyValue {
    param(
        [object]$Object,
        [string]$Name,
        [object]$DefaultValue = $null
    )
    if ($null -eq $Object) {
        return $DefaultValue
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $DefaultValue
    }
    return $property.Value
}

function Get-IntValue {
    param(
        [object]$Object,
        [string]$Name,
        [int]$DefaultValue = 0
    )
    $value = Get-PropertyValue -Object $Object -Name $Name -DefaultValue $DefaultValue
    if ($null -eq $value -or $value -eq "") {
        return $DefaultValue
    }
    return [int]$value
}

function Get-NestedText {
    param(
        [object]$Object,
        [string]$ObjectName,
        [string]$Name,
        [string]$DefaultValue = "-"
    )
    $nested = Get-PropertyValue -Object $Object -Name $ObjectName -DefaultValue $null
    $value = Get-PropertyValue -Object $nested -Name $Name -DefaultValue $DefaultValue
    if ($null -eq $value -or $value -eq "") {
        return $DefaultValue
    }
    return ([string]$value).Trim()
}

function Has-BusinessValue {
    param([object]$Value)
    if ($null -eq $Value) {
        return $false
    }
    $text = ([string]$Value).Trim()
    return -not [string]::IsNullOrWhiteSpace($text) -and $text -ne "-"
}

function Invoke-Api {
    param(
        [string]$Method = "GET",
        [string]$Path,
        [object]$Body = $null
    )
    $headers = @{
        Authorization = "Bearer $script:Token"
    }
    if ($script:WorkspaceId -gt 0) {
        $separator = "?"
        if ($Path.Contains("?")) {
            $separator = "&"
        }
        $Path = "$Path$separator" + "workspace_id=$script:WorkspaceId"
    }
    $uri = "$BaseUrl$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -TimeoutSec 60
    }
    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -ContentType "application/json" -Body $json -TimeoutSec 120
}

Write-Section "Health"
$health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 10
Assert-True (($health.status -eq "ok") -or ($health.app -eq "Cargo Platform")) "Backend health check failed."
Write-Host ("Backend: " + ($health | ConvertTo-Json -Compress))

foreach ($url in @($TenantUiUrl, $AdminUiUrl)) {
    $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
    Assert-True ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) "UI endpoint failed: $url"
    Write-Host "UI OK: $url ($($response.StatusCode))"
}

Write-Section "Login"
$loginBody = @{
    username = $Username
    password = $Password
}
$login = Invoke-RestMethod -Method POST -Uri "$BaseUrl/auth/login" -ContentType "application/json" -Body ($loginBody | ConvertTo-Json) -TimeoutSec 30
$script:Token = $login.access_token
Assert-True (-not [string]::IsNullOrWhiteSpace($script:Token)) "Login did not return access token."

$me = Invoke-RestMethod -Uri "$BaseUrl/auth/me" -Headers @{ Authorization = "Bearer $script:Token" } -TimeoutSec 30
if ($WorkspaceId -le 0) {
    $workspace = @($me.workspaces | Select-Object -First 1)[0]
    Assert-True ($null -ne $workspace) "Current user has no workspace."
    $script:WorkspaceId = [int]$workspace.id
}
else {
    $script:WorkspaceId = $WorkspaceId
}
Write-Host "Workspace: $script:WorkspaceId"

Write-Section "Task Selection"
$tasks = Invoke-Api -Path "/capture-tasks?limit=2000"
Assert-True (@($tasks).Count -gt 0) "No capture tasks found."
if ($TaskId -le 0) {
    $task = @($tasks | Sort-Object -Property id -Descending | Select-Object -First 1)[0]
}
else {
    $task = @($tasks | Where-Object { [int]$_.id -eq $TaskId } | Select-Object -First 1)[0]
}
Assert-True ($null -ne $task) "Requested capture task was not found."
$script:TaskId = [int]$task.id
Write-Host "Task: #$script:TaskId $($task.name) / $($task.status)"

Write-Section "Recognition Rule Pack"
$packs = Invoke-Api -Path "/recognition-rule-packs"
$activePack = Get-PropertyValue -Object $packs -Name "active_pack"
Assert-True ($null -ne $activePack) "No active recognition rule pack. Order parsing must prompt user to import/enable a rule pack."
Write-Host "Active pack: $($activePack.code) / $($activePack.name)"

Write-Section "Order Rows"
$draftsPath = "/order-row-drafts/tasks/{0}?limit=5000" -f $script:TaskId
$drafts = Invoke-Api -Path $draftsPath
$draftSummary = Get-PropertyValue -Object $drafts -Name "summary"
$parents = @((Get-PropertyValue -Object $drafts -Name "parents" -DefaultValue @()))
$draftRows = @((Get-PropertyValue -Object $drafts -Name "rows" -DefaultValue @()))
$parentCount = @($parents).Count
$rowCount = @($draftRows).Count
$summaryParentCount = Get-IntValue -Object $draftSummary -Name "parent_waybill_count" -DefaultValue $parentCount
$summaryRowCount = Get-IntValue -Object $draftSummary -Name "standard_row_count" -DefaultValue $rowCount
Assert-True ($parentCount -gt 0) "Order row drafts returned no parent waybills."
Assert-True ($rowCount -gt 0) "Order row drafts returned no rows."
Assert-True ($rowCount -ge $parentCount) "Order row count is smaller than parent waybill count; multi-product/single-product accounting is suspicious."
Write-Host "Draft parents: $parentCount (summary $summaryParentCount)"
Write-Host "Draft rows:    $rowCount (summary $summaryRowCount)"

Write-Section "Product Matching Preview"
$previewBody = @{
    scope = @{
        scope_type = "current_batch"
        task_id = $script:TaskId
        confirmed_by_user = $true
    }
    include_saved_rules = $true
}
$matchPreview = Invoke-Api -Method POST -Path "/product-sku-linking/preview" -Body $previewBody
$matchSummary = Get-PropertyValue -Object $matchPreview -Name "summary"
$matchRows = @((Get-PropertyValue -Object $matchPreview -Name "rows" -DefaultValue @()))
$matched = Get-IntValue -Object $matchSummary -Name "matched" 0
$productUnmatched = Get-IntValue -Object $matchSummary -Name "product_unmatched" 0
$skuUnmatched = Get-IntValue -Object $matchSummary -Name "sku_unmatched" 0
$skuAmbiguous = Get-IntValue -Object $matchSummary -Name "sku_ambiguous" 0
$imageUnmatched = Get-IntValue -Object $matchSummary -Name "image_unmatched" 0
$conflict = Get-IntValue -Object $matchSummary -Name "conflict" 0
$pending = Get-IntValue -Object $matchSummary -Name "pending" 0
$special = Get-IntValue -Object $matchSummary -Name "special" 0
$matchTotal = $matched + $productUnmatched + $skuUnmatched + $skuAmbiguous + $imageUnmatched + $conflict + $pending + $special
if ($matchTotal -eq 0) {
    $matchTotal = @($matchRows).Count
}
Assert-True (@($matchRows).Count -eq $rowCount) "Product matching row count ($(@($matchRows).Count)) does not match order draft row count ($rowCount)."
Write-Host "Matched: $matched"
Write-Host "Product unmatched: $productUnmatched"
Write-Host "SKU unmatched: $skuUnmatched"
Write-Host "SKU ambiguous: $skuAmbiguous"
Write-Host "Image unmatched: $imageUnmatched"
Write-Host "Conflict: $conflict"
Write-Host "Pending: $pending"
Write-Host "Special: $special"

$exceptionGroups = @{}
foreach ($row in $matchRows) {
    $status = Get-PropertyValue -Object $row -Name "match_status" -DefaultValue ""
    if ($status -eq "matched" -or $status -eq "special" -or $status -eq "") {
        continue
    }
    $productText = Get-NestedText -Object $row -ObjectName "input" -Name "product"
    $salesAttr1Text = Get-NestedText -Object $row -ObjectName "input" -Name "sales_attr1"
    $salesAttr2Text = Get-NestedText -Object $row -ObjectName "input" -Name "sales_attr2"
    $key = "$status`t$productText`t$salesAttr1Text`t$salesAttr2Text"
    if (-not $exceptionGroups.ContainsKey($key)) {
        $exceptionGroups[$key] = [ordered]@{
            status = $status
            product = $productText
            sales_attr1 = $salesAttr1Text
            sales_attr2 = $salesAttr2Text
            count = 0
        }
    }
    $exceptionGroups[$key].count += 1
}

Write-Host ""
Write-Host "Top actionable exception groups:"
$topGroups = @($exceptionGroups.Values | Sort-Object -Property count -Descending | Select-Object -First 12)
if (@($topGroups).Count -eq 0) {
    Write-Host "No actionable exceptions."
}
foreach ($group in $topGroups) {
    Write-Host ("- {0} x{1}: {2} / {3} / {4}" -f $group.status, $group.count, $group.product, $group.sales_attr1, $group.sales_attr2)
}

Write-Section "Report Preview"
$report = Invoke-Api -Path "/collector-control/tasks/$script:TaskId/report-preview"
$reportRows = @((Get-PropertyValue -Object $report -Name "rows" -DefaultValue @()))
$reportSummary = Get-PropertyValue -Object $report -Name "summary"
$reportTotal = Get-IntValue -Object $reportSummary -Name "total" @($reportRows).Count
Assert-True (@($reportRows).Count -eq $rowCount) "Report preview rows ($(@($reportRows).Count)) do not match order draft rows ($rowCount)."
Assert-True ($reportTotal -eq @($reportRows).Count) "Report preview summary total ($reportTotal) does not match returned rows ($(@($reportRows).Count))."
$expectedNormalRows = 0
foreach ($row in $reportRows) {
    $status = Get-PropertyValue -Object $row -Name "status" -DefaultValue ""
    $salesAttr1 = Get-PropertyValue -Object $row -Name "sales_attr1_text" -DefaultValue ""
    $salesAttr2 = Get-PropertyValue -Object $row -Name "sales_attr2_text" -DefaultValue ""
    if ($status -eq "matched" -and (Has-BusinessValue $salesAttr1) -and (Has-BusinessValue $salesAttr2)) {
        $expectedNormalRows += 1
    }
}
$expectedExceptionRows = @($reportRows).Count - $expectedNormalRows
Write-Host "Report rows: $(@($reportRows).Count)"
Write-Host ("Report summary: " + ($reportSummary | ConvertTo-Json -Compress))
Write-Host "Expected workbook normal rows: $expectedNormalRows"
Write-Host "Expected workbook exception rows: $expectedExceptionRows"

Write-Section "Workbook Download"
$workbookPath = Join-Path $env:TEMP "cargo-platform-report-task-$script:TaskId.xlsx"
$headers = @{
    Authorization = "Bearer $script:Token"
}
$downloadPath = "/collector-control/tasks/$script:TaskId/report-workbook?workspace_id=$script:WorkspaceId"
Invoke-WebRequest -Uri "$BaseUrl$downloadPath" -Headers $headers -OutFile $workbookPath -UseBasicParsing -TimeoutSec 120 | Out-Null
$workbookFile = Get-Item -LiteralPath $workbookPath
Assert-True ($workbookFile.Length -gt 1000) "Downloaded workbook is too small or empty."
Write-Host "Workbook: $workbookPath ($($workbookFile.Length) bytes)"

Write-Section "Workbook Structure"
$pythonCandidates = @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
)
$pythonCommand = $null
foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $pythonCommand = $candidate
        break
    }
}
if ($null -eq $pythonCommand) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        $pythonCommand = $command.Source
    }
}
Assert-True ($null -ne $pythonCommand) "Python is required to inspect the workbook."

$workbookCheck = @'
import json
import sys
from openpyxl import load_workbook

path = sys.argv[1]
expected_normal_rows = int(sys.argv[2])
expected_exception_rows = int(sys.argv[3])
normal_headers = [
    "\u5546\u54c1",
    "\u9500\u552e\u5c5e\u60271",
    "\u56fe\u7247",
    "\u9500\u552e\u5c5e\u60272",
    "\u6570\u91cf",
    "\u5907\u6ce8",
    "\u56fe\u7247\u5339\u914d\u6587\u672c",
]
exception_sheet_name = "\u5f02\u5e38\u9762\u5355"
exception_headers = ["\u56fe\u7247\u5339\u914d\u6587\u672c"]

workbook = load_workbook(path, read_only=True, data_only=True)
if exception_sheet_name not in workbook.sheetnames:
    raise SystemExit("Workbook is missing exception sheet.")

normal_data_rows = 0
normal_sheets = []
for sheet in workbook.worksheets:
    header = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    if sheet.title == exception_sheet_name:
        if header[:1] != exception_headers:
            raise SystemExit(f"Exception sheet header mismatch: {header}")
        exception_data_rows = max(sheet.max_row - 1, 0)
        continue
    if header[:7] != normal_headers:
        raise SystemExit(f"Normal sheet header mismatch in {sheet.title}: {header}")
    normal_sheets.append(sheet.title)
    normal_data_rows += max(sheet.max_row - 1, 0)

if not normal_sheets:
    raise SystemExit("Workbook has no normal report sheet.")
if normal_data_rows != expected_normal_rows:
    raise SystemExit(f"Normal data row count mismatch: {normal_data_rows} != {expected_normal_rows}")
if exception_data_rows != expected_exception_rows:
    raise SystemExit(f"Exception row count mismatch: {exception_data_rows} != {expected_exception_rows}")

print(json.dumps({
    "normal_sheets": normal_sheets,
    "normal_data_rows": normal_data_rows,
    "exception_data_rows": exception_data_rows,
}, ensure_ascii=False))
'@
$workbookCheckPath = Join-Path $env:TEMP "cargo-platform-workbook-check.py"
[System.IO.File]::WriteAllText($workbookCheckPath, $workbookCheck, [System.Text.Encoding]::UTF8)
$workbookCheckResult = & $pythonCommand $workbookCheckPath $workbookPath $expectedNormalRows $expectedExceptionRows
if ($LASTEXITCODE -ne 0) {
    throw "Workbook structure validation failed."
}
Write-Host $workbookCheckResult

Write-Section "Result"
Write-Host "Customer delivery audit completed."
Write-Host ("Task #{0}: parents={1}, rows={2}, matched={3}, actionable_exceptions={4}, special={5}" -f $script:TaskId, $parentCount, $rowCount, $matched, ($productUnmatched + $skuUnmatched + $skuAmbiguous + $imageUnmatched + $conflict + $pending), $special)
