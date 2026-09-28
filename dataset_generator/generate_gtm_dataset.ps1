param(
    [string]$InputCsv = "..\assurex_nigeria_warranty_claims_v2.csv",
    [string]$OutputDir = ".\gtm_dataset",
    [int]$TrainVariants = 2,
    [int]$Workers = 8
)

python .\claim_card_generator.py `
  --input $InputCsv `
  --output $OutputDir `
  --train-variants $TrainVariants `
  --workers $Workers `
  --overwrite
