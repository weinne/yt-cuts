FROM python:3.10-slim

# Instalar dependências do sistema (FFmpeg, yt-dlp, curl para gum, unzip para fontes)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    unzip \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Instalar fonte Crimson Pro
RUN mkdir -p /app/fonts && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/crimsonpro/CrimsonPro%5Bwght%5D.ttf" -o /app/fonts/CrimsonPro-Bold.ttf

# Configurar diretório de trabalho
WORKDIR /app

# Instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir yt-dlp instagrapi

# Copiar código
COPY . .

# Dar permissão de execução aos scripts
RUN chmod +x run.sh run_worker.sh

# Variáveis de ambiente padrão
ENV INSTA_USER=""
ENV INSTA_PASS=""

# O script precisa de terminal interativo, então rodamos via entrypoint
ENTRYPOINT ["python3", "sermon_to_shorts.py"]
