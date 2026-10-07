; AeroBench setup — post-install steps for the bundled third-party applications.
; Included by electron-builder (package.json → build.nsis.include). The installer runs
; elevated (per-machine install), so Microsoft MPI is installed without another prompt.

!macro customInstall
  ; --- 1. ParaView: recreate the duplicate DLLs removed from the package as hard links ----
  DetailPrint "Completing the ParaView installation..."
  nsExec::ExecToLog '"$INSTDIR\resources\python\python.exe" -I "$INSTDIR\resources\tools\restore_links.py" "$INSTDIR\resources"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "ParaView file restoration reported code $0 (see resources\tools\restore_links.log)"
    MessageBox MB_ICONEXCLAMATION|MB_OK "Some ParaView files could not be restored (code $0).$\r$\nAeroBench works, but if result images fail, run this setup again to repair the installation." /SD IDOK
  ${EndIf}

  ; --- 2. Microsoft MPI (needed by every OpenFOAM program of the Windows build) -----------
  ${IfNot} ${FileExists} "$PROGRAMFILES64\Microsoft MPI\Bin\mpiexec.exe"
    DetailPrint "Installing Microsoft MPI 10.1.3..."
    SetOutPath "$INSTDIR\resources\redist"
    ExecWait '"$INSTDIR\resources\redist\msmpisetup.exe" -unattend' $1
    SetOutPath "$INSTDIR"
    ${If} ${FileExists} "$PROGRAMFILES64\Microsoft MPI\Bin\mpiexec.exe"
      DetailPrint "Microsoft MPI installed."
    ${Else}
      DetailPrint "Microsoft MPI setup ended with code $1."
      MessageBox MB_ICONEXCLAMATION|MB_OK "Microsoft MPI could not be installed (code $1).$\r$\nAeroBench will offer to install it when it starts (Welcome screen, Environment)." /SD IDOK
    ${EndIf}
  ${Else}
    DetailPrint "Microsoft MPI is already installed."
  ${EndIf}
!macroend

!macro customUnInstall
  ; Microsoft MPI is a shared system component (other software may use it): it is kept.
  ; Remove it from Windows Settings > Apps if no longer needed.
!macroend

; Replaces electron-builder's default check, which would kill every process started from the
; installation folder — now including running OpenFOAM simulations — without saying so.
; tools\running.py (run with the installation's own Python) tells the application apart from
; simulations; simulations are stopped gracefully (state written, resumable) only if the user
; agrees. Silent installs never stop simulations: they exit with code 3 instead.
!macro customCheckAppRunning
  ${If} ${FileExists} "$INSTDIR\resources\python\python.exe"
  ${AndIf} ${FileExists} "$INSTDIR\resources\tools\running.py"
    nsExec::ExecToLog '"$INSTDIR\resources\python\python.exe" -I "$INSTDIR\resources\tools\running.py" check "$INSTDIR"'
    Pop $R0
    ${If} $R0 == 20
      ${If} ${Cmd} `MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "AeroBench simulations are running.$\r$\n$\r$\nOK: stop them now. Each one writes its current state and can be resumed afterwards (Run page > Resume).$\r$\nCancel: quit this setup and let them finish." /SD IDCANCEL IDOK`
        DetailPrint "Closing AeroBench..."
        nsExec::ExecToLog '"$INSTDIR\resources\python\python.exe" -I "$INSTDIR\resources\tools\running.py" close-app "$INSTDIR"'
        Pop $R0
        DetailPrint "Stopping simulations (each writes its current state; this can take a few minutes)..."
        nsExec::ExecToLog '"$INSTDIR\resources\python\python.exe" -I "$INSTDIR\resources\tools\running.py" stop-sims "$INSTDIR"'
        Pop $R0
        ${If} $R0 != 0
          MessageBox MB_OK|MB_ICONSTOP "Some simulations did not stop. Stop them in AeroBench (Run page), then run this setup again." /SD IDOK
          SetErrorLevel 3
          Quit
        ${EndIf}
      ${Else}
        SetErrorLevel 3
        Quit
      ${EndIf}
    ${ElseIf} $R0 == 10
      ${If} ${Cmd} `MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "AeroBench is running. Click OK to close it and continue." /SD IDOK IDOK`
        nsExec::ExecToLog '"$INSTDIR\resources\python\python.exe" -I "$INSTDIR\resources\tools\running.py" close-app "$INSTDIR"'
        Pop $R0
        Sleep 500
      ${Else}
        Quit
      ${EndIf}
    ${ElseIf} $R0 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "The setup could not check whether AeroBench or a simulation is running.$\r$\nClose AeroBench and stop running simulations, then click OK." /SD IDOK
    ${EndIf}
  ${EndIf}
!macroend
