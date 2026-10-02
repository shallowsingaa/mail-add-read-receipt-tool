$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '先创建 .venv 并安装 client、dev 依赖，见 README。' }
& $taskPython -m PyInstaller --noconfirm --clean --onefile --console --name receipt-client --collect-all textual client_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }
Copy-Item -LiteralPath client.example.toml -Destination dist/client.toml
Copy-Item -LiteralPath README.md -Destination dist/README.md
Copy-Item -LiteralPath LICENSE -Destination dist/LICENSE
Copy-Item -LiteralPath VALIDATION.md -Destination dist/VALIDATION.md
New-Item -ItemType Directory -Path dist/docs -Force | Out-Null
Copy-Item -Path docs/*.md -Destination dist/docs
& .\dist\receipt-client.exe --smoke-test
if ($LASTEXITCODE -ne 0) { throw 'exe 运行时验证失败' }
Compress-Archive -Path dist/receipt-client.exe,dist/client.toml,dist/README.md,dist/LICENSE,dist/VALIDATION.md,dist/docs -DestinationPath dist/receipt-client-windows-x64.zip -Force
