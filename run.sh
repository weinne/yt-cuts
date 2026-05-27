#!/bin/bash

# Obtém o diretório onde o script está localizado
BASE_DIR=$(dirname "$(readlink -f "$0")")
cd "$BASE_DIR"

# Verifica se o ambiente virtual existe e o ativa
if [ -f "venv/bin/activate" ]; then
    echo "✅ Ativando ambiente virtual 'venv'..."
    source venv/bin/activate
elif [ -f "venv/Scripts/activate" ]; then
    echo "✅ Ativando ambiente virtual (Windows style) 'venv'..."
    source venv/Scripts/activate
else
    echo "⚠️  'venv' não encontrado ou incompleto. Usando Python do sistema..."
fi

# Roda o script passando todos os argumentos
python sermon_to_shorts.py "$@"
