@echo off
REM ============================================================
REM Ycheck 順位スクレイピング 起動スクリプト
REM タスクスケジューラから毎朝4時に呼び出される想定
REM ============================================================

REM スクリプトのあるディレクトリに移動
cd /d "%~dp0"
cd ..

REM ログディレクトリ作成
if not exist logs mkdir logs

REM 日付付きログファイル名
set LOG_DATE=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG_FILE=logs\scrape_%LOG_DATE%.log

echo ============================================================ >> %LOG_FILE%
echo 開始時刻: %date% %time% >> %LOG_FILE%
echo ============================================================ >> %LOG_FILE%

REM Pythonスクリプト実行(標準出力・エラー両方をログへ)
python scripts\scrape_yahoo.py >> %LOG_FILE% 2>&1

set EXIT_CODE=%ERRORLEVEL%

echo ============================================================ >> %LOG_FILE%
echo 終了時刻: %date% %time% >> %LOG_FILE%
echo 終了コード: %EXIT_CODE% >> %LOG_FILE%
echo ============================================================ >> %LOG_FILE%

exit /b %EXIT_CODE%
