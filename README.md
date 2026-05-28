# YT-Cuts: Sermon to Shorts 🎬

Ferramenta automatizada para transformar sermões (ou vídeos longos) do YouTube em "Shorts" virais para YouTube, Instagram Reels e TikTok, com sistema de agendamento integrado.

## ✨ Funcionalidades

- **Extração Inteligente:** Utiliza Gemini Pro para analisar transcrições e encontrar os momentos mais impactantes.
- **Legendas Dinâmicas:** Gera legendas sincronizadas palavra por palavra (Word-Level Sync) com estilo personalizável.
- **Remoção de Silêncio:** Detecta e remove silêncios automaticamente para manter o vídeo dinâmico, com ajuste automático de sincronia das legendas.
- **Processamento de Vídeo:** Recorte automático para o formato vertical (9:16) usando FFmpeg com filtros de qualidade (Unsharp, CAS, ColorBalance).
- **IA Generativa:** 
  - Gemini Flash para polir gramática das legendas.
  - Geração automática de títulos, descrições e hashtags.
- **Automação de Redes Sociais:**
  - Login seguro no Instagram com suporte a **Autenticação em 2 Etapas (2FA)**.
  - Persistência de sessão (login único).
  - Sistema de fila e agendamento local para posts.

## 🚀 Pré-requisitos

Antes de começar, você precisará ter instalado:

- [Python 3.10+](https://www.python.org/)
- [FFmpeg](https://ffmpeg.org/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [Gemini CLI](https://github.com/google/gemini-cli) (configurado com sua API Key)

## 📦 Instalação

1. Clone o repositório:
```bash
git clone https://github.com/weinne/yt-cuts.git
cd yt-cuts
```

2. Crie um ambiente virtual e instale as dependências:
```bash
python -m venv venv
source venv/bin/activate  # No Windows: venv\Scripts\activate
pip install instagrapi
```

## 🛠️ Como Usar

### 1. Processar um Vídeo
Rode o script principal passando a URL do YouTube:
```bash
python sermon_to_shorts.py "https://www.youtube.com/watch?v=EXEMPLO"
```

O script irá:
1. Baixar a transcrição.
2. Pedir para a IA escolher os 3 melhores momentos.
3. Permitir que você edite legendas, gere o vídeo e a descrição.
4. Oferecer a opção de **Agendar no Instagram**.

## 🛠️ Solução de Problemas (Troubleshooting)

### Erro 429 ou Detecção de Bot no YouTube
Se você encontrar erros como "Too Many Requests" ou "Sign in to confirm you’re not a bot", siga estes passos:

1. **Use Cookies:**
   - Instale a extensão [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/ccmclabimipitempgejnadgljebadhia) no seu navegador desktop.
   - Acesse o YouTube, faça login e exporte os cookies para um arquivo chamado `cookies.txt`.
   - Coloque o arquivo `cookies.txt` na pasta raiz deste projeto.
   - O script detectará automaticamente o arquivo e o usará em todas as requisições ao YouTube.

2. **JavaScript Runtime:**
   - O `yt-dlp` funciona melhor com um runtime JavaScript. No Termux ou Linux, instale o Node.js:
     ```bash
     pkg install nodejs-lts  # No Termux
     # ou
     sudo apt install nodejs # No Ubuntu/Debian
     ```

### 2. Agendamento no Instagram
Ao escolher a opção de agendar, o post será salvo na pasta `insta_queue/`.

Para que o post seja publicado na hora certa, você deve deixar o **Worker** rodando:

```bash
# Opcional: Defina suas credenciais para evitar prompts manuais
export INSTA_USER='seu_usuario'
export INSTA_PASS='sua_senha'

python insta_worker.py
```

### 🔐 Autenticação em 2 Etapas (2FA)
Na primeira vez que você agendar ou postar, o script pedirá o código 2FA no terminal. Após isso, uma sessão será salva em `insta_session.json` e você não precisará repetir o processo por um longo período.

## 📁 Estrutura do Projeto

- `sermon_to_shorts.py`: Script principal de processamento e interface.
- `insta_worker.py`: Background worker para uploads agendados.
- `insta_queue/`: Pasta onde os posts agendados aguardam o horário.
- `outputs/`: Pasta com os vídeos finais, transcrições e miniaturas.

## ⚠️ Aviso Legal
Este projeto utiliza a biblioteca `instagrapi`, que interage com a API privada do Instagram. Use com moderação para evitar suspensões na conta. Recomenda-se o uso de contas de Criador de Conteúdo ou Business.

---
Desenvolvido com ❤️ para facilitar a propagação de mensagens que importam.
