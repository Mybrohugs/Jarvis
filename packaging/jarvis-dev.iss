#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#define AppVersion "1.4.3"
[Setup]
AppId={{D29153E1-6143-439A-9F9C-C32C68982D13}
AppName=JARVIS DEV Station
AppVersion={#AppVersion}
AppPublisher=Mybrohugs
AppPublisherURL=https://github.com/Mybrohugs/Jarvis
AppSupportURL=https://github.com/Mybrohugs/Jarvis/issues
DefaultDirName={localappdata}\JARVIS-DEV-Station
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=JARVIS-Setup-v{#AppVersion}-windows-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
SetupMutex=JARVIS_DEV_Station_Setup
CloseApplications=no
RestartApplications=no
UninstallDisplayName=JARVIS DEV Station
UninstallDisplayIcon={app}\versions\{#AppVersion}\JARVIS-DEV.exe
UninstallFilesDir={app}\uninstall
LicenseFile={#BundleDir}\LICENSE
AppComments=ChatGPT text and video-to-TV DEV station. User settings are preserved on removal.
VersionInfoVersion=1.4.3.0
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{userprograms}\JARVIS DEV Station\JARVIS DEV Station"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\Run-JARVIS-DEV.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\versions\{#AppVersion}\JARVIS-DEV.exe"
Name: "{userdesktop}\JARVIS DEV Station"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\Run-JARVIS-DEV.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\versions\{#AppVersion}\JARVIS-DEV.exe"; Tasks: desktopicon
[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\Run-JARVIS-DEV.ps1"""; Description: "{cm:LaunchProgram,JARVIS DEV Station}"; Flags: postinstall nowait skipifsilent unchecked
[UninstallDelete]
Type: files; Name: "{app}\Run-JARVIS-DEV.ps1"
Type: files; Name: "{app}\Run-JARVIS-DEV.cmd"
Type: files; Name: "{app}\current.json"
Type: files; Name: "{app}\setup-state.json"
[CustomMessages]
russian.CloseStation=Закройте окно JARVIS DEV Station и повторите установку. Работающие службы изменяться не будут.
english.CloseStation=Close JARVIS DEV Station and retry setup. Running services will not be changed.
russian.UnsafePath=Установка возможна только в собственную папку JARVIS-DEV-Station без символических ссылок.
english.UnsafePath=Setup requires the dedicated JARVIS-DEV-Station folder without symbolic links.
[Code]
function GetFileAttributesW(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function CreateFileW(Name: String; Access, Share: LongWord; Security: Integer;
  Creation, Flags: LongWord; Template: Integer): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function SetEnvironmentVariableW(Name, Value: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function LinkFreeChain(Dir: String): Boolean;
var Parent: String; Attr: LongWord;
begin
  Result := False;
  while Dir <> '' do begin
    Attr := GetFileAttributesW(Dir);
    if (Attr <> $FFFFFFFF) and ((Attr and $400) <> 0) then Exit;
    Parent := ExtractFileDir(Dir);
    if Parent = Dir then Break;
    Dir := Parent;
  end;
  Result := True;
end;

function LinkFreeDirectory(Dir: String; Recurse: Boolean): Boolean;
var Entry: TFindRec;
begin
  Result := False;
  if not LinkFreeChain(Dir) then Exit;
  if FileExists(Dir) then Exit;
  if FindFirst(AddBackslash(Dir) + '*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          if (Entry.Attributes and $400) <> 0 then Exit;
          if Recurse and ((Entry.Attributes and $10) <> 0) then
            if not LinkFreeDirectory(AddBackslash(Dir) + Entry.Name, True) then Exit;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Root, LockPath: String; Handle: THandle;
begin
  Result := '';
  Root := ExpandFileName(WizardDirValue);
  if (CompareText(RemoveBackslashUnlessRoot(Root),
      ExpandConstant('{localappdata}\JARVIS-DEV-Station')) <> 0) or
      not LinkFreeDirectory(Root, False) or
      not LinkFreeDirectory(Root + '\versions\{#AppVersion}', True) then begin
    Result := CustomMessage('UnsafePath'); Exit;
  end;
  LockPath := Root + '\data\desktop-instance.lock';
  if FileExists(LockPath) then begin
    Handle := CreateFileW(LockPath, $80000000, 0, 0, 3, 0, 0);
    if Handle = THandle(-1) then begin
      Result := CustomMessage('CloseStation'); Exit;
    end;
    CloseHandle(Handle);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer; OldRoot, OldShortcut: String;
begin
  if CurStep = ssPostInstall then begin
    OldRoot := GetEnv('JARVIS_DEV_INSTALL_ROOT');
    OldShortcut := GetEnv('JARVIS_DEV_SKIP_SHORTCUT');
    SetEnvironmentVariableW('JARVIS_DEV_INSTALL_ROOT', ExpandConstant('{app}'));
    SetEnvironmentVariableW('JARVIS_DEV_SKIP_SHORTCUT', '1');
    try
      if not Exec(ExpandConstant('{app}\versions\{#AppVersion}\JARVIS-DEV.exe'),
        '--install --quiet --output "' + ExpandConstant('{app}\setup-state.json') + '"',
        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
        RaiseException('Cannot initialize JARVIS DEV Station');
      if Code <> 0 then RaiseException('JARVIS package verification failed');
    finally
      SetEnvironmentVariableW('JARVIS_DEV_INSTALL_ROOT', OldRoot);
      SetEnvironmentVariableW('JARVIS_DEV_SKIP_SHORTCUT', OldShortcut);
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := LinkFreeDirectory(ExpandConstant('{app}'), False) and
    LinkFreeDirectory(ExpandConstant('{app}\versions\{#AppVersion}'), True);
  if not Result then MsgBox(CustomMessage('UnsafePath'), mbError, MB_OK);
end;
