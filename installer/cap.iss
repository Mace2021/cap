; Windows installer for C.A.P. (Inno Setup 6). build.py runs this after PyInstaller:
;   ISCC.exe /DAppVersion=2.2.2 /DSourceDir=..\dist\CAP /DOutputDir=..\dist installer\cap.iss
; Produces dist\CAP-<version>-windows-x64-setup.exe.
;
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\CAP, adds a Start menu entry
; and an uninstaller. Silent install for the Microsoft Store and IT tools:
;   CAP-<version>-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
; Silent uninstall: "%LOCALAPPDATA%\Programs\CAP\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
; The user's data (%LOCALAPPDATA%\CAP) is kept on uninstall, so reinstalling or updating loses nothing.
;
; Signing: when SignTool is defined (/DSignTool=...), the installer, uninstaller and setup are signed with it. See
; build.md for the certificate setup.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\CAP"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef Publisher
  ; Must match the name on the code-signing certificate once you have one.
  #define Publisher "C.A.P."
#endif

[Setup]
; Never change AppId: Windows uses it to recognise updates and the uninstaller.
AppId={{8F3C2A61-4B7E-4D2A-9C55-3E1B7A0D6F42}
AppName=C.A.P. Inbox Assistant
AppVersion={#AppVersion}
AppVerName=C.A.P. Inbox Assistant {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL=https://mace2021.github.io/cap/
AppSupportURL=https://mace2021.github.io/cap/#feedback
AppUpdatesURL=https://mace2021.github.io/cap/#download
VersionInfoVersion={#AppVersion}
VersionInfoProductName=C.A.P. Inbox Assistant
VersionInfoDescription=C.A.P. Inbox Assistant Setup
DefaultDirName={autopf}\CAP
DefaultGroupName=C.A.P.
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=CAP-{#AppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\packaging\icon\cap.ico
UninstallDisplayName=C.A.P. Inbox Assistant
UninstallDisplayIcon={app}\CAP.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
#ifdef SignTool
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the whole app folder, so files dropped by a newer PyInstaller never mix with old ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\C.A.P. Inbox Assistant"; Filename: "{app}\CAP.exe"
Name: "{autodesktop}\C.A.P. Inbox Assistant"; Filename: "{app}\CAP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CAP.exe"; Description: "{cm:LaunchProgram,C.A.P. Inbox Assistant}"; Flags: nowait postinstall skipifsilent
