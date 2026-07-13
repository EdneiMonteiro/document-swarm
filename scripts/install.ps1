# install.ps1
# Instala a skill document-swarm no Copilot CLI (Windows) criando um symlink:
#   ~/.copilot/skills/document-swarm  ->  <raiz deste repo>
#
# Uso:
#   pwsh scripts/install.ps1
#   pwsh scripts/install.ps1 -WithPresentation   # tenta instalar tb o toolchain do Modo Apresentação (PPTX)
#
# O symlink no Windows exige Developer Mode habilitado
# (Settings > Privacy & security > For developers) OU um terminal elevado.
# Se a criação de symlink falhar, o script cai para junction (mklink /J),
# que não exige privilégio.

[CmdletBinding()]
param(
  # Quando presente, tenta instalar o toolchain do Modo Apresentação (PPTX):
  # pptxgenjs (npm), Pillow + markitdown (pip) e LibreOffice + Poppler (winget).
  [switch]$WithPresentation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$SkillsDir = Join-Path $env:USERPROFILE '.copilot\skills'
$LinkPath  = Join-Path $SkillsDir 'document-swarm'

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'SKILL.md'))) {
  throw "SKILL.md não encontrado em $RepoRoot — rode este script de dentro do repo document-swarm."
}

$PythonExe = $null
foreach ($candidate in @('python3', 'python')) {
  $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
  if (-not $cmd) { continue }
  & $cmd.Source -c 'import sys; raise SystemExit(sys.version_info.major != 3)' 2>$null
  if ($LASTEXITCODE -eq 0) {
    $PythonExe = $cmd.Source
    break
  }
}
if (-not $PythonExe) {
  throw 'Python 3 é obrigatório para os checks determinísticos do modo documento.'
}

Write-Host '🐝 Instalando skill document-swarm'
Write-Host "   repo:  $RepoRoot"
Write-Host "   link:  $LinkPath"
Write-Host ('   python: ' + (& $PythonExe --version 2>&1))

New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null

# Remove link/pasta existente (sem seguir o link).
if (Test-Path -LiteralPath $LinkPath) {
  $item = Get-Item -LiteralPath $LinkPath -Force
  if ($item.LinkType) { $item.Delete() } else { Remove-Item -LiteralPath $LinkPath -Recurse -Force }
}

$kind = $null
try {
  New-Item -ItemType SymbolicLink -Path $LinkPath -Target $RepoRoot -ErrorAction Stop | Out-Null
  $kind = 'symlink'
} catch {
  cmd /c mklink /J "`"$LinkPath`"" "`"$RepoRoot`"" | Out-Null
  if ($LASTEXITCODE -eq 0) {
    $kind = 'junction'
  } else {
    throw "Falha ao criar o link. Habilite o Developer Mode ou rode em terminal elevado. Erro: $($_.Exception.Message)"
  }
}

$target = (Get-Item -LiteralPath $LinkPath -Force).Target
Write-Host "   ✅ document-swarm instalado ($kind) -> $target"
Write-Host ''
Write-Host 'Reinicie o Copilot CLI e confirme com /skills.'
Write-Host ('Saída dos swarms (padrão): ' + (Join-Path $RepoRoot 'swarms'))
Write-Host 'Para mudar a saída, defina $env:DOCSWARM_ROOT ou indique o destino no pedido.'

# ── Modo Apresentação (PPTX): toolchain opcional ───────────────────────────────
# A skill funciona em modo documento sem nada disto. O Modo Apresentação precisa de
# pptxgenjs (build) + LibreOffice/Poppler (render p/ revisão de design) + Pillow/markitdown.
try {
  $PSNativeCommandUseErrorActionPreference = $false
  Write-Host ''
  Write-Host '— Modo Apresentação (PPTX): checando toolchain —'

  function Test-Cmd($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }
  function Test-PyMod($m) { & $PythonExe -c "import $m" 2>$null; return ($LASTEXITCODE -eq 0) }
  function Test-NpmGlobal($p) { if (-not (Test-Cmd npm)) { return $false }; try { return ((& npm ls -g $p 2>$null | Out-String) -match [regex]::Escape($p)) } catch { return $false } }

  $sofficeOk  = (Test-Cmd soffice)  -or (Test-Path 'C:\Program Files\LibreOffice\program\soffice.exe') -or (Test-Path 'C:\Program Files (x86)\LibreOffice\program\soffice.exe')
  $wingetPkgs = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
  $popplerOk  = (Test-Cmd pdftoppm) -or (Test-Path (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\pdftoppm.exe')) -or ([bool](Get-ChildItem -Path $wingetPkgs -Filter 'pdftoppm.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1))

  $checks = [ordered]@{
    'node (build)'           = (Test-Cmd node)
    'npm (build)'            = (Test-Cmd npm)
    'pptxgenjs (npm -g)'     = (Test-NpmGlobal 'pptxgenjs')
    'Python 3 (checks/QA)'  = $true
    'Pillow (thumbnail)'     = (Test-PyMod 'PIL')
    'markitdown (QA texto)'  = (Test-PyMod 'markitdown')
    'soffice (LibreOffice)'  = $sofficeOk
    'pdftoppm (Poppler)'     = $popplerOk
  }

  $missing = @()
  foreach ($k in $checks.Keys) {
    if ($checks[$k]) { Write-Host "   ✅ $k" } else { Write-Host "   ⚠️  $k (ausente)"; $missing += $k }
  }

  if ($missing.Count -eq 0) {
    Write-Host '   ✅ toolchain de apresentação completo.'
  } elseif ($WithPresentation) {
    Write-Host ''
    Write-Host '   Instalando dependências de apresentação (-WithPresentation)...'
    if (Test-Cmd npm)    { try { & npm install -g pptxgenjs 2>&1 | Out-Null } catch {} }
    try { & $PythonExe -m pip install --quiet Pillow "markitdown[pptx]" 2>&1 | Out-Null } catch {}
    if (Test-Cmd winget) {
      try { & winget install --id TheDocumentFoundation.LibreOffice -e --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null } catch {}
      try { & winget install --id oschwartz10612.Poppler -e --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null } catch {}
    } else {
      Write-Host '   ⚠️  winget ausente — instale LibreOffice e Poppler manualmente.'
    }
    Write-Host '   ✅ Tentativa concluída. REINICIE o Copilot CLI/terminal para o PATH pegar soffice/pdftoppm.'
  } else {
    Write-Host ''
    Write-Host '   Para habilitar o Modo Apresentação, instale o que falta:'
    Write-Host '     npm  install -g pptxgenjs'
    Write-Host '     pip  install Pillow "markitdown[pptx]"'
    Write-Host '     winget install TheDocumentFoundation.LibreOffice'
    Write-Host '     winget install oschwartz10612.Poppler'
    Write-Host '   Ou rode:  pwsh scripts\install.ps1 -WithPresentation'
    Write-Host '   Depois de instalar, REINICIE o Copilot CLI/terminal (PATH).'
  }
} catch {
  Write-Host "   ⚠️  Checagem do toolchain de apresentação falhou: $($_.Exception.Message)"
}
