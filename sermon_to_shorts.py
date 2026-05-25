import subprocess
import os
import sys
import json
import re
import datetime
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

def run_command(command, shell=False, check=True, input_text=None, silent=True):
    if not silent:
        print(f"Executing: {' '.join(command) if isinstance(command, list) else command}")
    result = subprocess.run(command, shell=shell, capture_output=True, text=True, input=input_text)
    if result.returncode != 0 and check:
        print(f"Error: {result.stderr}")
    return result

def run_spin_command(command, title="Processando...", silent=True, input_text=None):
    """Executa um comando exibindo um spinner do gum no stderr e capturando stdout."""
    gum_cmd = ["gum", "spin", "--spinner", "dot", "--title", title, "--"] + command
    try:
        # Usamos stderr=sys.stderr para que o spinner seja visível no terminal
        # E stdout=subprocess.PIPE para capturar o resultado do comando
        process = subprocess.Popen(gum_cmd, stdin=subprocess.PIPE if input_text else None, 
                                 stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
        stdout, _ = process.communicate(input=input_text)
        
        class Result:
            def __init__(self, stdout, returncode):
                self.stdout = stdout
                self.returncode = returncode
        
        return Result(stdout.strip(), process.returncode)
    except Exception as e:
        if not silent: print(f"Erro no spinner: {e}")
        return run_command(command, input_text=input_text, silent=silent)

def clean_styled_text(text):
    """Remove sequências de escape ANSI do gum style."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def clean_json_response(response_text):
    """Remove markdown formatting and extract JSON string."""
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)
    return response_text.strip()

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

def get_audio_channels_info(file_path):
    """Analisa os níveis de áudio por canal para detectar canais silenciosos."""
    cmd = [
        "ffmpeg", "-i", str(file_path),
        "-af", "astats=metadata=1:reset=1",
        "-f", "null", "-"
    ]
    res = run_command(cmd, check=False)
    output = res.stderr
    
    channels_rms = {}
    lines = output.splitlines()
    current_channel = None
    for line in lines:
        if "Channel:" in line:
            match = re.search(r"Channel:\s+(\d+)", line)
            if match:
                current_channel = int(match.group(1))
        elif "RMS level dB:" in line and current_channel is not None:
            match = re.search(r"RMS level dB:\s+([\-\d\.\w]+)", line)
            if match:
                val_str = match.group(1)
                try:
                    val = float(val_str)
                except ValueError:
                    if "inf" in val_str.lower():
                        val = -100.0
                    else:
                        continue
                channels_rms[current_channel] = val
                
    return channels_rms

def get_non_silent_intervals(file_path, noise=-45, duration=0.3):
    """Detecta intervalos não-silenciosos para sincronizar vídeo e áudio, com buffer de segurança."""
    cmd = [
        "ffmpeg", "-i", str(file_path),
        "-af", f"silencedetect=noise={noise}dB:d={duration}",
        "-f", "null", "-"
    ]
    res = run_command(cmd, check=False)
    output = res.stderr
    
    silence_ranges = []
    current_start = None
    
    for line in output.splitlines():
        if "silence_start" in line:
            m = re.search(r"silence_start: ([\d\.]+)", line)
            if m: current_start = float(m.group(1))
        elif "silence_end" in line:
            m = re.search(r"silence_end: ([\d\.]+)", line)
            if m:
                end = float(m.group(1))
                if current_start is not None:
                    silence_ranges.append((current_start, end))
                    current_start = None
                else:
                    silence_ranges.append((0.0, end))

    # FFmpeg doesn't always show the final silence_end
    # So we get total duration first
    dur_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
    dur_res = run_command(dur_cmd, check=False)
    try:
        total_duration = float(dur_res.stdout.strip())
    except:
        total_duration = 99999
        
    non_silent = []
    last_pos = 0.0
    buffer = 0.2
    
    for s_start, s_end in silence_ranges:
        start = max(0, last_pos - buffer)
        end = min(total_duration, s_start + buffer)
        
        if end > start + 0.1:
            non_silent.append((start, end))
        last_pos = s_end

    if last_pos < total_duration:
        start = max(0, last_pos - buffer)
        end = total_duration
        if end > start + 0.1:
            non_silent.append((start, end))

    if not non_silent:
        non_silent = [(0.0, total_duration)]

    return non_silent

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
        elif line.strip() and not line.startswith("WEBVTT") and 'Kind:' not in line and 'Language:' not in line:
            current_text_lines.append(line.strip())
    if current_ts:
        cues.append({'ts': current_ts, 'text': " ".join(current_text_lines)})

    # 2. Processamento incremental para montagem da master list
    for cue in cues:
        ts_match = re.findall(r'(\d+:\d+:\d+\.\d+|\d+:\d+\.\d+|\d+:\d+:\d+|\d+:\d+)', cue['ts'])
        if not ts_match: continue
        c_start = to_ms(norm_ts(ts_match[0]))
        c_end = to_ms(norm_ts(ts_match[1]))
        
        # Extrai palavras deste cue com seus timestamps
        cue_words = []
        parts = re.split(r'(<[^>]+>)', cue['text'])
        last_ts = c_start
        for p in parts:
            if p.startswith('<'):
                m = re.search(r'(\d+:\d+:\d+\.\d+|\d+:\d+\.\d+)', p)
                if m:
                    try:
                        last_ts = to_ms(norm_ts(m.group(1)))
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
            
        max_overlap = 0
        lookback = min(len(master_words), 50)
        for i in range(1, min(lookback, len(cue_words)) + 1):
            master_suffix = [mw['word'].lower() for mw in master_words[-i:]]
            cue_prefix = [cw['word'].lower() for cw in cue_words[:i]]
            if master_suffix == cue_prefix:
                max_overlap = i
        
        master_words.extend(cue_words[max_overlap:])

    if not master_words: return []

    # 3. Ajuste de tempos e filtragem
    for i in range(len(master_words) - 1):
        if master_words[i+1]['start'] > master_words[i]['start']:
            master_words[i]['end'] = master_words[i+1]['start']
        else:
            master_words[i]['end'] = master_words[i]['start'] + 200

    segment_words = [w for w in master_words if w['start'] >= (start_ms - 1000) and w['start'] <= (end_ms + 1000)]
    
    for w in segment_words:
        w['start'] -= start_ms
        w['end'] -= start_ms
        if w['start'] < 0: w['start'] = 0
        if w['end'] < 100: w['end'] = 100
        
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
    with open(vtt_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    lines = content.splitlines()
    text_lines = []
    for line in lines:
        if '-->' in line or not line.strip() or line.startswith("WEBVTT"):
            continue
        text_lines.append(line.strip())
    return " ".join(text_lines)

def generate_description(title, polished_text, video_url):
    instructions = (
        "Você é um social media manager. Crie uma descrição curta e impactante para este Reels/Short.\n"
        "Inclua hashtags relevantes e um Call to Action (CTA) para seguir o perfil e ver o vídeo completo.\n"
        f"Link do vídeo original: {video_url}\n\n"
        "Texto do vídeo:\n"
    )
    result = run_spin_command(["gemini", "-m", "gemini-3.1-flash-lite", "-p", instructions], input_text=polished_text, title="Gerando descrição viral...")
    return result.stdout.strip()

def generate_thumbnail(video_path, title, output_path):
    """Gera uma thumbnail rica com desfoque, overlay verde escuro e fonte Crimson Pro."""
    duration = get_duration(video_path)
    middle = duration / 2
    
    # Tenta localizar a fonte Crimson Pro ou fallback
    font_paths = [
        "/home/weinne/.local/share/fonts/c/CrimsonPro_VariableFont_wght.ttf",
        "/usr/share/fonts/truetype/crimsonpro/CrimsonPro-Bold.ttf",
        "/app/fonts/CrimsonPro-Bold.ttf", # Caminho no Docker
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    font_path = next((p for p in font_paths if os.path.exists(p)), "")
    font_arg = f":fontfile='{font_path}'" if font_path else ""
    
    # Envelopamento de linha para o título
    words = title.split()
    lines, curr = [], []
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
        f"drawtext={font_arg}:text='{wrapped_title}':fontcolor=white:fontsize=80:"
        f"line_spacing=20:x=(w-text_w)/2:y=(h-text_h)/2"
    )

    run_command([
        "ffmpeg", "-y", "-ss", str(middle), "-i", str(video_path),
        "-frames:v", "1", "-vf", vf, str(output_path)
    ], silent=True)
    return output_path.exists()

def get_insta_client():
    cl = Client()
    session_file = "insta_session.json"

    if os.path.exists(session_file):
        cl.load_settings(session_file)

    username = os.getenv("INSTA_USER") or input("Instagram Username: ")
    password = os.getenv("INSTA_PASS") or input("Instagram Password: ")

    try:
        cl.login(username, password)
    except TwoFactorRequired:
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

def schedule_instagram_post(video_path, caption, thumbnail_path, schedule_time_str, collaborators=None):
    queue_dir = Path("insta_queue")
    queue_dir.mkdir(exist_ok=True)

    post_id = f"post_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    post_data = {
        "video": str(video_path.absolute()),
        "caption": caption,
        "thumbnail": str(thumbnail_path.absolute()) if thumbnail_path else None,
        "scheduled_for": schedule_time_str,
        "collaborators": collaborators or []
    }

    with open(queue_dir / f"{post_id}.json", "w", encoding="utf-8") as f:
        json.dump(post_data, f, indent=4)

    print(f"\n✅ Post agendado para {schedule_time_str}!")

def get_video_id(url):
    result = run_command(["yt-dlp", "--get-id", url])
    return result.stdout.strip()

def run_gum(command, input_text=None):
    """Executa o gum garantindo que o TUI seja visível e interativo."""
    import sys
    try:
        if "pager" in command:
            subprocess.run(command, input=input_text, text=True, stderr=sys.stderr, stdout=sys.stdout)
            return ""
        
        if input_text:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
            stdout, _ = process.communicate(input=input_text)
            return stdout.strip()
        else:
            process = subprocess.Popen(command, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
            stdout, _ = process.communicate()
            return stdout.strip()
    except Exception as e:
        return ""

def stage1_preview(video_url, start_time, end_time, short_dir):
    """ETAPA 1: Módulo de Preview Dinâmico no Terminal"""
    preview_file = short_dir / "preview_low.mp4"
    test_clip = short_dir / "preview_test.mp4"
    
    s_ms = to_ms(norm_ts(start_time))
    e_ms = to_ms(norm_ts(end_time))
    preview_dur_ms = min(30000, max(15000, e_ms - s_ms))
    if (e_ms - s_ms) < 15000:
        preview_dur_ms = e_ms - s_ms
        
    preview_end_ts = format_ts(s_ms + preview_dur_ms)

    # Download preview section with spin
    run_spin_command([
        "yt-dlp", "-f", "worstvideo[height<=240][fps<=15]+worstaudio/worst",
        "--download-sections", f"*{start_time}-{preview_end_ts}",
        "-o", str(preview_file), video_url
    ], title="Baixando trecho para preview...")

    if not preview_file.exists():
        return False

    # Process test clip with spin
    run_spin_command([
        "ffmpeg", "-y", "-i", str(preview_file),
        "-vf", "crop=ih*(9/16):ih:(iw-ow)/2:0,scale=w=-2:h=480",
        "-c:v", "libx264", "-preset", "ultrafast", str(test_clip)
    ], title="Processando preview...")

    try:
        subprocess.run(["mpv", "--ontop", "--no-terminal", str(test_clip)], check=False)
    except:
        subprocess.run(["xdg-open", str(test_clip)], check=False)

    confirm = run_gum(["gum", "confirm", "O enquadramento está correto?"])
    
    if preview_file.exists(): preview_file.unlink()
    if test_clip.exists(): test_clip.unlink()

    return confirm == "true"

def stage2_render_premium(video_url, start_time, end_time, srt_path, output_path, short_dir):
    """ETAPA 2: Pipeline de Renderização Premium (Matriz FFmpeg)"""
    high_res_file = short_dir / "high_res_segment.mp4"

    if not high_res_file.exists():
        run_spin_command([
            "yt-dlp", "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/mp4",
            "--download-sections", f"*{start_time}-{end_time}",
            "-o", str(high_res_file), video_url
        ], title="Baixando vídeo em alta resolução...")

    if not high_res_file.exists():
        return False

    escaped_srt = str(srt_path).replace(":", "\\:").replace("'", "'\\''")
    style = "FontSize=12,FontName=Verdana,Bold=1,PrimaryColour=&H00FFFFFF,Alignment=10,MarginV=10"

    intervals = get_non_silent_intervals(high_res_file)
    between_expr = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in intervals])

    fps_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(high_res_file)]
    fps_res = run_command(fps_cmd, check=False)
    fps = fps_res.stdout.strip()
    if not fps or fps == "0/0": fps = "30"

    vf = [
        f"select='{between_expr}'",
        "hqdn3d=1.5:1.5:3:3",
        "crop=ih*(9/16):ih:(iw-ow)/2:0",
        "scale=1080:1920:flags=lanczos",
        "colorbalance=rm=-0.08:rh=-0.03",
        "eq=gamma=1.10:contrast=1.12:brightness=-0.02:saturation=1.1",
        "unsharp=5:5:1.0",
        "cas=strength=0.8",
        f"subtitles='{escaped_srt}':force_style='{style}'",
        f"setpts=N/({fps})/TB"
    ]

    rms_stats = get_audio_channels_info(high_res_file)
    audio_fix = None
    if len(rms_stats) >= 2:
        c1 = rms_stats.get(1, -100)
        c2 = rms_stats.get(2, -100)
        if abs(c1 - c2) > 10:
            audio_fix = "pan=stereo|c0=c0+c1|c1=c0+c1" if c1 > c2 else "pan=stereo|c0=c1+c0|c1=c1+c0"

    af = [f"aselect='{between_expr}'", "asetpts=PTS-STARTPTS"]
    if audio_fix: af.append(audio_fix)
    af.extend(["acompressor=threshold=-12dB:ratio=4:attack=5:release=50", "loudnorm=I=-16:TP=-1.5:LRA=11"])

    cmd = [
        "ffmpeg", "-y", "-i", str(high_res_file),
        "-vf", ",".join(vf),
        "-af", ",".join(af),
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path)
    ]
    
    result = run_spin_command(cmd, title="Renderizando vídeo premium (FFmpeg)...")
    return output_path.exists()

def start_file_server(port=None, directory="outputs"):
    """Inicia um servidor web básico em background para servir os arquivos gerados."""
    import http.server
    import socketserver
    import threading
    
    # Se port não for passado, tenta ler do ambiente (padrão Coolify/PaaS)
    if port is None:
        port = int(os.getenv("PORT", 8080))
    
    os.makedirs(directory, exist_ok=True)
    
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)
        def log_message(self, format, *args):
            return 

    def run():
        try:
            with socketserver.TCPServer(("", port), Handler) as httpd:
                httpd.serve_forever()
        except Exception:
            pass 
            
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port

def main():
    # Inicia o servidor de arquivos no background
    server_port = start_file_server()
    if len(sys.argv) < 2:
        print("Usage: python sermon_to_shorts.py [YOUTUBE_URL]")
        return

    video_url = sys.argv[1]
    video_id = get_video_id(video_url)
    if not video_id: return

    project_dir = Path(f"outputs/{video_id}")
    project_dir.mkdir(parents=True, exist_ok=True)
    
    vtt_file = project_dir / "transcript.vtt"
    clean_txt = project_dir / "clean_transcript.txt"
    analysis_json = project_dir / "analysis.json"

    if not vtt_file.exists():
        run_spin_command([
            "yt-dlp", "--write-auto-subs", "--write-subs", 
            "--sub-langs", "pt.*,en.*", "--sub-format", "vtt", 
            "--skip-download", "-o", str(project_dir / "transcript"), video_url
        ], title="Buscando legendas...")
        
        found_vtt = None
        for suffix in [".pt.vtt", ".pt-orig.vtt", ".en.vtt", ".vtt"]:
            f = project_dir / f"transcript{suffix}"
            if f.exists(): found_vtt = f; break
        if found_vtt: os.rename(found_vtt, vtt_file)
        else: return

    if not clean_txt.exists():
        with open(vtt_file, 'r', encoding='utf-8') as f: content = f.read()
        
        # Faz a deduplicação PESADA (palavra por palavra) na legenda completa
        print("--- 🧹 Limpando e deduplicando legenda completa ---")
        master_words = parse_youtube_vtt_to_words(content, 0, 99999999)
        
        # Gera o clean_transcript.txt para o Gemini a partir das palavras limpas
        clean_lines = []
        word_count = 0
        for w in master_words:
            m_val, s_val = w['start'] // 60000, (w['start'] % 60000) // 1000
            if word_count % 10 == 0:
                clean_lines.append(f"\n[{m_val:02}:{s_val:02}] {w['word']}")
            else:
                clean_lines.append(w['word'])
            word_count += 1
            
        with open(clean_txt, "w", encoding="utf-8") as f:
            f.write(" ".join(clean_lines))
    else:
        # Se o clean_txt já existe, apenas carregamos master_words para o TUI
        with open(vtt_file, 'r', encoding='utf-8') as f: content = f.read()
        master_words = parse_youtube_vtt_to_words(content, 0, 99999999)

    if not analysis_json.exists():
        prompt = (
            "Você é um estrategista de conteúdo viral religioso.\n"
            "Analise a transcrição e encontre os 10 melhores momentos (30-75s).\n"
            "IMPORTANTE: NÃO CORTE ideias no meio. Seja preciso com timestamps [MM:SS].\n"
            "Retorne JSON: " + '[{"start": "MM:SS", "end": "MM:SS", "title": "...", "score": 95, "reason": "..."}]'
        )
        with open(clean_txt, 'r', encoding='utf-8') as f: transcript = f.read()
        gemini_cmd = ["gemini", "-m", "gemini-3.1-pro-preview", "-p", prompt, "-o", "json"]
        result = run_spin_command(gemini_cmd, input_text=transcript, title="Gemini analisando ganchos virais...")
        try:
            res = clean_json_response(result.stdout)
            try:
                data = json.loads(res)
                if isinstance(data, dict) and 'response' in data: res = clean_json_response(data['response'])
            except: pass
            json.loads(res)
            with open(analysis_json, "w", encoding="utf-8") as f: f.write(res)
        except: return
    
    with open(analysis_json, "r", encoding="utf-8") as f: moments = json.load(f)

    while True:
        options = []
        for i, m in enumerate(moments):
            s = int(m.get('score', 0))
            options.append(f"{i+1:02}. [{s} pts] ({m['start']}-{m['end']}) {m['title']}")
        options.extend(["---", "➕ Recorte Personalizado", "🔄 Todos", "❌ Sair"])

        sel_raw = run_gum(["gum", "choose", "--header", "🔥 Escolha um momento viral", "--height", "15", "--cursor.foreground", "212"], input_text="\n".join(options))
        if not sel_raw or "Sair" in sel_raw: break
        
        if "Recorte Personalizado" in sel_raw:
            with open(vtt_file, 'r', encoding='utf-8') as f: vtt_c = f.read()
            all_w = parse_youtube_vtt_to_words(vtt_c, 0, 99999999) # Pega todas as palavras
            
            if not all_w:
                print("⚠️ Erro ao carregar palavras da legenda.")
                continue

            # 1. Selecionar palavra de INÍCIO
            opts = [f"{format_ts(w['start'])}: {w['word']}" for w in all_w]
            start_sel = run_gum(["gum", "choose", "--header", "📍 Selecione onde o recorte deve COMEÇAR", "--height", "20", "--filter"], input_text="\n".join(opts))
            
            if not start_sel: continue
            c_s_ms = to_ms(start_sel[:12])
            
            # 2. Selecionar palavra de FIM (começando da palavra de início)
            start_idx = next((i for i, w in enumerate(all_w) if w['start'] >= c_s_ms), 0)
            end_opts = [f"{format_ts(w['start'])}: {w['word']}" for w in all_w[start_idx:]]
            end_sel = run_gum(["gum", "choose", "--header", "🏁 Selecione onde o recorte deve TERMINAR", "--height", "20", "--filter"], input_text="\n".join(end_opts))
            
            if not end_sel: continue
            c_e_ms = to_ms(end_sel[:12])
            
            c_t = run_gum(["gum", "input", "--placeholder", "Título do vídeo personalizado"])
            
            if c_s_ms < c_e_ms:
                moments.append({
                    "start": format_ts(c_s_ms)[3:8], # MM:SS para o JSON
                    "end": format_ts(c_e_ms)[3:8], 
                    "title": c_t or "Recorte Personalizado", 
                    "score": 100
                })
                indices = [len(moments)-1]
            else:
                print("⚠️ O fim deve ser após o início.")
                continue
        elif "Todos" in sel_raw: indices = list(range(len(moments)))
        else:
            indices = []
            for line in sel_raw.splitlines():
                try: indices.append(int(line.split('.')[0]) - 1)
                except: continue

        for idx in indices:
            if idx < 0 or idx >= len(moments): continue
            selected = moments[idx]
            back_to_selection = False
            s_ms = max(0, to_ms(norm_ts(selected['start'])) - 2000)
            e_ms = to_ms(norm_ts(selected['end'])) + 2000
            
            short_id = f"momo_{idx+1}_{selected['start'].replace(':', '')}_{re.sub(r'[^a-zA-Z0-9]', '_', selected['title'])[:15]}"
            short_dir = project_dir / "shorts" / short_id
            short_dir.mkdir(parents=True, exist_ok=True)
            edited_vtt = short_dir / "edited.vtt"
            final_video = short_dir / "final.mp4"

            if not edited_vtt.exists():
                with open(vtt_file, 'r', encoding='utf-8') as f: vtt_c = f.read()
                while True:
                    words = parse_youtube_vtt_to_words(vtt_c, s_ms, e_ms)
                    print(f"\n--- ✂️ Ajuste: {selected['title']} ({format_ts(s_ms)} - {format_ts(e_ms)}) ---")
                    act = run_gum(["gum", "choose", "Continuar", "Ajustar Início", "Ajustar Fim", "Ver Trecho", "Cancelar", "--height", "10"])
                    if act == "Continuar": break
                    if not act or act == "Cancelar": back_to_selection = True; break
                    all_w = parse_youtube_vtt_to_words(vtt_c, 0, 9999999)
                    if "Início" in act or "Fim" in act:
                        c_idx = next((i for i, w in enumerate(all_w) if w['start'] >= (s_ms if "Início" in act else e_ms)), 0)
                        window = all_w[max(0, c_idx-40):min(len(all_w), c_idx+40)]
                        opts = ["<- Voltar"] + [f"{format_ts(w['start'])}: {w['word']}" for w in window]
                        new_o = run_gum(["gum", "choose", "--header", f"Novo {'INÍCIO' if 'Início' in act else 'FIM'}", "--height", "15"] + opts)
                        if new_o and new_o != "<- Voltar":
                            if "Início" in act: s_ms = to_ms(new_o[:12])
                            else: e_ms = to_ms(new_o[:12])
                    elif act == "Ver Trecho": run_gum(["gum", "pager"], input_text=" ".join([w['word'] for w in words]))

                if back_to_selection or not words: continue
                raw_t = " ".join([w['word'] for w in words])
                polish_res = run_spin_command(["gemini", "-m", "gemini-3.1-flash-lite", "-p", "Pontue e corrija gramática: "], input_text=raw_t, title="Refinando texto...")
                p_text = clean_json_response(polish_res.stdout)
                p_words = p_text.split()
                if abs(len(p_words) - len(words)) < 5:
                    for i in range(min(len(words), len(p_words))): words[i]['word'] = p_words[i]
                with open(edited_vtt, "w", encoding="utf-8") as f: f.write(generate_vtt_from_words(words))

            while True:
                polished_text = extract_text_from_vtt(edited_vtt)
                print(f"\n--- 🎬 Shorts: {selected['title']} ---")
                
                if final_video.exists():
                    # Calcula o caminho relativo para a URL
                    rel_path = final_video.relative_to(Path("outputs"))
                    base_url = os.getenv("PUBLIC_URL", f"http://localhost:{server_port}")
                    # Garante que a base_url termina com / ou rel_path começa sem /
                    full_url = f"{base_url.rstrip('/')}/{rel_path}"
                    print(f"🔗 Download: {full_url}")

                choice = run_gum(["gum", "choose", "--height", "12", "Preview", "Editar Legenda", "Renderizar", "Ver Descrição", "Nova Thumbnail", "Postar Instagram", "Próximo", "Voltar à Seleção", "Sair do Programa"])
                if not choice or choice == "Voltar à Seleção": back_to_selection = True; break
                if choice == "Sair do Programa": sys.exit(0)
                if choice == "Preview": stage1_preview(video_url, format_ts(s_ms), format_ts(e_ms), short_dir)
                elif choice == "Editar Legenda": subprocess.run(["micro" if subprocess.run(["which", "micro"], capture_output=True).returncode == 0 else "vim", str(edited_vtt)])
                elif choice == "Renderizar":
                    srt = short_dir / "segment.srt"
                    run_command(["ffmpeg", "-y", "-i", str(edited_vtt), str(srt)])
                    if stage2_render_premium(video_url, format_ts(s_ms), format_ts(e_ms), srt, final_video, short_dir):
                        generate_thumbnail(final_video, selected['title'], short_dir / "thumbnail.jpg")
                        desc = generate_description(selected['title'], polished_text, video_url)
                        with open(short_dir / "description.txt", "w", encoding="utf-8") as f: f.write(desc)
                        print(f"✅ Pronto: {final_video}")
                elif choice == "Ver Descrição":
                    df = short_dir / "description.txt"
                    if df.exists(): run_gum(["gum", "pager"], input_text=open(df).read())
                elif choice == "Nova Thumbnail": generate_thumbnail(final_video if final_video.exists() else (short_dir / "high_res_segment.mp4"), selected['title'], short_dir / "thumbnail.jpg")
                elif choice == "Postar Instagram":
                    if not final_video.exists(): continue
                    cl = get_insta_client()
                    if not cl: continue
                    cap = open(short_dir / "description.txt").read()
                    if run_gum(["gum", "confirm", "Editar legenda?"]) == "true":
                        tmp = short_dir / "temp_desc.txt"
                        with open(tmp, "w") as f: f.write(cap)
                        subprocess.run(["micro" if subprocess.run(["which", "micro"], capture_output=True).returncode == 0 else "vim", str(tmp)])
                        cap = open(tmp).read(); tmp.unlink()
                    col_in = run_gum(["gum", "input", "--placeholder", "Colaboradores (csv)"])
                    cols = []
                    if col_in:
                        for c in col_in.split(","):
                            try: cols.append(cl.user_id_from_username(c.strip()))
                            except: pass
                    t_in = run_gum(["gum", "input", "--placeholder", "Horário"])
                    now = datetime.datetime.now()
                    if not t_in or t_in.lower() == 'agora': sch = now + datetime.timedelta(minutes=1)
                    elif len(t_in) == 5:
                        sch = datetime.datetime.strptime(t_in, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
                        if sch < now: sch += datetime.timedelta(days=1)
                    else: sch = datetime.datetime.strptime(t_in, "%d/%m %H:%M").replace(year=now.year)
                    schedule_instagram_post(final_video, cap, short_dir / "thumbnail.jpg", sch.strftime("%Y-%m-%d %H:%M"), cols)
                elif choice == "Próximo": break
            if back_to_selection: break

if __name__ == "__main__":
    main()
