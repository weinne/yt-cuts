#!/bin/bash

# Obtém o diretório onde o script está localizado
BASE_DIR=$(dirname "$(readlink -f "$0")")
cd "$BASE_DIR"

if [ ! -d "venv" ]; then
    echo "❌ Erro: Ambiente virtual 'venv' não encontrado."
    exit 1
fi

source venv/bin/activate
echo "🚀 Iniciando Instagram Worker..."
python insta_worker.py
