# install.ps1
# Instala a skill document-swarm no Copilot CLI (Windows) criando um symlink:
#   ~/.copilot/skills/document-swarm  ->  <raiz deste repo>
#
# Uso:
#   pwsh scripts/install.ps1
#
# O symlink no Windows exige Developer Mode habilitado
# (Settings > Privacy & security > For developers) OU um terminal elevado.
# Se a criação de symlink falhar, o script cai para junction (mklink /J),
# que não exige privilégio.

[CmdletBinding()]
param()

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
