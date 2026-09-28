param(
    [string]$InputCsv = "..\assurex_nigeria_warranty_claims_v2.csv",
    [string]$OutputDir = ".\gtm_dataset",
    [int]$TrainVariants = 2,
    [int]$Workers = 8
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$InputPath = if ([System.IO.Path]::IsPathRooted($InputCsv)) { $InputCsv } else { Join-Path $ScriptRoot $InputCsv }
$OutputPath = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $ScriptRoot $OutputDir }
$Generator = Join-Path $ScriptRoot "claim_card_generator.py"

if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
  throw "Input CSV not found: $InputPath"
}
if (-not (Test-Path -LiteralPath $Generator -PathType Leaf)) {
  throw "Dataset generator not found: $Generator"
}

& python $Generator --input $InputPath --output $OutputPath --train-variants $TrainVariants --workers $Workers --overwrite
if ($LASTEXITCODE -ne 0) { throw "Dataset generation failed with exit code $LASTEXITCODE" }
