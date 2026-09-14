@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ===== HerPulse 本地采集 + 自动推送 =====
echo.

REM 1. 采集（本地有代理，Reddit + AO3 都能正常访问）
echo [1/3] 正在采集 Reddit + AO3 ...
bash deploy/fetch_only.sh
if %errorlevel% neq 0 (
    echo [ERROR] 采集失败，停止推送
    pause
    exit /b 1
)

REM 2. 提交语料
echo [2/3] 正在提交语料到仓库 ...
git add data\
git commit -m "data: daily corpus"

REM 3. 推送（触发 GitHub Actions 自动跑后半段）
echo [3/3] 正在推送到 GitHub ...
git push

echo.
echo ===== 完成 =====
echo 请等待 3-5 分钟后访问：https://alpha041027.github.io/Trending-topics/
pause
