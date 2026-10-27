<#
.SYNOPSIS
    Build ps3texmod natively on Windows (no WSL, no MSYS2, no make, no sh).

.DESCRIPTION
    Uses the prebuilt PS3DK Windows release (GCC 16.1.0 for powerpc64-ps3-elf)
    and its bundled host tools (sprxlinker, make_self, fself, make_self_npdrm,
    sfo, pkg, package_finalize).  The PSL1GHT `ppu_rules` make flow needs a
    POSIX shell for its recipes, so this script runs the same five stages
    directly instead: compile, link, strip+sprxlinker, sign, package.

    The PS3DK location is taken from -PS3DK, then $env:PS3DK, then the
    repository-local toolchain\ps3dk\ps3-sdk-*-windows-x86_64 folder.

    Outputs (next to this script):
        ps3texmod.elf         raw PPU ELF
        ps3texmod.self        CEX-signed SELF
        ps3texmod.fake.self   fake-signed SELF (CFW / ps3load)
        ps3texmod.pkg         installable package
        ps3texmod.gnpdrm.pkg  package_finalize'd variant

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1

.EXAMPLE
    .\build.ps1 -PS3DK C:\ps3sdk -NoClean
#>
param(
    [string]$PS3DK = $env:PS3DK,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Fail([string]$Message) {
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Tool {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "$(Split-Path -Leaf $Exe) failed with exit code $LASTEXITCODE"
    }
}

# ---------------------------------------------------------------- toolchain
if (-not $PS3DK) {
    $candidates = @(
        (Join-Path (Split-Path -Parent $PSScriptRoot) "toolchain\ps3dk")
    ) | Where-Object { Test-Path $_ }
    foreach ($dir in $candidates) {
        $hit = Get-ChildItem $dir -Directory -Filter "ps3-sdk-*-windows-x86_64" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($hit) { $PS3DK = $hit.FullName; break }
    }
}
if (-not $PS3DK) {
    Fail "PS3DK is not set and no toolchain\ps3dk\ps3-sdk-*-windows-x86_64 was found. Download the Windows SDK zip from https://github.com/FirebirdTA01/PS3DK/releases and pass -PS3DK <dir>."
}
$PS3DK = (Resolve-Path $PS3DK).Path

$GCC   = Join-Path $PS3DK "ppu\bin\powerpc64-ps3-elf-gcc.exe"
$STRIP = Join-Path $PS3DK "ppu\bin\powerpc64-ps3-elf-strip.exe"
$HOSTBIN  = Join-Path $PS3DK "bin"
$SPRX  = Join-Path $HOSTBIN "sprxlinker.exe"
$SELF  = Join-Path $HOSTBIN "make_self.exe"
$FSELF = Join-Path $HOSTBIN "fself.exe"
$SELFN = Join-Path $HOSTBIN "make_self_npdrm.exe"
$SFO   = Join-Path $HOSTBIN "sfo.exe"
$PKGTOOL   = Join-Path $HOSTBIN "pkg.exe"
$FINAL = Join-Path $HOSTBIN "package_finalize.exe"

foreach ($tool in @($GCC, $STRIP, $SPRX, $SELF, $FSELF, $SELFN, $SFO, $PKGTOOL, $FINAL)) {
    if (-not (Test-Path $tool)) { Fail "missing tool: $tool" }
}

$env:PS3DK = $PS3DK
$env:PS3DEV = $PS3DK
$env:PSL1GHT = $PS3DK
$env:PATH = "$HOSTBIN;$PS3DK\ppu\bin;$env:PATH"

$versionOutput = & $GCC --version
$version = $versionOutput[0]
Write-Host "toolchain: $version"
Write-Host "PS3DK:     $PS3DK"

# ---------------------------------------------------------------- layout
$TARGET    = "ps3texmod"
$TITLE     = "ps3texmod - Prototype texture installer"
$APPID     = "PS3TEXMD1"
$CONTENTID = "UP0001-$APPID`_00-0000000000000000"
$BUILD     = Join-Path $PSScriptRoot "build"
$OBJDIR    = Join-Path $BUILD "obj"
$SELF_DIR  = Join-Path $BUILD "self"
$PKG_DIR   = Join-Path $BUILD "pkg"

if (-not $NoClean) {
    foreach ($path in @($BUILD, (Join-Path $PSScriptRoot "$TARGET.elf"),
                        (Join-Path $PSScriptRoot "$TARGET.self"),
                        (Join-Path $PSScriptRoot "$TARGET.fake.self"),
                        (Join-Path $PSScriptRoot "$TARGET.pkg"),
                        (Join-Path $PSScriptRoot "$TARGET.gnpdrm.pkg"))) {
        Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}
foreach ($dir in @($OBJDIR, $SELF_DIR, (Join-Path $PKG_DIR "USRDIR"))) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$INCLUDES = @(
    "-I$(Join-Path $PSScriptRoot 'include')",
    "-I$(Join-Path $PS3DK 'ppu\include')",
    "-I$(Join-Path $PS3DK 'ppu\include\simdmath')"
)
$CFLAGS = @(
    "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter",
    "-mcpu=cell", "-mhard-float", "-fmodulo-sched",
    "-ffunction-sections", "-fdata-sections", "-fcommon"
) + $INCLUDES

# ---------------------------------------------------------------- compile
$objects = @()
$sources = Get-ChildItem (Join-Path $PSScriptRoot "source") -Filter *.c | Sort-Object Name
if ($sources.Count -eq 0) { Fail "no sources found" }
foreach ($src in $sources) {
    $objFile = Join-Path $OBJDIR ($src.BaseName + ".o")
    Write-Host "  CC  $($src.Name)"
    Invoke-Tool -Exe $GCC -Arguments ($CFLAGS + @('-c', $src.FullName, '-o', $objFile))
    $objects += $objFile
}

# ---------------------------------------------------------------- link
$elf = Join-Path $PSScriptRoot "$TARGET.elf"
$link = $objects + @(
    "-mcpu=cell", "-mhard-float", "-fmodulo-sched",
    "-ffunction-sections", "-fdata-sections",
    "-Wl,-Map,$(Join-Path $BUILD 'ps3texmod.elf.map')",
    "-o", $elf,
    "-L$(Join-Path $PS3DK 'ppu\lib')",
    "-lrsx", "-lgcm_sys", "-lio", "-lsysutil", "-lrt", "-llv2", "-lm"
)
Write-Host "  LD  $TARGET.elf"
Invoke-Tool -Exe $GCC -Arguments $link

# ---------------------------------------------------------------- sign
$stripped = Join-Path $SELF_DIR "$TARGET.elf"
Write-Host "  STRIP"
Invoke-Tool -Exe $STRIP -Arguments @($elf, '-o', $stripped)
Write-Host "  SPRXLINK"
Invoke-Tool -Exe $SPRX -Arguments @($stripped)
Write-Host "  SELF"
Invoke-Tool -Exe $SELF -Arguments @($stripped, (Join-Path $PSScriptRoot "$TARGET.self"))
Invoke-Tool -Exe $FSELF -Arguments @($stripped, (Join-Path $PSScriptRoot "$TARGET.fake.self"))

# ---------------------------------------------------------------- package
Write-Host "  PKG"
Copy-Item (Join-Path $PSScriptRoot "pkg\ICON0.PNG") (Join-Path $PKG_DIR "ICON0.PNG") -Force
Invoke-Tool -Exe $SELFN -Arguments @($stripped, (Join-Path $PKG_DIR "USRDIR\EBOOT.BIN"), $CONTENTID)
Invoke-Tool -Exe $SFO -Arguments @('--title', $TITLE, '--appid', $APPID, '-f', (Join-Path $PSScriptRoot "pkg\sfo.xml"), (Join-Path $PKG_DIR "PARAM.SFO"))

$pkg = Join-Path $PSScriptRoot "$TARGET.pkg"
$gnp = Join-Path $PSScriptRoot "$TARGET.gnpdrm.pkg"
Invoke-Tool -Exe $PKGTOOL -Arguments @('--contentid', $CONTENTID, $PKG_DIR, $pkg)
Copy-Item $pkg $gnp -Force
Invoke-Tool -Exe $FINAL -Arguments @($gnp)

# ---------------------------------------------------------------- report
Write-Host ""
Write-Host "artifacts:"
foreach ($f in @($elf, (Join-Path $PSScriptRoot "$TARGET.self"),
                 (Join-Path $PSScriptRoot "$TARGET.fake.self"),
                 $pkg, $gnp)) {
    if (-not (Test-Path $f)) { Fail "expected output missing: $f" }
    $item = Get-Item $f
    $hash = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
    "{0,-26} {1,10}  {2}" -f $item.Name, $item.Length, $hash
}
Write-Host ""
Write-Host "content id: $CONTENTID"
Write-Host "done."
