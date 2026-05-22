import subprocess
import os
import sys
import json
import re
import datetime
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

def run_command(command, shell=False, check=True):
    print(f"Executing: {' '.join(command) if isinstance(command, list) else command}")
    result = subprocess.run(command, shell=shell, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"Error: {result.stderr}")
    return result

def clean_json_response(response_text):
    """Remove markdown formatting and extract JSON string."""
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)
    return response_text.strip()

def clean_vtt(vtt_path):
    with open(vtt_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    clean_lines = []
    for line in lines:
        if '-->' in line or 'WEBVTT' in line or 'Kind:' in line or 'Language:' in line:
            continue
        line = re.sub(r'<[^>]+>', '', line).strip()
        if line:
            clean_lines.append(line)
    
    unique_lines = []
    for line in clean_lines:
        if not unique_lines:
            unique_lines.append(line)
        elif line.startswith(unique_lines[-1]):
            unique_lines[-1] = line
        elif not unique_lines[-1].startswith(line):
            unique_lines.append(line)
            
    return " ".join(unique_lines)

def to_ms(t):
    parts = re.split('[:.]', t)
    if len(parts) == 3: # MM:SS.mmm
        return (int(parts[0])*60 + int(parts[1]))*1000 + int(parts[2])
    elif len(parts) == 4: # HH:MM:SS.mmm
        return (int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]))*1000 + int(parts[3])
    return 0

def norm_ts(ts):
    if ts.count(':') == 1: ts = "00:" + ts
    if '.' not in ts: ts = ts + ".000"
    return ts

def format_ts(ms):
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"

def parse_youtube_vtt_to_words(vtt_content, start_ms, end_ms):
    """Extrai palavras e timestamps, usando sobreposição de sequências para deduplicar rolling captions."""
    master_words = []
    lines = vtt_content.splitlines()
    
    # 1. Parse de todos os cues primeiro
    cues = []
    current_ts = None
    current_text_lines = []
    for line in lines:
        if '-->' in line:
            if current_ts:
                cues.append({'ts': current_ts, 'text': " ".join(current_text_lines)})
            current_ts = line
            current_text_lines = []
        elif line.strip() and not line.startswith("WEBVTT"):
            current_text_lines.append(line.strip())
    if current_ts:
        cues.append({'ts': current_ts, 'text': " ".join(current_text_lines)})

    # 2. Processamento incremental para montagem da master list
    for cue in cues:
        ts_match = re.findall(r'(\d+:\d+:\d+\.\d+|\d+:\d+\.\d+)', cue['ts'])
        if not ts_match: continue
        c_start = to_ms(norm_ts(ts_match[0]))
        c_end = to_ms(norm_ts(ts_match[1]))
        
        # Extrai palavras deste cue com seus timestamps
        cue_words = []
        parts = re.split(r'(<[^>]+>)', cue['text'])
        last_ts = c_start
        for p in parts:
            if p.startswith('<') and ':' in p:
                try:
                    last_ts = to_ms(norm_ts(p[1:-1]))
                except: pass
            else:
                clean_p = re.sub(r'<[^>]+>', '', p).strip()
                if clean_p:
                    for w in clean_p.split():
                        cue_words.append({'word': w, 'start': last_ts, 'end': c_end})
        
        if not cue_words: continue
        
        if not master_words:
            master_words.extend(cue_words)
            continue
            
        # Busca a maior sobreposição de sequência de palavras no final da master list
        max_overlap = 0
        # Olhamos até as últimas 50 palavras para encontrar a sobreposição
        lookback = min(len(master_words), 50)
        for i in range(1, min(lookback, len(cue_words)) + 1):
            master_suffix = [mw['word'].lower() for mw in master_words[-i:]]
            cue_prefix = [cw['word'].lower() for cw in cue_words[:i]]
            if master_suffix == cue_prefix:
                max_overlap = i
        
        # Adiciona apenas as palavras que vêm após a sobreposição
        master_words.extend(cue_words[max_overlap:])

    if not master_words: return []

    # 3. Ajuste de tempos e filtragem
    for i in range(len(master_words) - 1):
        if master_words[i+1]['start'] > master_words[i]['start']:
            master_words[i]['end'] = master_words[i+1]['start']
        else:
            master_words[i]['end'] = master_words[i]['start'] + 200

    segment_words = [w for w in master_words if w['start'] >= start_ms and w['start'] <= end_ms]
    for w in segment_words:
        w['start'] -= start_ms
        w['end'] -= start_ms
        
    return segment_words

def generate_vtt_from_words(words, words_per_cue=3):
    vtt = ["WEBVTT", ""]
    for i in range(0, len(words), words_per_cue):
        chunk = words[i:i+words_per_cue]
        if not chunk: continue
        
        start = format_ts(chunk[0]['start'])
        end = format_ts(chunk[-1]['end'])
        text = " ".join([w['word'] for w in chunk])
        
        vtt.append(f"{start} --> {end}")
        vtt.append(text)
        vtt.append("")
        
    return "\n".join(vtt)

def get_duration(video_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    result = run_command(cmd, check=False)
    try:
        return float(result.stdout.strip())
    except:
        return 0

def extract_text_from_vtt(vtt_path):
    if not os.path.exists(vtt_path): return ""
    with open(vtt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = content.splitlines()
    text_lines = []
    for line in lines:
        if '-->' in line or 'WEBVTT' in line or not line.strip():
            continue
        text_lines.append(line.strip())
    return " ".join(text_lines)

def generate_thumbnail(video_path, title, output_path):
    duration = get_duration(video_path)
    middle = duration / 2
    font_path = "/home/weinne/.local/share/fonts/c/CrimsonPro_VariableFont_wght.ttf"
    
    # Simple line wrapping for drawtext
    words = title.split()
    lines = []
    curr = []
    for w in words:
        if len(" ".join(curr + [w])) > 15:
            lines.append(" ".join(curr))
            curr = [w]
        else:
            curr.append(w)
    lines.append(" ".join(curr))
    wrapped_title = "\n".join(lines).replace("'", "'\\''").replace(":", "\\:")

    vf = (
        f"gblur=sigma=20,"
        f"drawbox=t=fill:color=0x002200@0.7,"
        f"drawbox=x=60:y=60:w=iw-120:h=ih-120:color=white@0.3:t=5,"
        f"drawtext=fontfile='{font_path}':text='{wrapped_title}':fontcolor=white:fontsize=80:"
        f"line_spacing=20:x=(w-text_w)/2:y=(h-text_h)/2"
    )

    print(f"--- Generating thumbnail: {output_path} ---")
    run_command([
        "ffmpeg", "-y", "-ss", str(middle), "-i", str(video_path),
        "-frames:v", "1", "-vf", vf, str(output_path)
    ])

def generate_description(title, polished_text, video_url):
    print("--- Generating video description with Gemini Flash ---")
    prompt = (
        f"Gere uma descrição atraente para um Short do YouTube/Instagram/TikTok.\n"
        f"Título do momento: {title}\n"
        f"Conteúdo: {polished_text}\n"
        f"Vídeo original: {video_url}\n"
        "A descrição deve ser curta, incluir emojis e hashtags relevantes. Responda APENAS com a descrição."
    )
    result = run_command(["gemini", "-m", "gemini-3.1-flash-lite", "-p", prompt])
    return clean_json_response(result.stdout)

def get_insta_client():
    cl = Client()
    session_file = "insta_session.json"
    
    if os.path.exists(session_file):
        print("--- Carregando sessão do Instagram ---")
        cl.load_settings(session_file)
    
    username = os.getenv("INSTA_USER") or input("Instagram Username: ")
    password = os.getenv("INSTA_PASS") or input("Instagram Password: ")

    try:
        cl.login(username, password)
    except TwoFactorRequired:
        print("--- Autenticação em 2 etapas detectada ---")
        verification_code = input("Digite o código 2FA: ")
        cl.login(username, password, verification_code=verification_code)
    except Exception as e:
        print(f"Erro ao logar no Instagram: {e}")
        if os.path.exists(session_file):
            os.remove(session_file)
            return get_insta_client()
        return None

    cl.dump_settings(session_file)
    return cl

def schedule_instagram_post(video_path, caption, thumbnail_path, schedule_time_str):
    queue_dir = Path("insta_queue")
    queue_dir.mkdir(exist_ok=True)
    
    post_id = f"post_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    post_data = {
        "video": str(video_path.absolute()),
        "caption": caption,
        "thumbnail": str(thumbnail_path.absolute()) if thumbnail_path else None,
        "scheduled_for": schedule_time_str
    }
    
    with open(queue_dir / f"{post_id}.json", "w", encoding="utf-8") as f:
        json.dump(post_data, f, indent=4)
    
    print(f"\n✅ Post agendado para {schedule_time_str}!")
    print(f"Dados salvos em: {queue_dir / f'{post_id}.json'}")

def get_video_id(url):
    result = run_command(["yt-dlp", "--get-id", url])
    return result.stdout.strip()

def main():
    if len(sys.argv) < 2:
        print("Usage: python sermon_to_shorts.py [YOUTUBE_URL]")
        return

    video_url = sys.argv[1]
    video_id = get_video_id(video_url)
    if not video_id:
        print("Error: Could not extract video ID.")
        return

    # Project setup
    project_dir = Path(f"outputs/{video_id}")
    project_dir.mkdir(parents=True, exist_ok=True)
    
    vtt_file = project_dir / "transcript.vtt"
    clean_txt = project_dir / "clean_transcript.txt"
    analysis_json = project_dir / "analysis.json"

    # 1. Download subtitles (Cache check)
    if not vtt_file.exists():
        print("--- Fetching subtitles ---")
        run_command([
            "yt-dlp", "--write-auto-subs", "--write-subs", 
            "--sub-langs", "pt.*,en.*", "--sub-format", "vtt", 
            "--skip-download", "-o", str(project_dir / "transcript"), video_url
        ])
        
        # Robustly find the transcript file
        found_vtt = None
        for suffix in [".pt.vtt", ".pt-orig.vtt", ".en.vtt", ".vtt"]:
            potential_file = project_dir / f"transcript{suffix}"
            if potential_file.exists():
                found_vtt = potential_file
                break
        
        if found_vtt:
            os.rename(found_vtt, vtt_file)
        else:
            print("Error: Could not download subtitles.")
            return
    else:
        print(f"--- Subtitles found in cache: {vtt_file} ---")

    if not clean_txt.exists():
        with open(vtt_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # Simplistic clean for analysis
        text = re.sub(r'<[^>]+>', '', content)
        text = re.sub(r'\d+:\d+:\d+\.\d+ --> \d+:\d+:\d+\.\d+.*', '', text)
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("WEBVTT")]
        # Deduplicate lines for cleaner analysis
        unique_lines = []
        for l in lines:
            if not unique_lines or l != unique_lines[-1]:
                unique_lines.append(l)
        with open(clean_txt, "w", encoding="utf-8") as f:
            f.write(" ".join(unique_lines))

    # 2. Analyze with Gemini
    if not analysis_json.exists():
        print("--- Analyzing with Gemini (Pro Model) ---")
        prompt = (
            "Analise a transcrição deste sermão e encontre os 3 melhores momentos para criar vídeos virais.\n"
            "Retorne EXCLUSIVAMENTE um JSON: "
            '[{"start": "MM:SS", "end": "MM:SS", "title": "...", "reason": "..."}]'
        )
        gemini_cmd = ["gemini", "-m", "gemini-3.1-pro-preview", "-p", f"@{clean_txt} {prompt}", "-o", "json"]
        result = run_command(gemini_cmd)
        try:
            gemini_data = json.loads(result.stdout)
            raw_response = gemini_data['response'] if 'response' in gemini_data else result.stdout
            moments_json = clean_json_response(raw_response)
            json.loads(moments_json)
            with open(analysis_json, "w", encoding="utf-8") as f:
                f.write(moments_json)
        except Exception as e:
            print(f"Failed to parse Gemini response: {e}")
            return
    
    with open(analysis_json, "r", encoding="utf-8") as f:
        moments = json.load(f)

    # 3. User Selection
    print("\n--- Escolha os momentos para processar ---")
    for i, m in enumerate(moments):
        print(f"{i+1}. [{m['start']} - {m['end']}] {m['title']}")
    
    choice_input = input("Escolha (1-3, 'todos', 'q'): ")
    if choice_input.lower() == 'q': return
    selected_indices = list(range(len(moments))) if choice_input.lower() == 'todos' else [int(x.strip()) - 1 for x in choice_input.split(',')]

    for idx in selected_indices:
        selected = moments[idx]
        safe_title = re.sub(r'[^a-zA-Z0-9]', '_', selected['title'])[:20]
        short_id = f"momo_{idx+1}_{selected['start'].replace(':', '')}_{safe_title}"
        short_dir = project_dir / "shorts" / short_id
        short_dir.mkdir(parents=True, exist_ok=True)

        segment_file = short_dir / "segment.mp4"
        edited_vtt = short_dir / "edited.vtt"
        final_video = short_dir / "final.mp4"

        # 4. Download segment
        if not segment_file.exists():
            run_command(["yt-dlp", "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/mp4", 
                         "--download-sections", f"*{selected['start']}-{selected['end']}", 
                         "-o", str(segment_file), video_url])

        # 5. Subtitles with Word-Level Sync
        if not edited_vtt.exists():
            print("--- Processing Subtitles with Word-Level Sync ---")
            with open(vtt_file, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            
            s_ms = to_ms(norm_ts(selected['start']))
            e_ms = to_ms(norm_ts(selected['end']))
            
            words = parse_youtube_vtt_to_words(vtt_content, s_ms, e_ms)
            
            if not words:
                print("Warning: No words found for this segment.")
                continue

            # Optional: Polish text with Gemini (preserving words)
            raw_text = " ".join([w['word'] for w in words])
            polish_prompt = (
                "Você é um editor de legendas. Sua tarefa é adicionar PONTUAÇÃO e corrigir GRAMÁTICA do texto.\n"
                "IMPORTANTE: Você NÃO pode remover nem repetir palavras. Mantenha a ordem exata.\n"
                "As repetições brutas já foram removidas heuristicamente.\n"
                f"TEXTO: {raw_text}"
            )
            print("--- Polishing text with Gemini ---")
            polish_result = run_command(["gemini", "-m", "gemini-3.1-flash-lite", "-p", polish_prompt])
            try:
                res_content = polish_result.stdout
                try:
                    polish_data = json.loads(res_content)
                    polished_text = polish_data['response'] if 'response' in polish_data else res_content
                except:
                    polished_text = res_content
            except:
                polished_text = raw_text
            
            # Simple re-alignment: split polished text into words and map back
            polished_words = clean_json_response(polished_text).split()
            # If Gemini changed word count significantly, we fallback to original words
            if abs(len(polished_words) - len(words)) < 5:
                for i in range(min(len(words), len(polished_words))):
                    words[i]['word'] = polished_words[i]
            
            final_vtt = generate_vtt_from_words(words)
            with open(edited_vtt, "w", encoding="utf-8") as f:
                f.write(final_vtt)

        # Ensure polished_text is available for description
        polished_text = extract_text_from_vtt(edited_vtt)

        # 6. Finalization
        while True:
            print(f"\n1. Editar legenda | 2. Gerar vídeo completo | 3. Gerar apenas descrição | 4. Gerar apenas miniatura | 5. Agendar no Instagram | 6. Próximo")
            sub_choice = input("Escolha: ")
            if sub_choice == '1':
                subprocess.run(["micro" if subprocess.run(["which", "micro"], capture_output=True).returncode == 0 else "vim", str(edited_vtt)])
            elif sub_choice == '2':
                srt_file = short_dir / "segment.srt"
                run_command(["ffmpeg", "-y", "-i", str(edited_vtt), str(srt_file)])
                
                # Style: White text, Size 12, Middle Center, Light Shadow (No Outline)
                style = (
                    "FontSize=12,"
                    "FontName=Verdana,"
                    "Bold=1,"
                    "PrimaryColour=&H00FFFFFF,"   # White Text
                    "OutlineColour=&H00000000,"   # Transparent/None
                    "BackColour=&H80000000,"      # Semi-transparent Black Shadow
                    "BorderStyle=1,"              # Outline + Shadow mode
                    "Outline=0,"                  # No outline
                    "Shadow=1,"                   # Light shadow, smaller distance
                    "Alignment=10,"               # Middle Center
                    "MarginV=0"
                )
                escaped_srt = str(srt_file).replace(":", "\\:").replace("'", "'\\''")
                run_command([
                    "ffmpeg", "-y", "-i", str(segment_file),
                    "-vf", f"crop=ih*9/16:ih,scale=1080:1920,subtitles='{escaped_srt}':force_style='{style}'",
                    "-c:v", "libx264", "-crf", "18", "-c:a", "aac", str(final_video)
                ])
                
                # New: Thumbnail and Description
                thumbnail_file = short_dir / "thumbnail.jpg"
                generate_thumbnail(final_video, selected['title'], thumbnail_file)
                
                desc = generate_description(selected['title'], polished_text, video_url)
                desc_file = short_dir / "description.txt"
                with open(desc_file, "w", encoding="utf-8") as f:
                    f.write(desc)
                
                print(f"\n✅ Concluído!")
                print(f"Vídeo: {final_video}")
                print(f"Thumbnail: {thumbnail_file}")
                print(f"Descrição: {desc_file}")
            elif sub_choice == '3':
                desc = generate_description(selected['title'], polished_text, video_url)
                desc_file = short_dir / "description.txt"
                with open(desc_file, "w", encoding="utf-8") as f:
                    f.write(desc)
                print(f"\n--- Descrição Gerada ---\n{desc}\n------------------------")
                print(f"Salvo em: {desc_file}")
            elif sub_choice == '4':
                thumbnail_file = short_dir / "thumbnail.jpg"
                source = final_video if final_video.exists() else segment_file
                generate_thumbnail(source, selected['title'], thumbnail_file)
                print(f"✅ Thumbnail pronta: {thumbnail_file}")
            elif sub_choice == '5':
                if not final_video.exists():
                    print("⚠️ Gere o vídeo (opção 2) primeiro!")
                    continue
                
                desc_file = short_dir / "description.txt"
                if desc_file.exists():
                    with open(desc_file, "r", encoding="utf-8") as f:
                        caption = f.read()
                else:
                    caption = input("Legenda do post: ")
                
                thumbnail_file = short_dir / "thumbnail.jpg"
                if not thumbnail_file.exists():
                    thumbnail_file = None
                
                print("\nQuando deseja postar?")
                print("Formatos aceitos: 'agora', 'HH:MM', 'DD/MM HH:MM'")
                time_input = input("Horário: ")
                
                now = datetime.datetime.now()
                if time_input.lower() == 'agora':
                    sched_time = now + datetime.timedelta(minutes=1)
                elif len(time_input) == 5: # HH:MM
                    try:
                        sched_time = datetime.datetime.strptime(time_input, "%H:%M").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        if sched_time < now:
                            sched_time += datetime.timedelta(days=1)
                    except:
                        print("Formato inválido.")
                        continue
                else: # DD/MM HH:MM
                    try:
                        sched_time = datetime.datetime.strptime(time_input, "%d/%m %H:%M").replace(year=now.year)
                    except:
                        print("Formato inválido.")
                        continue
                
                schedule_instagram_post(final_video, caption, thumbnail_file, sched_time.strftime("%Y-%m-%d %H:%M"))

            elif sub_choice == '6':
                break

if __name__ == "__main__":
    main()
