#!/bin/bash

# Obtém o diretório onde o script está localizado
BASE_DIR=$(dirname "$(readlink -f "$0")")
cd "$BASE_DIR"

# Verifica se o ambiente virtual existe
if [ ! -d "venv" ]; then
    echo "❌ Erro: Ambiente virtual 'venv' não encontrado."
    echo "Rode 'setup.sh' ou crie o venv primeiro."
    exit 1
fi

# Ativa o ambiente virtual e roda o script passando todos os argumentos
source venv/bin/activate
python sermon_to_shorts.py "$@"
