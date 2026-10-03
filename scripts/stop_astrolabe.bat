@echo off
rem Stop the Astrolabe server (matches only the astrolabe python process)
wmic process where "name='python.exe' and CommandLine like '%%astrolabe.app%%'" call terminate >nul 2>&1
echo Astrolabe stopped.
pause
