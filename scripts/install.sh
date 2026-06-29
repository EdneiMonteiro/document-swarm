#!/bin/bash
# install.sh
# Instala a skill document-swarm no Copilot CLI (Linux/macOS) criando um symlink:
#   ~/.copilot/skills/document-swarm  ->  <raiz deste repo>
#
# Uso:
#   ./scripts/install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SKILLS_DIR="$HOME/.copilot/skills"
LINK_PATH="$SKILLS_DIR/document-swarm"

if [ ! -f "$REPO_ROOT/SKILL.md" ]; then
  echo "SKILL.md não encontrado em $REPO_ROOT — rode este script de dentro do repo document-swarm." >&2
  exit 1
fi

echo "🐝 Instalando skill document-swarm"
echo "   repo:  $REPO_ROOT"
echo "   link:  $LINK_PATH"

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
