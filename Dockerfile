FROM python:3.10-slim

# Instalar dependências do sistema (FFmpeg, yt-dlp, curl para gum)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instalar gum (TUI tool)
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://repo.charm.sh/apt/gpg.key | gpg --dearmor -o /etc/apt/keyrings/charm.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" | tee /etc/apt/list.d/charm.list \
    && apt-get update && apt-get install -y gum

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
