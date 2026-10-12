$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $env:JAVA_HOME -or -not $env:ANDROID_HOME) {
    throw 'Set JAVA_HOME to JDK 21 and ANDROID_HOME to Android SDK 36 first.'
}
Push-Location $projectRoot
try {
    & npm.cmd run sync:android
    if ($LASTEXITCODE -ne 0) { throw 'Android asset synchronization failed.' }
    $sdkPath = $env:ANDROID_HOME.Replace('\', '/')
    Set-Content -LiteralPath (Join-Path $projectRoot 'android/local.properties') -Value "sdk.dir=$sdkPath" -Encoding ascii
    & (Join-Path $projectRoot 'android/gradlew.bat') -p (Join-Path $projectRoot 'android') assembleDebug --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'Android build failed.' }
    Write-Output (Join-Path $projectRoot 'android/app/build/outputs/apk/debug/app-debug.apk')
} finally { Pop-Location }
