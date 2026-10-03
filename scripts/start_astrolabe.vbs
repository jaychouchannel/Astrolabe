' Astrolabe auto-start: launch server hidden, then open dashboard in browser.
' Runs at Windows logon (copy lives in the Startup folder).
Option Explicit
Dim shell, repoDir, wmi, procs, running, winhttp, i

repoDir = "D:\Astrolabe"
Set shell = CreateObject("WScript.Shell")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

' Already running? (any python.exe whose command line mentions astrolabe.app)
running = False
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='python.exe' AND CommandLine LIKE '%astrolabe.app%'")
If procs.Count > 0 Then running = True

If running Then
    shell.Run "http://127.0.0.1:8765"
    WScript.Quit
End If

' Start the hidden server (python.exe writes logs to server.log)
shell.Run """" & repoDir & "\scripts\run_server.bat""", 0, False

' Wait until it answers (direct connection, bypass any system proxy)
For i = 1 To 15
    WScript.Sleep 2000
    Set winhttp = CreateObject("WinHttp.WinHttpRequest.5.1")
    winhttp.SetProxy 0
    On Error Resume Next
    winhttp.open "GET", "http://127.0.0.1:8765/api/state", False
    winhttp.send
    If Err.Number = 0 Then
        If winhttp.Status = 200 Then Exit For
    End If
    Err.Clear
    On Error GoTo 0
Next

shell.Run "http://127.0.0.1:8765"
