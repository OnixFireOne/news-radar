# Проверка баланса токенов API
# Перед запуском замените YOUR_API_KEY на ваш реальный ключ

$headers = @{
    "Authorization" = "Bearer sk-funpay-gd0s6nKfecBT2ORiOu6MLw0wJJlzBhW3Mofa8oj1Yt2beiwS"
}

$response = Invoke-WebRequest -Uri "https://api.gloyai.fun/claude/key" -Headers $headers -UseBasicParsing

$data = $response.Content | ConvertFrom-Json

Write-Host "=== Баланс токенов ===" -ForegroundColor Cyan
Write-Host "Доступно токенов: $($data.tokens_balance)" -ForegroundColor Green
Write-Host "Использовано токенов: $($data.tokens_used)" -ForegroundColor Yellow
Write-Host "Количество запросов: $($data.requests_count)" -ForegroundColor Magenta