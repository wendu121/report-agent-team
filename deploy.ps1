# deploy.ps1 — report-agent-team 一键部署（在宿主 Windows 终端以普通用户运行，无需管理员）
# 用途：构建并启动 api + web（compose 自动拉起依赖 postgres/redis），后端走 PyPI 清华镜像加速。
$ErrorActionPreference = "Continue"
$ROOT = "E:\第二电脑\report-agent-team"
Set-Location $ROOT

Write-Host "==> [0/3] 检查 Docker daemon ..." -ForegroundColor Cyan
try {
    docker info *> $null
} catch {
    Write-Host "✗ Docker daemon 未运行。请先启动 Docker Desktop（GUI 勾选 'Start when you sign in' 防复发），再重跑本脚本。" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Docker OK" -ForegroundColor Green

Write-Host "==> [1/3] 构建 api / web 镜像（PyPI 清华镜像加速；前端 npm 默认源，若慢见文件末注）..." -ForegroundColor Cyan
docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple api web
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ 构建失败，见上方错误。" -ForegroundColor Red
    Write-Host "  前端 npm 慢/失败时，编辑 web/Dockerfile 第6行改为:" -ForegroundColor Yellow
    Write-Host '    RUN npm ci --registry=https://registry.npmmirror.com || npm install --registry=https://registry.npmmirror.com' -ForegroundColor Yellow
    exit 1
}
Write-Host "✓ 构建完成" -ForegroundColor Green

Write-Host "==> [2/3] 启动 api / web（+postgres/redis）..." -ForegroundColor Cyan
docker compose up -d api web
docker compose ps

Write-Host "==> [3/3] 等待 api 健康检查（最多 60s）..." -ForegroundColor Cyan
$ok = $false
for ($i=0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    docker compose exec -T api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
}
if ($ok) {
    Write-Host "✓ 部署成功！浏览器打开 http://localhost:18080" -ForegroundColor Green
    Write-Host "  测『经验/反思』面板：设置 → 经验/反思（反思建议 + 经验草稿双 Tab，可采纳/驳回）" -ForegroundColor White
    Write-Host "  测真实 LLM 调 MCP：对话『查一下 XXX 最新进展』看 tool_calls / sources" -ForegroundColor White
    Write-Host "  测自主学习：『记住：…』生成经验草稿 → 设置里采纳后生效" -ForegroundColor White
    Write-Host "  测跨会话召回：新开会话问相关话题，看是否召回历史知识" -ForegroundColor White
} else {
    Write-Host "! /health 未就绪，查日志：docker compose logs api" -ForegroundColor Yellow
}
