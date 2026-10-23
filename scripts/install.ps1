# Installs managed links only; never removes a real user directory.
[CmdletBinding()]
param([switch]$WithoutMonitor, [switch]$WithPdf)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SkillPath = Join-Path $env:USERPROFILE '.copilot\skills\document-swarm'
$MonitorPath = Join-Path $env:USERPROFILE '.copilot\extensions\document-swarm-monitor'
$MonitorSource = Join-Path $RepoRoot '.github\extensions\document-swarm-monitor'
$PdfPath = Join-Path $env:USERPROFILE '.copilot\extensions\document-swarm-pdf'
$PdfSource = Join-Path $RepoRoot '.github\extensions\document-swarm-pdf'

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'SKILL.md') -PathType Leaf)) {
  throw "SKILL.md não encontrado em $RepoRoot."
}
if ($WithPdf -and -not (Test-Path -LiteralPath (Join-Path $PdfSource 'extension.mjs') -PathType Leaf)) {
  throw 'Extensão PDF incompleta. Confira o clone antes de instalar com -WithPdf.'
}
if (-not $WithoutMonitor) {
  foreach ($file in @('extension.mjs', 'ui\index.html')) {
    if (-not (Test-Path -LiteralPath (Join-Path $MonitorSource $file) -PathType Leaf)) {
      throw "Monitor incompleto: $file não encontrado. Use -WithoutMonitor para instalar somente a skill."
    }
  }
}

$PythonExe = $null
foreach ($candidate in @('python3', 'python')) {
  $command = Get-Command $candidate -ErrorAction SilentlyContinue
  if (-not $command) { continue }
  & $command.Source -c 'import sys; raise SystemExit(sys.version_info.major != 3)' 2>$null
  if ($LASTEXITCODE -eq 0) { $PythonExe = $command.Source; break }
}
if (-not $PythonExe) { throw 'Python 3 é obrigatório para os checks determinísticos.' }

function Get-ManagedLink([string]$Link, [string]$Target) {
  try { $item = Get-Item -LiteralPath $Link -Force -ErrorAction Stop }
  catch [System.Management.Automation.ItemNotFoundException] { return $null }
  if ($item.LinkType -notin @('SymbolicLink', 'Junction')) {
    throw "Destino não gerenciado: $Link. Nenhum diretório real será removido."
  }
  $actual = @($item.Target)[0]
  if (-not [System.IO.Path]::IsPathRooted($actual)) { $actual = Join-Path $item.Parent.FullName $actual }
  $actual = [System.IO.Path]::GetFullPath($actual).TrimEnd('\', '/')
  $expected = [System.IO.Path]::GetFullPath($Target).TrimEnd('\', '/')
  if (-not [StringComparer]::OrdinalIgnoreCase.Equals($actual, $expected)) {
    throw "O link $Link pertence a outro destino. Confirme a instalação existente antes de alterá-lo."
  }
  return $item
}

function New-ManagedLink([string]$Link, [string]$Target) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Link) | Out-Null
  try {
    New-Item -ItemType SymbolicLink -Path $Link -Target $Target -ErrorAction Stop | Out-Null
  } catch {
    $symbolicError = $_.Exception.Message
    try {
      New-Item -ItemType Junction -Path $Link -Target $Target -ErrorAction Stop | Out-Null
    } catch {
      throw "Falha ao criar link $Link. Symlink: $symbolicError. Junction: $($_.Exception.Message)"
    }
  }
}

# Preflight all requested destinations before changing any of them.
$SkillLink = Get-ManagedLink $SkillPath $RepoRoot
$MonitorLink = Get-ManagedLink $MonitorPath $MonitorSource
$PdfLink = if ($WithPdf) { Get-ManagedLink $PdfPath $PdfSource } else { $null }
$created = [System.Collections.Generic.List[object]]::new()
try {
  if (-not $SkillLink) {
    New-ManagedLink $SkillPath $RepoRoot
    $created.Add(@{ Path = $SkillPath; Target = $RepoRoot })
  }
  if ($WithPdf -and -not $PdfLink) {
    New-ManagedLink $PdfPath $PdfSource
    $created.Add(@{ Path = $PdfPath; Target = $PdfSource })
  }
  if ($WithoutMonitor) {
    if ($MonitorLink) { $MonitorLink.Delete() }
  } elseif (-not $MonitorLink) {
    New-ManagedLink $MonitorPath $MonitorSource
    $created.Add(@{ Path = $MonitorPath; Target = $MonitorSource })
  }
} catch {
  $installationError = $_
  foreach ($link in $created) {
    try {
      $item = Get-ManagedLink $link.Path $link.Target
      if ($item) { $item.Delete() }
    } catch {
      Write-Warning "Não foi possível reverter o link $($link.Path): $($_.Exception.Message)"
    }
  }
  throw $installationError
}

Write-Host "Skill instalada: $SkillPath"
if ($WithoutMonitor) {
  Write-Host 'Monitor global não instalado. Use monitor: false no brief para desativá-lo também em um projeto que contenha a extensão.'
} else {
  Write-Host "Monitor instalado: $MonitorPath"
  Write-Host 'A extensão usa o SDK fornecido pelo Copilot; nenhuma dependência npm é instalada.'
}
if ($WithPdf) {
  Write-Host "Ferramenta PDF instalada: $PdfPath"
  Write-Host 'Dependências PDF são opcionais: instale requirements-pdf.txt em .venv-pdf para renderizar e inspecionar.'
}
Write-Host 'Reinicie o Copilot CLI para redescobrir skill e extensão.'
Write-Host ('Saída padrão: ' + (Join-Path $RepoRoot 'swarms'))
