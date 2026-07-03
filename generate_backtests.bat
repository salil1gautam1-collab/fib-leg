@echo off
REM Generates every Backtest-tab combination (5 timeframes x 3 exits, 11-yr data)
REM and publishes them to the app. Run on the PC that holds fibleg\data\Stocks_data.
REM Takes ~2-3 hours; leave the window open, progress prints as it goes.
cd /d "%~dp0"
"C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe" gen_backtest_all.py "fibleg/data/Stocks_data"
if errorlevel 1 (
  echo.
  echo Generation FAILED - nothing was published. See the error above.
  pause
  exit /b 1
)
git add docs/backtest_*.json
git commit -m "backtests: regenerate all TF x exit combos"
git pull --rebase origin main
git push origin main
echo.
echo Done - the app's Backtest tab will show every combo after the Pages deploy (~1 min).
pause
