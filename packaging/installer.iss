; Inno Setup script — OrionQ console installer.
;
;   tools\build_windows_console.ps1 supplies the canonical pyproject version.
;
; Produces packaging\output\OrionQ-Setup-<version>.exe
; Per-user install by default: no administrator prompt, no UAC shield.

#ifndef AppName
  #define AppName "OrionQ"
#endif
#ifndef AppExe
  #define AppExe "OrionQ.exe"
#endif
#define AppPublisher "OrionQ"
#ifndef AppVersion
  #error "Supply /DAppVersion from pyproject.toml using tools/build_windows_console.ps1"
#endif
#ifndef SourceRoot
  #define SourceRoot "..\dist\OrionQ"
#endif

[Setup]
AppId={{8F2A6C31-4D5E-4A77-9E1B-7C0A9D3F51B2}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
UsePreviousAppDir=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=console.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
InfoAfterFile=README-WINDOWS.txt

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Only files owned by the previous installer, inside its reused installation.
; Persistent journals and preserved portable releases are never deleted.
Type: files; Name: "{app}\ORION-Q.exe"
; External legacy shortcuts may belong to a portable release. Preserve them;
; [Icons] creates the new OrionQ shortcuts without deleting the old ones.

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "ORION-Q.Desktop"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "ORION-Q.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The SQLite journal lives in %LOCALAPPDATA% and is deliberately NOT removed:
; it is the operator's audit record. Delete it by hand if you mean to.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
