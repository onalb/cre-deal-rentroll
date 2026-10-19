# Recreate the AgentCore CloudWatch dashboard, saved queries, and metric filters.
# Usage (repo root):  powershell -File deploy/put-agentcore-dashboard.ps1
$ErrorActionPreference = "Stop"
$aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path $aws)) { $aws = "aws" }
$region = "us-east-1"
$profile = "default"
$lg = "/aws/bedrock-agentcore/runtimes/cre_deal_rentroll-Nh63z6GXmK-DEFAULT"
$dash = "cre-deal-rentroll-agentcore"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bodyFile = Join-Path $here "cloudwatch-agentcore-dashboard.json"

& $aws cloudwatch put-dashboard --dashboard-name $dash --dashboard-body "file://$bodyFile" --region $region --profile $profile
if ($LASTEXITCODE -ne 0) { throw "put-dashboard failed" }

function Put-Query($name, $qs) {
  $existing = & $aws logs describe-query-definitions --query-definition-name-prefix $name --region $region --profile $profile --output json | ConvertFrom-Json
  $qid = ($existing.queryDefinitions | Where-Object { $_.name -eq $name } | Select-Object -First 1).queryDefinitionId
  $args = @("logs","put-query-definition","--name",$name,"--log-group-names",$lg,"--query-string",$qs,"--query-language","CWLI","--region",$region,"--profile",$profile)
  if ($qid) { $args += @("--query-definition-id", $qid) }
  & $aws @args | Out-Null
}

Put-Query "cre-deal-rentroll-agentcore-run" @"
fields @timestamp, @logStream, @message
| filter @message like /RUN /
| filter @message not like /GET \/ping/
| sort @timestamp desc
| limit 100
"@

Put-Query "cre-deal-rentroll-agentcore-invocations" @"
fields @timestamp, @logStream, @message
| filter @message like /\/invocations/
| filter @message like /POST / or @message like / 422/ or @message like / 500/
| filter @message not like /GET \/ping/
| sort @timestamp desc
| limit 50
"@

Put-Query "cre-deal-rentroll-agentcore-boxes" @"
fields @message
| parse @message /RUN (?<box>agentcore_[a-z0-9_]+)/
| filter ispresent(box)
| stats count() as lines by box
| sort box asc
"@

Put-Query "cre-deal-rentroll-agentcore-box-lines" @"
fields @timestamp, @logStream, @message
| filter @message like /RUN agentcore_/
| sort @timestamp desc
| limit 100
"@

foreach ($mf in @(
  "metric-filter-run.json",
  "metric-filter-ping.json",
  "metric-filter-invocations.json"
)) {
  $mfPath = Join-Path $here $mf
  & $aws logs put-metric-filter --cli-input-json "file://$mfPath" --region $region --profile $profile
  if ($LASTEXITCODE -ne 0) { throw "put-metric-filter $mf failed" }
}

Write-Host "Dashboard: https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#dashboards:name=$dash"
