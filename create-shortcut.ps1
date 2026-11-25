# Creates a desktop shortcut "Local Agent (lca)" with the project icon.
$root = $PSScriptRoot
$desk = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut("$desk\Local Agent (lca).lnk")
$s.TargetPath = "$root\lca.cmd"
$s.WorkingDirectory = $env:USERPROFILE
if (Test-Path "$root\lca.ico") { $s.IconLocation = "$root\lca.ico,0" }
$s.Description = "Local LLM agent"
$s.Save()
Write-Host "Shortcut created on your desktop."
