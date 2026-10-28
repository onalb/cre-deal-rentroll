$ErrorActionPreference = "Continue"
$aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
$env:AWS_PROFILE = "default"
$env:AWS_DEFAULT_REGION = "us-east-1"
$Account = "226721156429"
$Region = "us-east-1"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $PSScriptRoot "agentcore-platform.json"
$ids = @{}
if (Test-Path $Out) {
  try { $ids = Get-Content $Out -Raw | ConvertFrom-Json -AsHashtable } catch { $ids = @{} }
}

function AwsJson {
  param([Parameter(ValueFromRemainingArguments=$true)]$Args)
  $raw = & $aws @Args --region $Region --output json 2>&1 | Out-String
  return $raw
}

function TryAws {
  param($Label, [string[]]$Args)
  Write-Host "`n=== $Label ==="
  $errFile = [IO.Path]::GetTempFileName()
  $outFile = [IO.Path]::GetTempFileName()
  $p = Start-Process -FilePath $aws -ArgumentList ($Args + @("--region", $Region, "--output", "json")) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $outFile -RedirectStandardError $errFile
  $stdout = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
  $stderr = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
  Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) {
    Write-Host ("FAIL {0}: {1}" -f $Label, (($stderr + $stdout) -replace '\s+', ' ').Substring(0, [Math]::Min(500, (($stderr + $stdout).Length))))
    return $null
  }
  return $stdout
}

# --- IAM runtime policy ---
& $aws iam put-role-policy --role-name CreDealRentrollAgentCoreRuntime --policy-name agentcore-exec --policy-document ("file://{0}" -f (Join-Path $PSScriptRoot "agentcore-exec-policy.json")) | Out-Null
Write-Host "Updated CreDealRentrollAgentCoreRuntime policy"

# Gateway + Lambda roles
$trust = Get-Content (Join-Path $PSScriptRoot "agentcore-trust.json") -Raw
$roles = @("CreDealRentrollGateway","CreDealRentrollLambda","CreDealRentrollEval")
foreach ($r in $roles) {
  $exists = & $aws iam get-role --role-name $r 2>$null
  if (-not $exists) {
    $tmp = Join-Path $env:TEMP "$r-trust.json"
    Set-Content -Path $tmp -Value $trust -Encoding utf8
    & $aws iam create-role --role-name $r --assume-role-policy-document ("file://{0}" -f $tmp) | Out-Null
  }
}

$gwPolicy = @{
  Version = "2012-10-17"
  Statement = @(
    @{ Effect = "Allow"; Action = @("lambda:InvokeFunction"); Resource = "arn:aws:lambda:${Region}:${Account}:function:cre-deal-rentroll-gateway" },
    @{ Effect = "Allow"; Action = @("logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"); Resource = "*" }
  )
} | ConvertTo-Json -Depth 8
Set-Content -Path (Join-Path $env:TEMP "gw-pol.json") -Value $gwPolicy -Encoding utf8
& $aws iam put-role-policy --role-name CreDealRentrollGateway --policy-name gateway --policy-document ("file://{0}" -f (Join-Path $env:TEMP "gw-pol.json")) | Out-Null

$lamPolicy = @{
  Version = "2012-10-17"
  Statement = @(
    @{ Effect = "Allow"; Action = @("dynamodb:DescribeTable","dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:Scan"); Resource = "arn:aws:dynamodb:${Region}:${Account}:table/ml_interview" },
    @{ Effect = "Allow"; Action = @("logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"); Resource = "*" }
  )
} | ConvertTo-Json -Depth 8
Set-Content -Path (Join-Path $env:TEMP "lam-pol.json") -Value $lamPolicy -Encoding utf8
& $aws iam put-role-policy --role-name CreDealRentrollLambda --policy-name lambda-ddb --policy-document ("file://{0}" -f (Join-Path $env:TEMP "lam-pol.json")) | Out-Null

$evalPolicy = @{
  Version = "2012-10-17"
  Statement = @(
    @{ Effect = "Allow"; Action = @("logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogGroups","logs:FilterLogEvents","logs:StartQuery","logs:GetQueryResults"); Resource = "*" },
    @{ Effect = "Allow"; Action = @("bedrock-agentcore:Evaluate","bedrock:InvokeModel"); Resource = "*" }
  )
} | ConvertTo-Json -Depth 8
Set-Content -Path (Join-Path $env:TEMP "eval-pol.json") -Value $evalPolicy -Encoding utf8
& $aws iam put-role-policy --role-name CreDealRentrollEval --policy-name eval --policy-document ("file://{0}" -f (Join-Path $env:TEMP "eval-pol.json")) | Out-Null

Start-Sleep -Seconds 8

# --- Memory ---
$memOut = TryAws "create-memory" @(
  "bedrock-agentcore-control","create-memory",
  "--name","CreDealRentrollMem",
  "--description","CRE rent-roll session and fact memory",
  "--event-expiry-duration","30"
)
if (-not $memOut) {
  $listed = TryAws "list-memories" @("bedrock-agentcore-control","list-memories")
  if ($listed) { $memOut = $listed }
}
if ($memOut) {
  $m = $memOut | ConvertFrom-Json
  if ($m.memory) { $ids.MEMORY_ID = $m.memory.memoryId; $ids.MEMORY_ARN = $m.memory.arn }
  elseif ($m.memories) {
    $hit = @($m.memories) | Where-Object { $_.id -eq "CreDealRentrollMem" -or $_.memoryId -like "*CreDealRentrollMem*" -or $_.name -eq "CreDealRentrollMem" } | Select-Object -First 1
    if ($hit) { $ids.MEMORY_ID = $hit.id; if (-not $ids.MEMORY_ID) { $ids.MEMORY_ID = $hit.memoryId }; $ids.MEMORY_ARN = $hit.arn }
  }
}

# --- Policy engine ---
$pe = TryAws "create-policy-engine" @(
  "bedrock-agentcore-control","create-policy-engine",
  "--name","cre-deal-rentroll-policy",
  "--description","Allow CRE MCP tools on Gateway"
)
if ($pe) {
  $p = $pe | ConvertFrom-Json
  $ids.POLICY_ENGINE_ID = $p.policyEngineId; if (-not $ids.POLICY_ENGINE_ID) { $ids.POLICY_ENGINE_ID = $p.policyEngine.policyEngineId }
  $ids.POLICY_ENGINE_ARN = $p.policyEngineArn; if (-not $ids.POLICY_ENGINE_ARN) { $ids.POLICY_ENGINE_ARN = $p.policyEngine.policyEngineArn }
}
if ($ids.POLICY_ENGINE_ID) {
  $cedar = '{"cedar":{"statement":"permit (principal, action, resource);"}}'
  $cedarFile = Join-Path $env:TEMP "cedar.json"
  Set-Content $cedarFile $cedar -Encoding utf8
  $pol = TryAws "create-policy" @(
    "bedrock-agentcore-control","create-policy",
    "--name","allow-cre-tools",
    "--policy-engine-id",$ids.POLICY_ENGINE_ID,
    "--definition",$cedar,
    "--enforcement-mode","LOG_ONLY",
    "--validation-mode","IGNORE_ALL_FINDINGS"
  )
  if ($pol) {
    $pj = $pol | ConvertFrom-Json
    $ids.POLICY_ID = $pj.policyId; if (-not $ids.POLICY_ID) { $ids.POLICY_ID = $pj.policy.policyId }
  }
}

# --- Code interpreter + Browser ---
$ci = TryAws "create-code-interpreter" @(
  "bedrock-agentcore-control","create-code-interpreter",
  "--name","cre_deal_rentroll_ci",
  "--network-configuration","networkMode=PUBLIC"
)
if ($ci) { $c = $ci | ConvertFrom-Json; $ids.CODE_INTERPRETER_ID = $c.codeInterpreterId; if (-not $ids.CODE_INTERPRETER_ID) { $ids.CODE_INTERPRETER_ID = $c.codeInterpreter.codeInterpreterId } }

$br = TryAws "create-browser" @(
  "bedrock-agentcore-control","create-browser",
  "--name","cre_deal_rentroll_browser",
  "--network-configuration","networkMode=PUBLIC"
)
if ($br) { $b = $br | ConvertFrom-Json; $ids.BROWSER_ID = $b.browserId; if (-not $ids.BROWSER_ID) { $ids.BROWSER_ID = $b.browser.browserId } }

# Payments skipped — no microtransactions in this app.

# --- Lambda zip ---
$lamDir = Join-Path $env:TEMP "cre-gw-lambda"
Remove-Item $lamDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $lamDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot "lambda_gateway\handler.py") (Join-Path $lamDir "handler.py")
Copy-Item (Join-Path $Root "api\cre_rentroll") (Join-Path $lamDir "cre_rentroll") -Recurse
python -m pip install --disable-pip-version-check -q pydantic httpx ddgs openpyxl python-dotenv -t $lamDir
$zip = Join-Path $env:TEMP "cre-gw-lambda.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($lamDir, $zip)

$fnExists = & $aws lambda get-function --function-name cre-deal-rentroll-gateway 2>$null
if (-not $fnExists) {
  TryAws "create-function" @(
    "lambda","create-function",
    "--function-name","cre-deal-rentroll-gateway",
    "--runtime","python3.12",
    "--role","arn:aws:iam::${Account}:role/CreDealRentrollLambda",
    "--handler","handler.lambda_handler",
    "--timeout","60",
    "--memory-size","512",
    "--zip-file","fileb://$zip",
    "--environment","Variables={DDB_TABLE=ml_interview,AWS_REGION=us-east-1}"
  ) | Out-Null
} else {
  TryAws "update-function-code" @("lambda","update-function-code","--function-name","cre-deal-rentroll-gateway","--zip-file","fileb://$zip") | Out-Null
}
$ids.LAMBDA_ARN = "arn:aws:lambda:${Region}:${Account}:function:cre-deal-rentroll-gateway"

# --- Gateway ---
$gwArgs = @(
  "bedrock-agentcore-control","create-gateway",
  "--name","cre-deal-rentroll",
  "--role-arn","arn:aws:iam::${Account}:role/CreDealRentrollGateway",
  "--protocol-type","MCP",
  "--authorizer-type","AWS_IAM",
  "--protocol-configuration","mcp={supportedVersions=[2023-06-01],instructions=CRE rent-roll MCP tools,disableMcpListToolsPagination=true}"
)
if ($ids.POLICY_ENGINE_ARN) {
  $gwArgs += @("--policy-engine-configuration","arn=$($ids.POLICY_ENGINE_ARN),mode=LOG_ONLY")
}
$gw = TryAws "create-gateway" $gwArgs
if ($gw) {
  $g = $gw | ConvertFrom-Json
  $ids.GATEWAY_ID = $g.gatewayId; if (-not $ids.GATEWAY_ID) { $ids.GATEWAY_ID = $g.gateway.gatewayId }
  $ids.GATEWAY_ARN = $g.gatewayArn; if (-not $ids.GATEWAY_ARN) { $ids.GATEWAY_ARN = $g.gateway.gatewayArn }
  $ids.GATEWAY_URL = $g.gatewayUrl; if (-not $ids.GATEWAY_URL) { $ids.GATEWAY_URL = $g.gateway.gatewayUrl }
}

if ($ids.GATEWAY_ID) {
  $tools = Get-Content (Join-Path $PSScriptRoot "gateway-tools.json") -Raw
  $targetCfg = @{ mcp = @{ lambda = @{ lambdaArn = $ids.LAMBDA_ARN; toolSchema = @{ inlinePayload = ($tools | ConvertFrom-Json) } } } } | ConvertTo-Json -Depth 12 -Compress
  $tcFile = Join-Path $env:TEMP "gw-target.json"
  Set-Content $tcFile $targetCfg -Encoding utf8
  $cred = '[{"credentialProviderType":"GATEWAY_IAM_ROLE"}]'
  TryAws "create-gateway-target" @(
    "bedrock-agentcore-control","create-gateway-target",
    "--gateway-identifier",$ids.GATEWAY_ID,
    "--name","cre-tools",
    "--target-configuration","file://$tcFile",
    "--credential-provider-configurations",$cred
  ) | Out-Null
}

# --- Registry ---
$reg = TryAws "create-registry" @(
  "bedrock-agentcore-control","create-registry",
  "--registry-name","cre_deal_rentroll"
)
if ($reg) {
  $rj = $reg | ConvertFrom-Json
  $ids.REGISTRY_ID = $rj.registryId; if (-not $ids.REGISTRY_ID) { $ids.REGISTRY_ID = $rj.registry.registryId }
}
if ($ids.REGISTRY_ID) {
  # Canonical skill files live in the capabilities gateway. The running deal agent fetches them and does not read this registry.
  $SkillRoot = Join-Path (Split-Path -Parent $Root) "capabilities-gateway\skills"
  foreach ($sk in @("rent-roll-abstract","citation-policy","underwriting")) {
    $md = Get-Content (Join-Path $SkillRoot "$sk\SKILL.md") -Raw
    $desc = @{ agentSkills = @{ skillMd = @{ inlineContent = $md } } } | ConvertTo-Json -Depth 8 -Compress
    $df = Join-Path $env:TEMP "skill-$sk.json"
    Set-Content $df $desc -Encoding utf8
    TryAws "create-registry-record-$sk" @(
      "bedrock-agentcore-control","create-registry-record",
      "--registry-id",$ids.REGISTRY_ID,
      "--name",$sk,
      "--descriptor-type","AGENT_SKILLS",
      "--descriptors","file://$df"
    ) | Out-Null
  }
}

# --- Evaluator / online eval ---
$ids.EVALUATOR_ID = "Builtin.Faithfulness"
$oe = TryAws "create-online-evaluation-config" @(
  "bedrock-agentcore-control","create-online-evaluation-config",
  "--online-evaluation-config-name","cre_deal_rentroll_online",
  "--rule","samplingConfig={samplingPercentage=100.0}",
  "--data-source-config","cloudWatchLogs={logGroupNames=[/aws/bedrock-agentcore/runtimes/cre_deal_rentroll-Nh63z6GXmK-DEFAULT]}",
  "--evaluators","evaluatorId=Builtin.Faithfulness",
  "--evaluation-execution-role-arn","arn:aws:iam::${Account}:role/CreDealRentrollEval",
  "--enable-on-create"
)
if ($oe) { $oj = $oe | ConvertFrom-Json; $ids.ONLINE_EVAL_ID = $oj.onlineEvaluationConfigId }

# --- Harness ---
$rt = TryAws "get-agent-runtime" @("bedrock-agentcore-control","get-agent-runtime","--agent-runtime-id","cre_deal_rentroll-Nh63z6GXmK")
$openaiArn = $null
if ($rt) {
  $rtj = $rt | ConvertFrom-Json
  $key = $rtj.environmentVariables.OPENAI_API_KEY
  if ($key) {
    $secName = "cre-deal-rentroll-openai"
    $existing = & $aws secretsmanager describe-secret --secret-id $secName 2>$null
    if (-not $existing) {
      $created = TryAws "create-secret" @("secretsmanager","create-secret","--name",$secName,"--secret-string",$key)
      if ($created) { $openaiArn = ($created | ConvertFrom-Json).ARN }
    } else {
      TryAws "put-secret" @("secretsmanager","put-secret-value","--secret-id",$secName,"--secret-string",$key) | Out-Null
      $openaiArn = ($existing | ConvertFrom-Json).ARN
      if (-not $openaiArn) { $openaiArn = "arn:aws:secretsmanager:${Region}:${Account}:secret:$secName" }
    }
  }
}
$harnessEnv = '{"agentCoreRuntimeEnvironment":{"networkConfiguration":{"networkMode":"PUBLIC"}}}'
$artifact = '{"containerConfiguration":{"containerUri":"226721156429.dkr.ecr.us-east-1.amazonaws.com/cre-deal-rentroll-agentcore:latest"}}'
$model = '{"openAiModelConfig":{"modelId":"gpt-4o-mini"}}'
if ($openaiArn) { $model = ('{"openAiModelConfig":{"modelId":"gpt-4o-mini","apiKeyArn":"' + $openaiArn + '"}}') }
$hArgs = @(
  "bedrock-agentcore-control","create-harness",
  "--harness-name","cre_deal_rentroll",
  "--execution-role-arn","arn:aws:iam::${Account}:role/CreDealRentrollAgentCoreRuntime",
  "--environment",$harnessEnv,
  "--environment-artifact",$artifact,
  "--model",$model
)
$har = TryAws "create-harness" $hArgs
if ($har) {
  $hj = $har | ConvertFrom-Json
  $ids.HARNESS_ID = $hj.harnessId; if (-not $ids.HARNESS_ID) { $ids.HARNESS_ID = $hj.harness.harnessId }
  $ids.HARNESS_ARN = $hj.harnessArn; if (-not $ids.HARNESS_ARN) { $ids.HARNESS_ARN = $hj.harness.harnessArn }
}

$ids.WORKLOAD_NAME = "cre_deal_rentroll-Nh63z6GXmK"
$ids.RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:226721156429:runtime/cre_deal_rentroll-Nh63z6GXmK"
($ids | ConvertTo-Json -Depth 6) | Set-Content $Out -Encoding utf8
Write-Host "`nWrote $Out"
$ids | ConvertTo-Json -Depth 6
