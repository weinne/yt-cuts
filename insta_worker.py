import os
import json
import time
import datetime
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

def get_insta_client():
    cl = Client()
    session_file = "insta_session.json"
    
    if os.path.exists(session_file):
        cl.load_settings(session_file)
    else:
        print("Erro: Sessão do Instagram não encontrada. Faça o login primeiro usando o sermon_to_shorts.py")
        return None
    
    username = os.getenv("INSTA_USER")
    password = os.getenv("INSTA_PASS")

    if not username or not password:
        # Tenta pegar dos settings carregados se possível, mas instagrapi geralmente precisa do user/pass para o login() validar a sessão
        print("Erro: INSTA_USER e INSTA_PASS não definidos no ambiente.")
        return None

    try:
        cl.login(username, password)
    except Exception as e:
        print(f"Erro ao validar sessão: {e}")
        return None

    return cl

def process_queue():
    queue_dir = Path("insta_queue")
    if not queue_dir.exists():
        return

    cl = None
    
    for post_file in sorted(queue_dir.glob("*.json")):
        with open(post_file, "r", encoding="utf-8") as f:
            post_data = json.load(f)
        
        scheduled_time = datetime.datetime.strptime(post_data["scheduled_for"], "%Y-%m-%d %H:%M")
        
        if datetime.datetime.now() >= scheduled_time:
            print(f"--- Processando post: {post_file.name} ---")
            
            if cl is None:
                cl = get_insta_client()
                if cl is None:
                    print("Abortando processamento por falha no login.")
                    return

            try:
                print(f"Fazendo upload do Reel: {post_data['video']}")
                media = cl.clip_upload(
                    post_data["video"],
                    caption=post_data["caption"],
                    thumbnail=post_data["thumbnail"]
                )
                print(f"✅ Upload concluído! Media ID: {media.pk}")
                post_file.unlink() # Remove da fila
            except Exception as e:
                print(f"❌ Erro ao fazer upload: {e}")
                # Mantém na fila para tentar novamente depois

def main():
    print("--- Instagram Worker Iniciado ---")
    print("Verificando a pasta 'insta_queue' a cada 60 segundos...")
    
    while True:
        try:
            process_queue()
        except Exception as e:
            print(f"Erro no loop do worker: {e}")
        
        time.sleep(60)

if __name__ == "__main__":
    main()
