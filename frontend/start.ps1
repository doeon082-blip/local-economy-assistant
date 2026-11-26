$globalNode = Get-Command node -ErrorAction SilentlyContinue
$portableNode = Join-Path $PSScriptRoot '.local_tools\node\node-v24.22.3-win-x64\node.exe'
$viteEntry = Join-Path $PSScriptRoot 'node_modules\vite\bin\vite.js'

if ($globalNode -and (Test-Path -LiteralPath $viteEntry)) {
    & $globalNode.Source $viteEntry --host 127.0.0.1
} elseif ((Test-Path -LiteralPath $portableNode) -and (Test-Path -LiteralPath $viteEntry)) {
    & $portableNode $viteEntry --host 127.0.0.1
} else {
    npm.cmd run dev
}
