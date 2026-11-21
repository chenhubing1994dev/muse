$ErrorActionPreference = 'Stop'
npm test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run check
npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'VIDEO_VISUAL_REVIEW_UI_VALIDATION_OK'
