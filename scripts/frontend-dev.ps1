# 프론트엔드(Vite) 개발 서버만 단독 실행합니다. 백엔드는 별도로 실행하세요.
$frontend = (Resolve-Path (Join-Path $PSScriptRoot '..\frontend')).Path
$globalNode = Get-Command node -ErrorAction SilentlyContinue
$portableNode = Join-Path $frontend '.local_tools\node\node-v24.22.3-win-x64\node.exe'
$viteEntry = Join-Path $frontend 'node_modules\vite\bin\vite.js'

Set-Location -LiteralPath $frontend
if ($globalNode -and (Test-Path -LiteralPath $viteEntry)) {
    & $globalNode.Source $viteEntry --host 127.0.0.1
} elseif ((Test-Path -LiteralPath $portableNode) -and (Test-Path -LiteralPath $viteEntry)) {
    & $portableNode $viteEntry --host 127.0.0.1
} else {
    npm.cmd run dev
}
