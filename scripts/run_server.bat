@echo off
rem Launch the Astrolabe server; log to server.log (used by start_astrolabe.vbs)
cd /d D:\Astrolabe
.venv\Scripts\python.exe -m astrolabe.app > server.log 2>&1
