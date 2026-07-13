#!/bin/bash
# install.sh
# Instala a skill document-swarm no Copilot CLI (Linux/macOS) criando um symlink:
#   ~/.copilot/skills/document-swarm  ->  <raiz deste repo>
#
# Uso:
#   ./scripts/install.sh
#   ./scripts/install.sh --with-presentation   # tenta instalar tb o toolchain do Modo Apresentação (PPTX)
set -e

WITH_PRESENTATION=0
for arg in "$@"; do
  case "$arg" in
    --with-presentation) WITH_PRESENTATION=1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SKILLS_DIR="$HOME/.copilot/skills"
LINK_PATH="$SKILLS_DIR/document-swarm"

if [ ! -f "$REPO_ROOT/SKILL.md" ]; then
  echo "SKILL.md não encontrado em $REPO_ROOT — rode este script de dentro do repo document-swarm." >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info.major != 3)'; then
  PY=python
else
  echo "Python 3 é obrigatório para os checks determinísticos do modo documento." >&2
  exit 1
fi

echo "🐝 Instalando skill document-swarm"
echo "   repo:  $REPO_ROOT"
echo "   link:  $LINK_PATH"
echo "   python: $($PY --version 2>&1)"

mkdir -p "$SKILLS_DIR"

# Remove link/pasta existente
if [ -L "$LINK_PATH" ] || [ -e "$LINK_PATH" ]; then
  rm -rf "$LINK_PATH"
fi

ln -s "$REPO_ROOT" "$LINK_PATH"

echo "   ✅ document-swarm instalado -> $(readlink -f "$LINK_PATH")"
echo ""
echo "Reinicie o Copilot CLI e confirme com /skills."
echo "Saída dos swarms (padrão): $REPO_ROOT/swarms"
echo "Para mudar a saída, defina DOCSWARM_ROOT ou indique o destino no pedido."

# ── Modo Apresentação (PPTX): toolchain opcional ───────────────────────────────
# A skill funciona em modo documento sem nada disto. O Modo Apresentação precisa de
# pptxgenjs (build) + LibreOffice/Poppler (render p/ revisão de design) + Pillow/markitdown.
echo ""
echo "— Modo Apresentação (PPTX): checando toolchain —"

have()  { command -v "$1" >/dev/null 2>&1; }
pymod() { have "$PY" && "$PY" -c "import $1" >/dev/null 2>&1; }

MISSING=""
check() {
  if eval "$2" >/dev/null 2>&1; then
    echo "   ✅ $1"
  else
    echo "   ⚠️  $1 (ausente)"
    MISSING="$MISSING $1"
  fi
}

check "node (build)"          "have node"
check "npm (build)"           "have npm"
check "pptxgenjs (npm -g)"    "npm ls -g pptxgenjs"
check "$PY (checks/QA)"       "have $PY"
check "Pillow (thumbnail)"    "pymod PIL"
check "markitdown (QA texto)" "pymod markitdown"
check "soffice (LibreOffice)" "have soffice"
check "pdftoppm (Poppler)"    "have pdftoppm"

if [ -z "$MISSING" ]; then
  echo "   ✅ toolchain de apresentação completo."
elif [ "$WITH_PRESENTATION" = "1" ]; then
  echo ""
  echo "   Instalando dependências de apresentação (--with-presentation)..."
  have npm && npm install -g pptxgenjs || true
  have "$PY" && "$PY" -m pip install --quiet Pillow "markitdown[pptx]" || true
  if have apt-get; then
    sudo apt-get update && sudo apt-get install -y libreoffice poppler-utils || true
  elif have dnf; then
    sudo dnf install -y libreoffice poppler-utils || true
  elif have brew; then
    brew install --cask libreoffice || true
    brew install poppler || true
  else
    echo "   ⚠️  Gerenciador de pacotes não detectado — instale LibreOffice e Poppler manualmente."
  fi
  echo "   ✅ Tentativa concluída. Reinicie o Copilot CLI/terminal se necessário."
else
  echo ""
  echo "   Para habilitar o Modo Apresentação, instale o que falta:"
  echo "     npm  install -g pptxgenjs"
  echo "     pip  install Pillow 'markitdown[pptx]'"
  echo "     Debian/Ubuntu:  sudo apt-get install libreoffice poppler-utils"
  echo "     Fedora:         sudo dnf install libreoffice poppler-utils"
  echo "     macOS:          brew install --cask libreoffice && brew install poppler"
  echo "   Ou rode:  ./scripts/install.sh --with-presentation"
fi
