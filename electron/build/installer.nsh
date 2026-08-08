; Custom NSIS hooks for the Mr. AI Marketer installer.
;
; electron-builder resolves nsis.include relative to directories.buildResources, which
; defaults to the `build` folder next to package.json — hence electron/build/, not the
; repo-root build/ that holds the icons.
;
; Why this file exists: the app runs its Python backend as a separate process
; (mr-ai-marketer-backend.exe, spawned by spawnPackagedBackend). Electron kills it on
; before-quit and window-all-closed, but two cases leave it alive anyway — the app crashing
; rather than quitting, and PyInstaller's bootloader spawning a child that outlives the
; signal sent to its parent. An orphaned backend holds open file handles inside the install
; directory, and NSIS then fails to overwrite them: an upgrade silently installs a half-old
; app, and an uninstall leaves the folder behind. Killing it first is the fix.
;
; taskkill's exit codes are deliberately ignored (`nsExec::Exec` result discarded): "no such
; process" is the normal, expected case, and must not fail the install.

!macro customInit
  DetailPrint "Closing any running Mr. AI Marketer backend..."
  nsExec::Exec 'taskkill /F /IM "mr-ai-marketer-backend.exe" /T'
  Pop $0
!macroend

!macro customUnInit
  DetailPrint "Closing any running Mr. AI Marketer backend..."
  nsExec::Exec 'taskkill /F /IM "mr-ai-marketer-backend.exe" /T'
  Pop $0
!macroend

; Uninstall deliberately leaves user data in place — deleteAppDataOnUninstall is false in
; package.json. That folder holds the SQLite database and every document, image and post the
; app has generated, which is the user's work, not the app's. Reinstalling picks it all back
; up. Say so plainly, with the path, so nobody is left wondering where it went or has to
; guess where to look to remove it by hand.
!macro customUnInstall
  ${ifNot} ${isUpdated}
    MessageBox MB_OK|MB_ICONINFORMATION \
      "Mr. AI Marketer has been removed.$\r$\n$\r$\n\
      Your data has been kept: the database, and everything the app generated for you, are \
      still in$\r$\n$\r$\n$APPDATA\mr-ai-marketer$\r$\n$\r$\n\
      Reinstalling picks up where you left off. Delete that folder yourself if you want it \
      gone."
  ${endIf}
!macroend
