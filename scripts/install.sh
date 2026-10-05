#!/bin/bash
# Installs managed links only; never removes a real user directory.
set -eu

WITHOUT_MONITOR=0
WITH_PDF=0
for arg in "$@"; do
  case "$arg" in
    --without-monitor) WITHOUT_MONITOR=1 ;;
    --with-pdf) WITH_PDF=1 ;;
    *) printf 'Argumento não suportado: %s\nUso: ./scripts/install.sh [--without-monitor] [--with-pdf]\n' "$arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SKILL_PATH="$HOME/.copilot/skills/document-swarm"
MONITOR_PATH="$HOME/.copilot/extensions/document-swarm-monitor"
MONITOR_SOURCE="$REPO_ROOT/.github/extensions/document-swarm-monitor"
PDF_PATH="$HOME/.copilot/extensions/document-swarm-pdf"
PDF_SOURCE="$REPO_ROOT/.github/extensions/document-swarm-pdf"

if [ ! -f "$REPO_ROOT/SKILL.md" ]; then
  echo "SKILL.md não encontrado em $REPO_ROOT." >&2
  exit 1
fi
if [ "$WITH_PDF" = 1 ] && [ ! -f "$PDF_SOURCE/extension.mjs" ]; then
  echo "Extensão PDF incompleta. Confira o clone antes de instalar com --with-pdf." >&2
  exit 1
fi
if [ "$WITHOUT_MONITOR" = 0 ]; then
  for file in extension.mjs ui/index.html; do
    if [ ! -f "$MONITOR_SOURCE/$file" ]; then
      echo "Monitor incompleto: $file. Use --without-monitor para instalar somente a skill." >&2
      exit 1
    fi
  done
fi
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info.major != 3)'; then
  PY=python
else
  echo "Python 3 é obrigatório para os checks determinísticos." >&2
  exit 1
fi

canonical() {
  PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$PY" -c 'import os,sys; print(os.path.normcase(os.path.realpath(sys.argv[1])))' "$1"
}
managed() {
  if [ -L "$1" ] || [ -e "$1" ]; then
    if [ ! -L "$1" ]; then
      echo "Destino não gerenciado: $1. Nenhum diretório real será removido." >&2
      return 1
    fi
    if [ "$(canonical "$1")" != "$(canonical "$2")" ]; then
      echo "O link $1 pertence a outro destino. Confirme a instalação existente." >&2
      return 1
    fi
  fi
}

managed "$SKILL_PATH" "$REPO_ROOT"
managed "$MONITOR_PATH" "$MONITOR_SOURCE"
if [ "$WITH_PDF" = 1 ]; then managed "$PDF_PATH" "$PDF_SOURCE"; fi
CREATED_SKILL=0
CREATED_MONITOR=0
CREATED_PDF=0
rollback() {
  status=$?
  if [ "$status" -ne 0 ]; then
    if [ "$CREATED_PDF" = 1 ] && managed "$PDF_PATH" "$PDF_SOURCE"; then
      rm "$PDF_PATH" || echo "Não foi possível reverter $PDF_PATH." >&2
    fi
    if [ "$CREATED_MONITOR" = 1 ] && managed "$MONITOR_PATH" "$MONITOR_SOURCE"; then
      rm "$MONITOR_PATH" || echo "Não foi possível reverter $MONITOR_PATH." >&2
    fi
    if [ "$CREATED_SKILL" = 1 ] && managed "$SKILL_PATH" "$REPO_ROOT"; then
      rm "$SKILL_PATH" || echo "Não foi possível reverter $SKILL_PATH." >&2
    fi
  fi
}
trap rollback EXIT

if [ ! -L "$SKILL_PATH" ]; then
  mkdir -p "$(dirname "$SKILL_PATH")"
  ln -s "$REPO_ROOT" "$SKILL_PATH"
  CREATED_SKILL=1
fi
if [ "$WITH_PDF" = 1 ] && [ ! -L "$PDF_PATH" ]; then
  mkdir -p "$(dirname "$PDF_PATH")"
  ln -s "$PDF_SOURCE" "$PDF_PATH"
  CREATED_PDF=1
fi
if [ "$WITHOUT_MONITOR" = 1 ]; then
  if [ -L "$MONITOR_PATH" ]; then rm "$MONITOR_PATH"; fi
elif [ ! -L "$MONITOR_PATH" ]; then
  mkdir -p "$(dirname "$MONITOR_PATH")"
  ln -s "$MONITOR_SOURCE" "$MONITOR_PATH"
  CREATED_MONITOR=1
fi
trap - EXIT

echo "Skill instalada: $SKILL_PATH"
if [ "$WITHOUT_MONITOR" = 1 ]; then
  echo "Monitor global não instalado. Use monitor: false no brief para desativá-lo também no projeto da extensão."
else
  echo "Monitor instalado: $MONITOR_PATH"
  echo "A extensão usa o SDK do Copilot; nenhuma dependência npm é instalada."
fi
if [ "$WITH_PDF" = 1 ]; then
  echo "Ferramenta PDF instalada: $PDF_PATH"
  echo "Dependências PDF são opcionais: instale requirements-pdf.txt em .venv-pdf."
fi
echo "Reinicie o Copilot CLI para redescobrir skill e extensão."
echo "Saída padrão: $REPO_ROOT/swarms"
