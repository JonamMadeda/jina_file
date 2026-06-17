[Setup]
AppName=jina_file
AppVersion=1.1.0
AppPublisher=jina_file
DefaultDirName={autopf}\jina_file
DefaultGroupName=jina_file
AllowNoIcons=yes
OutputDir=.
OutputBaseFilename=jina_file_setup
Compression=lzma2
SolidCompression=yes
Uninstallable=yes
PrivilegesRequired=admin
SetupIconFile=jf_icon.ico

[Files]
Source: "jina_file.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\jina_file"; Filename: "{app}\jina_file.exe"
Name: "{commondesktop}\jina_file"; Filename: "{app}\jina_file.exe"

[Run]
Filename: "{app}\jina_file.exe"; Description: "Launch jina_file"; Flags: postinstall nowait skipifsilent
