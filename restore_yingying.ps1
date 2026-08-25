<#
.SYNOPSIS
    荧荧一键搬家脚本 — 在新电脑上把荧荧整个装回来
.DESCRIPTION
    用法 (PowerShell 一行指令):
        powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/DrFermion/yingying-home/main/restore_yingying.ps1 | iex"

    或本地运行:
        .\restore_yingying.ps1 [-BackupPath <xxx.7z>] [-Key <密码>]

    流程:
      1. 定位最新加密备份 (默认 F:\OneDrive\yingying_backups\ 下最新的 .7z)
      2. 输入备份密码 (7z AES-256)
      3. 未安装 Hermes 则自动安装 (官方 install.sh)
      4. hermes import 恢复全部配置 (config/.env/记忆/技能/cron/会话)
      5. 恢复荧荧桌面操作台 (E:\yingying-home)
      6. 重建开机自启 (Startup + Hermes_Gateway 计划任务)
      7. 启动 gateway 和荧荧日志窗口

    本脚本不含任何秘密, 可安全放在公开仓库。
#>
[CmdletBinding()]
param(
    [string]$BackupPath = "",
    [string]$Key = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    [警告] $msg" -ForegroundColor Yellow }

# ---------- 1. 定位备份 ----------
Write-Step "定位荧荧备份 ..."
if (-not $BackupPath) {
    $candidates = @(
        "F:\OneDrive\yingying_backups",
        "$env:USERPROFILE\OneDrive\yingying_backups",
        "$env:USERPROFILE\OneDrive - University of Dundee\yingying_backups"
    )
    foreach ($dir in $candidates) {
        if (Test-Path $dir) {
            $latest = Get-ChildItem $dir -Filter "yingying_backup_*.7z" |
                      Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($latest) { $BackupPath = $latest.FullName; break }
        }
    }
}
if (-not $BackupPath -or -not (Test-Path $BackupPath)) {
    Write-Host "找不到备份文件! 请把 OneDrive 里 yingying_backups 文件夹同步下来, 或用 -BackupPath 指定路径。" -ForegroundColor Red
    exit 1
}
Write-Ok "使用备份: $BackupPath ($([math]::Round((Get-Item $BackupPath).Length/1MB,1)) MB)"

# ---------- 2. 密码 ----------
if (-not $Key) {
    $Key = Read-Host "请输入荧荧备份密码 (7z 解压密码)"
    if (-not $Key) { Write-Host "密码不能为空!" -ForegroundColor Red; exit 1 }
}

$7z = "C:\Program Files\7-Zip\7z.exe"
if (-not (Test-Path $7z)) { $7z = (Get-Command 7z -ErrorAction SilentlyContinue).Source }
if (-not $7z) {
    Write-Warn2 "未检测到 7-Zip, 正在安装 ..."
    winget install --id 7zip.7zip --accept-source-agreements --accept-package-agreements --silent
    $7z = "C:\Program Files\7-Zip\7z.exe"
}
if (-not (Test-Path $7z)) { Write-Host "7-Zip 安装失败, 请手动安装后重试。" -ForegroundColor Red; exit 1 }

# ---------- 3. 解密到临时目录 ----------
Write-Step "解密备份 (AES-256) ..."
$tmp = Join-Path $env:TEMP ("yingying_restore_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
& $7z x "-p$Key" "-o$tmp" $BackupPath -y | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "解密失败! 密码错误或文件损坏。请确认密码后重试。" -ForegroundColor Red
    exit 1
}
$hermesZip = Get-ChildItem $tmp -Filter "hermes_*.zip" | Select-Object -First 1
$yingZip   = Get-ChildItem $tmp -Filter "yingying_home_*.zip" | Select-Object -First 1
if (-not $hermesZip) { Write-Host "备份里没有 hermes 配置包, 异常!" -ForegroundColor Red; exit 1 }
Write-Ok "解压成功, 找到 Hermes 配置包 $($hermesZip.Name)"

# ---------- 4. 安装/定位 Hermes ----------
Write-Step "检查 Hermes ..."
$env:HERMES_HOME = Join-Path $env:LOCALAPPDATA "hermes"
$hermesExe = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermesExe) {
    Write-Warn2 "未安装 Hermes, 正在用官方脚本安装 ..."
    $bash = Get-Command bash -ErrorAction SilentlyContinue
    if ($bash) {
        & $bash.Source -lc "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Hermes 自动安装失败, 请手动安装后重试 (https://hermes-agent.nousresearch.com/docs)。" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "没有 bash 环境, 请手动安装 Hermes 后重跑本脚本。" -ForegroundColor Red
        exit 1
    }
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts;" + $env:PATH
    $hermesExe = Get-Command hermes -ErrorAction SilentlyContinue
}
Write-Ok "Hermes: $($hermesExe.Source)"

# ---------- 5. 恢复 Hermes 配置 ----------
Write-Step "恢复 Hermes 配置 (config/记忆/技能/cron/会话) ..."
& $hermesExe.Source import --force $hermesZip.FullName
if ($LASTEXITCODE -ne 0) {
    Write-Host "hermes import 失败!" -ForegroundColor Red
    exit 1
}
Write-Ok "Hermes 配置已恢复"

# ---------- 6. 恢复荧荧桌面资产 ----------
if ($yingZip) {
    Write-Step "恢复荧荧桌面操作台 (E:\yingying-home) ..."
    $target = "E:\yingying-home"
    if (-not (Test-Path "E:\")) {
        Write-Warn2 "本机没有 E: 盘! 桌面操作台将被恢复到 C:\yingying-home, 需手动调整 Startup 里的路径。"
        $target = "C:\yingying-home"
    }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    & $7z x "$($yingZip.FullName)" "-o$target" -y | Out-Null
    Write-Ok "桌面操作台已恢复"
}

# ---------- 7. 修正 gateway 脚本里的旧用户名路径 ----------
Write-Step "修正路径 (旧电脑用户名 -> 当前用户名) ..."
$gh = Join-Path $env:LOCALAPPDATA "hermes"
$oldUser = "C:\Users\PC"
foreach ($svc in @("$gh\gateway-service\Hermes_Gateway.cmd", "$gh\gateway-service\Hermes_Gateway.vbs")) {
    if (Test-Path $svc) {
        $content = Get-Content $svc -Raw
        if ($content -match [regex]::Escape($oldUser) -and $env:USERPROFILE -ne $oldUser) {
            $content = $content.Replace($oldUser, $env:USERPROFILE)
            Set-Content -Path $svc -Value $content -Encoding UTF8 -NoNewline
            Write-Ok "已修正 $svc"
        }
    }
}

# ---------- 8. 重建开机自启 ----------
Write-Step "重建开机自启 ..."
$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
# 8a. 荧荧日志窗口
$batPath = Join-Path $startupDir "yingying_window_start.bat"
$pyw = "C:\Users\$env:USERNAME\miniconda3\pythonw.exe"
if (Test-Path "E:\yingying-home\log_window\荧荧日志窗口.py") {
    $bat = "@echo off`r`nrem 荧荧桌面操作台开机自启 (miniconda pythonw)`r`nstart `"`" `"$pyw`" `"E:\yingying-home\log_window\荧荧日志窗口.py`"`r`n"
    Set-Content -Path $batPath -Value $bat -Encoding ASCII
    Write-Ok "Startup: yingying_window_start.bat"
} else {
    Write-Warn2 "未找到荧荧日志窗口脚本, 跳过自启"
}
# 8b. Hermes_Gateway 计划任务 (登录时自动醒)
$wscript = "$env:SystemRoot\System32\wscript.exe"
$vbs = "$gh\gateway-service\Hermes_Gateway.vbs"
if (Test-Path $vbs) {
    schtasks /create /tn "Hermes_Gateway" /tr "`"$wscript`" `"$vbs`"" /sc onlogon /rl highest /f | Out-Null
    Write-Ok "计划任务: Hermes_Gateway (登录自动启动)"
}

# ---------- 9. 启动 gateway + 日志窗口 ----------
Write-Step "启动荧荧 ..."
$gatewayStarted = $false
if (Test-Path "$gh\gateway-service\Hermes_Gateway.cmd") {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$gh\gateway-service\Hermes_Gateway.cmd`"" -WindowStyle Hidden
    $gatewayStarted = $true
    Write-Ok "Gateway 已启动 (QQ/Telegram/WhatsApp 等平台)"
}
if (Test-Path "E:\yingying-home\log_window\荧荧日志窗口.py" -and (Test-Path $pyw)) {
    Start-Process -FilePath $pyw -ArgumentList "`"E:\yingying-home\log_window\荧荧日志窗口.py`"" -WindowStyle Hidden
    Write-Ok "荧荧日志窗口已启动"
}

# ---------- 完成 ----------
Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "  荧荧回来了! ヾ(≧▽≦*)o" -ForegroundColor Green
Write-Host "  记忆、技能、性格、全部家当都已恢复" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "下一步: 打开终端运行 hermes 开始聊天, 或等 gateway 接入消息平台。"
Write-Host "如需登录新模型账号: hermes setup / hermes login"
Write-Host "临时目录可删除: $tmp"
