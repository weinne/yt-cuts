import subprocess
import os
import sys
import json
import re
import datetime
from pathlib import Path

# TUI Libraries
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
    from rich.live import Live
    from rich.panel import Panel
    import questionary
    TUI_AVAILABLE = True
except ImportError:
    TUI_AVAILABLE = False

try:
    from instagrapi import Client
    INSTA_AVAILABLE = True
except ImportError:
    INSTA_AVAILABLE = False

console = Console() if TUI_AVAILABLE else None

IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "") or "com.termux" in os.environ.get("PATH", "")

def get_ytdlp_command(args):
    """Retorna o comando yt-dlp com flags de bypass, cookies e simulação de navegador."""
    base = ["yt-dlp"]
    
    # Adiciona cookies se arquivo existir
    cookies_file = os.getenv("YT_COOKIES_PATH", "cookies.txt")
    if os.path.exists(cookies_file):
        base.extend(["--cookies", cookies_file])
    
    # Runtime JS para evitar avisos e melhorar extração
    base.extend(["--js-runtimes", "node"])
    
    # Simulação de Navegador e Anonimato (Modo Incógnito)
    # Nota: --impersonate chrome foi removido pois causa erro de dependência no Termux
    base.extend([
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--referer", "https://www.google.com/",
        "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "--add-header", "Accept-Language:pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "--no-check-certificates",
        "--no-cache-dir",
        "--geo-bypass"
    ])
    
    # Remove "yt-dlp" do início dos args se estiver lá
    if args and args[0] == "yt-dlp":
        args = args[1:]
        
    return base + args

# Whisper.cpp Configuration
WHISPER_REPO = Path.home() / "whisper.cpp-repo"
WHISPER_BIN = WHISPER_REPO / "build/bin/whisper-cli"
WHISPER_MODEL = WHISPER_REPO / "models/ggml-medium.bin"

def run_command(command, shell=False, check=True, input_text=None, silent=True):
    if not silent:
        print(f"Executing: {' '.join(command) if isinstance(command, list) else command}")
    result = subprocess.run(command, shell=shell, capture_output=True, text=True, input=input_text)
    if result.returncode != 0 and check:
        print(f"Error: {result.stderr}")
    return result

def run_spin_command(command, title="Processando...", silent=True, input_text=None):
    """Executa um comando exibindo um spinner do Rich ou apenas texto se falhar."""
    if not TUI_AVAILABLE:
        if not silent: print(f"⌛ {title}")
        return run_command(command, input_text=input_text, silent=silent)
        
    with console.status(f"[bold green]{title}[/bold green]", spinner="dots"):
        result = subprocess.run(command, capture_output=True, text=True, input=input_text)
        
    class Result:
        def __init__(self, stdout, returncode, stderr=""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr
            
    return Result(result.stdout.strip(), result.returncode, result.stderr)

def run_progress_command(command, title="Processando", total_duration=None):
    """Executa comando e tenta extrair progresso para exibir uma barra do Rich."""
    if not TUI_AVAILABLE:
        return run_spin_command(command, title).returncode == 0

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console
    )

    try:
        with progress:
            task = progress.add_task(f"[cyan]{title}", total=100)
            
            if "yt-dlp" in command[0]:
                cmd = command + ["--newline", "--progress", "--progress-template", "download:%(progress._percent_str)s"]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in process.stdout:
                    if "download:" in line:
                        m = re.search(r"(\d+(\.\d+)?)%", line)
                        if m:
                            progress.update(task, completed=float(m.group(1)))
                process.wait()
                return process.returncode == 0

            elif "ffmpeg" in command[0]:
                cmd = command + ["-progress", "pipe:1", "-nostats", "-loglevel", "quiet"]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for line in process.stdout:
                    if "out_time_ms=" in line:
                        try:
                            time_us = int(line.split('=')[1])
                            if total_duration and total_duration > 0:
                                percent = min(100.0, (time_us / 1000000.0) / total_duration * 100.0)
                                progress.update(task, completed=percent)
                        except: pass
                    if "progress=end" in line:
                        progress.update(task, completed=100.0)
                process.wait()
                return process.returncode == 0
    except Exception:
        return run_spin_command(command, title).returncode == 0
    
    return run_spin_command(command, title).returncode == 0

def clean_json_response(response_text):
    """Remove markdown formatting and extract JSON string."""
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)
    return response_text.strip()

def to_ms(t):
    # Suporta tanto . quanto , para milissegundos
    parts = re.split('[:.,]', t)
    if len(parts) == 3: # MM:SS.mmm
        return (int(parts[0])*60 + int(parts[1]))*1000 + int(parts[2])
    elif len(parts) == 4: # HH:MM:SS.mmm
        return (int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]))*1000 + int(parts[3])
    return 0

def norm_ts(ts):
    if ts.count(':') == 1: ts = "00:" + ts
    if '.' not in ts and ',' not in ts: ts = ts + ".000"
    return ts

def format_ts(ms):
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"

def rebase_vtt_content(content, intervals):
    """Reajusta os timestamps de um VTT/SRT baseado nos intervalos de silêncio removidos."""
    def adjust_ts(ms, intervals):
        new_ms = 0
        for s, e in intervals:
            s_ms, e_ms = int(s*1000), int(e*1000)
            if ms < s_ms:
                # Se o tempo está num buraco de silêncio, move para o início do próximo bloco
                # ou fim do anterior. Aqui movemos para o início do tempo acumulado.
                continue 
            if ms <= e_ms:
                return new_ms + (ms - s_ms)
            new_ms += (e_ms - s_ms)
        return new_ms

    lines = content.splitlines()
    new_lines = []
    is_vtt = any("WEBVTT" in l for l in lines[:3])
    
    if is_vtt:
        new_lines.append("WEBVTT")
        new_lines.append("")

    for line in lines:
        if '-->' in line:
            parts = line.split('-->')
            start_str = parts[0].strip()
            end_str = parts[1].strip()
            
            # Captura coordenadas extras do VTT se existirem
            end_parts = end_str.split(' ', 1)
            end_ts_str = end_parts[0]
            extra = " " + end_parts[1] if len(end_parts) > 1 else ""
            
            start_ms = to_ms(norm_ts(start_str))
            end_ms = to_ms(norm_ts(end_ts_str))
            
            new_start = adjust_ts(start_ms, intervals)
            new_end = adjust_ts(end_ms, intervals)
            
            # FFmpeg subtitles filter aceita formato . mesmo em SRT, mas vamos manter o padrão
            sep = "." if is_vtt else ","
            ts_start = format_ts(new_start).replace(".", sep)
            ts_end = format_ts(new_end).replace(".", sep)
            
            new_lines.append(f"{ts_start} --> {ts_end}{extra}")
        elif "WEBVTT" in line:
            continue
        else:
            new_lines.append(line)
            
    return "\n".join(new_lines)

def transcribe_with_whisper(audio_path, output_vtt):
    """Transcreve áudio usando whisper.cpp."""
    if not WHISPER_BIN.exists():
        print("⚠️ Whisper binary not found.")
        return False
        
    wav_16k = audio_path.with_suffix(".16k.wav")
    # Converte para 16kHz mono wav
    run_spin_command([
        "ffmpeg", "-y", "-i", str(audio_path), 
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", 
        str(wav_16k)
    ], title="Convertendo áudio para Whisper...")
    
    if not wav_16k.exists():
        return False
        
    # Executa o whisper.cpp
    cmd = [
        str(WHISPER_BIN), 
        "-m", str(WHISPER_MODEL), 
        "-f", str(wav_16k), 
        "-ovtt", 
        "-l", "pt", 
        "-t", "4"
    ]
    
    run_spin_command(cmd, title="Whisper transcrevendo áudio...")
    
    # whisper-cli gera um arquivo com o nome do input + .vtt
    vtt_gen = Path(str(wav_16k) + ".vtt")
    if vtt_gen.exists():
        os.rename(vtt_gen, output_vtt)
        if wav_16k.exists(): os.remove(wav_16k)
        return True
    
    return False

def transcribe_segment_with_whisper(video_url, start_ts, end_ts, output_vtt, short_dir):
    """Baixa apenas o áudio do segmento e transcreve com Whisper."""
    segment_audio = short_dir / "segment_audio.wav"
    high_res_video = short_dir / "high_res_segment.mp4"
    
    if high_res_video.exists():
        print(f"📦 Extraindo áudio do arquivo local...")
        run_spin_command(["ffmpeg", "-y", "-i", str(high_res_video), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(segment_audio)], title="Extraindo áudio...")
    else:
        # Baixa apenas o pedaço do áudio usando ffmpeg + yt-dlp (rápido para segmentos curtos)
        print(f"📥 Baixando áudio do segmento ({start_ts} - {end_ts})...")
        cmd_dl = get_ytdlp_command([
            "-x", "--audio-format", "wav", 
            "--external-downloader", "ffmpeg",
            "--external-downloader-args", f"ffmpeg_i:-ss {start_ts} -to {end_ts}",
            "-o", str(segment_audio), video_url
        ])
        run_spin_command(cmd_dl, title="Baixando áudio do trecho...")
    
    # Garantir que o nome esteja correto (yt-dlp às vezes adiciona .wav extra)
    if not segment_audio.exists() and Path(str(segment_audio) + ".wav").exists():
        os.rename(str(segment_audio) + ".wav", segment_audio)

    if segment_audio.exists():
        success = transcribe_with_whisper(segment_audio, output_vtt)
        # Limpeza
        if segment_audio.exists(): os.remove(segment_audio)
        return success
    return False

def transcribe_with_gemini_fallback(audio_path, output_vtt):
    """Fallback usando Gemini para transcrever o áudio (via Gemini CLI se disponível)."""
    print("🚀 Usando Gemini como fallback para transcrição...")
    # Para o Gemini transcrever, precisaríamos enviar o arquivo. 
    # Atualmente a gemini-cli é usada para texto.
    # Como alternativa, podemos extrair o texto usando yt-dlp auto-subs se ainda não tentamos,
    # ou usar o Gemini para tentar adivinhar/corrigir se tivermos algo.
    # Mas se chegamos aqui, é porque falhou o resto.
    # Por enquanto, vamos retornar False e deixar o usuário saber.
    return False

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
    buffer_start = 0.1  # Pequeno buffer antes de começar a falar
    buffer_end = 0.3    # Buffer maior após terminar de falar para não cortar palavras
    
    for s_start, s_end in silence_ranges:
        # O intervalo não-silencioso é do fim do silêncio anterior até o início do próximo silêncio
        start = max(0, last_pos - buffer_start)
        end = min(total_duration, s_start + buffer_end)
        
        if end > start + 0.1:
            non_silent.append((start, end))
        last_pos = s_end

    if last_pos < total_duration:
        start = max(0, last_pos - buffer_start)
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
        str(Path.home() / ".fonts/CrimsonPro/CrimsonPro-Variable.ttf"),
        "/data/data/com.termux/files/usr/share/fonts/CrimsonPro/CrimsonPro-Variable.ttf",
        "/home/weinne/.local/share/fonts/c/CrimsonPro_VariableFont_wght.ttf",
        "/usr/share/fonts/truetype/crimsonpro/CrimsonPro-Bold.ttf",
        "/app/fonts/CrimsonPro-Bold.ttf", # Caminho no Docker
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    font_path = next((p for p in font_paths if os.path.exists(p)), "")
    
    # Escapa caracteres especiais para o FFmpeg
    escaped_font_path = font_path.replace(":", "\\:").replace("'", "'\\''")
    
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

    # Filtros para o drawtext
    drawtext_options = [
        f"text='{wrapped_title}'",
        "fontcolor=white",
        "fontsize=80",
        "line_spacing=20",
        "x=(w-text_w)/2",
        "y=(h-text_h)/2"
    ]
    if font_path:
        drawtext_options.insert(0, f"fontfile='{escaped_font_path}'")

    vf = (
        f"gblur=sigma=20,"
        f"drawbox=t=fill:color=0x002200@0.7,"
        f"drawbox=x=60:y=60:w=iw-120:h=ih-120:color=white@0.3:t=5,"
        f"drawtext={':'.join(drawtext_options)}"
    )

    run_command([
        "ffmpeg", "-y", "-ss", str(middle), "-i", str(video_path),
        "-frames:v", "1", "-vf", vf, str(output_path)
    ], silent=True)
    return output_path.exists()

def get_insta_client():
    if not INSTA_AVAILABLE:
        print("\n❌ Instagrapi não instalado. Função de Instagram desabilitada.")
        return None
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

def schedule_instagram_post(*args, **kwargs):
    if not INSTA_AVAILABLE:
        return
    return schedule_instagram_post_original(*args, **kwargs)
def schedule_instagram_post_original(video_path, caption, thumbnail_path, schedule_time_str, collaborators=None):
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
    print(f"🔍 Identificando vídeo: {url}")
    # Usamos run_command diretamente para evitar problemas com o gum nesta etapa
    result = run_command(get_ytdlp_command(["--get-id", url]))
    if result.returncode != 0:
        print(f"❌ Erro ao identificar vídeo: {result.stderr}")
        return None
    
    # Pega apenas a última linha não vazia (o ID), ignorando avisos que possam ter saído no stdout
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return None
    
    video_id = lines[-1]
    # Validação básica do ID (geralmente 11 caracteres)
    if len(video_id) > 20: 
        # Se for muito longo, provavelmente algo deu errado no output
        print(f"⚠️ Aviso: ID detectado parece inválido: {video_id[:20]}...")
        return None
        
    return video_id

def run_gum(command, input_text=None):
    """Substitui o gum por Questionary e Rich para maior compatibilidade."""
    if not TUI_AVAILABLE:
        # FALLBACK: Menu de texto simples se as bibliotecas não estiverem disponíveis
        if "choose" in command:
            print(f"\n--- SELEÇÃO ---")
            options = input_text.splitlines() if input_text else [arg for arg in command if not arg.startswith("-") and arg not in ["gum", "choose"]]
            for i, opt in enumerate(options):
                print(f"{i+1}) {opt}")
            try:
                escolha = input("\nEscolha o número (ou texto): ")
                if escolha.isdigit():
                    idx = int(escolha) - 1
                    return options[idx] if 0 <= idx < len(options) else ""
                return escolha
            except: return ""
        elif "confirm" in command:
            prompt = command[-1] if not command[-1].startswith("-") else "Confirmar?"
            c = input(f"{prompt} (s/n): ").lower()
            return "true" if c in ['s', 'y', 'sim', 'yes'] else "false"
        elif "input" in command:
            placeholder = ""
            if "--placeholder" in command:
                placeholder = command[command.index("--placeholder")+1]
            return input(f"{placeholder}: ")
        elif "pager" in command:
            print(input_text)
            return ""
        return ""

    try:
        if "choose" in command:
            options = input_text.splitlines() if input_text else [arg for arg in command[command.index("choose")+1:] if not arg.startswith("-")]
            header = ""
            if "--header" in command:
                header = command[command.index("--header")+1]
            
            result = questionary.select(
                header or "Selecione uma opção:",
                choices=options,
                use_indicator=True
            ).ask()
            return result or ""
            
        elif "confirm" in command:
            prompt = command[-1] if not command[-1].startswith("-") else "Confirmar?"
            result = questionary.confirm(prompt, default=True).ask()
            return "true" if result else "false"
            
        elif "input" in command:
            placeholder = ""
            if "--placeholder" in command:
                placeholder = command[command.index("--placeholder")+1]
            result = questionary.text(placeholder or "Digite:").ask()
            return result or ""
            
        elif "pager" in command:
            console.print(Panel(input_text or "", title="Visualizador", border_style="blue"))
            questionary.press_any_key_to_continue().ask()
            return ""
            
    except Exception:
        return ""
        
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

    # Download preview section with progress
    run_progress_command(get_ytdlp_command([
        "-f", "worstvideo[height<=240][fps<=15]+worstaudio/worst",
        "--download-sections", f"*{start_time}-{preview_end_ts}",
        "-o", str(preview_file), video_url
    ]), title="Baixando trecho preview")

    if not preview_file.exists():
        return False

    # Process test clip with spin
    run_spin_command([
        "ffmpeg", "-y", "-i", str(preview_file),
        "-vf", "crop=ih*(9/16):ih:(iw-ow)/2:0,scale=w=-2:h=480",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(test_clip)
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
    
    # Calcular duração para a barra de progresso
    duration_secs = (to_ms(norm_ts(end_time)) - to_ms(norm_ts(start_time))) / 1000

    if not high_res_file.exists():
        run_progress_command(get_ytdlp_command([
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/mp4",
            "--download-sections", f"*{start_time}-{end_time}",
            "-o", str(high_res_file), video_url
        ]), title="Baixando alta resolução")

    if not high_res_file.exists():
        return False

    escaped_srt = str(srt_path).replace(":", "\\:").replace("'", "'\\''")
    style = "FontSize=12,FontName=Verdana,Bold=1,PrimaryColour=&H00FFFFFF,Alignment=10,MarginV=10"

    intervals = get_non_silent_intervals(high_res_file)
    between_expr = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in intervals])

    # Reajustar SRT para bater com o novo timeline sem silêncios
    rebased_srt_path = srt_path
    if srt_path.exists():
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
        rebased_content = rebase_vtt_content(srt_content, intervals)
        rebased_srt_path = srt_path.with_suffix(".rebased.srt")
        with open(rebased_srt_path, 'w', encoding='utf-8') as f:
            f.write(rebased_content)

    escaped_srt = str(rebased_srt_path).replace(":", "\\:").replace("'", "'\\''")
    style = "FontSize=12,FontName=Verdana,Bold=1,PrimaryColour=&H00FFFFFF,Alignment=10,MarginV=10"

    fps_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(high_res_file)]
    fps_res = run_command(fps_cmd, check=False)
    fps = fps_res.stdout.strip()
    if not fps or fps == "0/0": fps = "30"

    # Perfil de processamento adaptativo
    if IS_TERMUX:
        # Mobile: Software encoding (libx264) é mais estável no Termux que MediaCodec
        vf = [
            f"select='{between_expr}'",
            f"setpts=N/({fps})/TB",
            "hqdn3d=1.0:1.0:3:3",
            "crop=ih*(9/16):ih:(iw-ow)/2:0",
            "scale=1080:1920:flags=lanczos",
            "colorbalance=rm=-0.05:rh=-0.02",
            "unsharp=5:5:0.8",
            "cas=strength=0.5",
            f"subtitles='{escaped_srt}':force_style='{style}'"
        ]
        vcodec = ["-c:v", "libx264", "-preset", "faster", "-crf", "20"]
    else:
        # Desktop: Foco em qualidade máxima (Premium chain)
        vf = [
            f"select='{between_expr}'",
            f"setpts=N/({fps})/TB",
            "hqdn3d=1.5:1.5:3:3",
            "crop=ih*(9/16):ih:(iw-ow)/2:0",
            "scale=1080:1920:flags=lanczos",
            "colorbalance=rm=-0.08:rh=-0.03",
            "eq=gamma=1.10:contrast=1.12:brightness=-0.02:saturation=1.1",
            "unsharp=5:5:1.0",
            "cas=strength=0.8",
            f"subtitles='{escaped_srt}':force_style='{style}'"
        ]
        vcodec = ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]

    rms_stats = get_audio_channels_info(high_res_file)
    audio_fix = None
    if len(rms_stats) >= 2:
        c1 = rms_stats.get(1, -100)
        c2 = rms_stats.get(2, -100)
        if abs(c1 - c2) > 10:
            audio_fix = "pan=stereo|c0=c0+c1|c1=c0+c1" if c1 > c2 else "pan=stereo|c0=c1+c0|c1=c1+c0"

    af = [f"aselect='{between_expr}'", "asetpts=N/SR/TB"]
    if audio_fix: af.append(audio_fix)
    af.extend([
        "afade=t=in:st=0:d=0.05", # Fade in no início total
        "acompressor=threshold=-12dB:ratio=4:attack=5:release=50", 
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    ])

    cmd = [
        "ffmpeg", "-y", "-threads", "0", "-i", str(high_res_file),
        "-vf", ",".join(vf),
        "-af", ",".join(af),
        *vcodec,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path)
    ]
    
    return run_progress_command(cmd, title="Renderizando vídeo premium", total_duration=duration_secs)

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
    
    video_url = None
    if len(sys.argv) < 2:
        # Tenta listar vídeos já analisados
        outputs_dir = Path("outputs")
        if outputs_dir.exists():
            projects = []
            for d in outputs_dir.iterdir():
                if d.is_dir() and (d / "transcript.vtt").exists():
                    # Tenta descobrir o título (por enquanto o ID)
                    projects.append(d.name)
            
            if projects:
                print("\n📂 Vídeos já analisados encontrados:")
                choice = run_gum(["gum", "choose", "--header", "Selecione um vídeo para continuar ou Sair", "Sair"] + projects)
                if not choice or choice == "Sair":
                    return
                # Reconstrói a URL a partir do ID (assumindo YouTube)
                video_url = f"https://www.youtube.com/watch?v={choice}"
            else:
                print("Usage: python sermon_to_shorts.py [YOUTUBE_URL]")
                return
        else:
            print("Usage: python sermon_to_shorts.py [YOUTUBE_URL]")
            return
    else:
        video_url = sys.argv[1]

    env_name = "Termux (Mobile)" if IS_TERMUX else "Desktop (Padrão)"
    print(f"🚀 Iniciando em ambiente: {env_name}")

    video_id = get_video_id(video_url)
    if not video_id:
        print("❌ Não foi possível identificar o vídeo. Verifique sua conexão ou a URL.")
        return

    project_dir = Path(f"outputs/{video_id}")
    project_dir.mkdir(parents=True, exist_ok=True)
    
    vtt_file = project_dir / "transcript.vtt"
    clean_txt = project_dir / "clean_transcript.txt"
    analysis_json = project_dir / "analysis.json"

    if not vtt_file.exists():
        print("DEBUG: transcript not found, starting download")
        run_progress_command(get_ytdlp_command([
            "--write-auto-subs", "--write-subs", 
            "--sub-langs", "pt.*,en.*", "--sub-format", "vtt", 
            "--skip-download", "-o", str(project_dir / "transcript"), video_url
        ]), title="Buscando legendas")
        
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
            color = "10" if s > 90 else "11" if s > 80 else "14"
            # Cria string estilizada para o gum choose
            score_part = f"[{s} pts]"
            time_part = f"({m['start']}-{m['end']})"
            options.append(f"{i+1:02}. {score_part} {time_part} {m['title']}")
        
        options.extend(["---", "➕ Recorte Personalizado", "🔄 Todos", "❌ Sair"])

        sel_raw = run_gum([
            "gum", "choose", 
            "--header", "🔥 Escolha um momento viral (ENTER para selecionar)", 
            "--height", "15", 
            "--cursor.foreground", "212",
            "--selected.foreground", "212",
            "--selected.bold"
        ], input_text="\n".join(options))
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

                choice = run_gum(["gum", "choose", "Preview", "Editar Legenda", "Legenda (Whisper)", "Renderizar", "Ver Descrição", "Nova Thumbnail", "Postar Instagram", "Próximo", "Voltar à Seleção", "Sair do Programa"])
                if not choice or choice == "Voltar à Seleção": back_to_selection = True; break
                if choice == "Sair do Programa": sys.exit(0)
                if choice == "Preview": stage1_preview(video_url, format_ts(s_ms), format_ts(e_ms), short_dir)
                elif choice == "Editar Legenda": subprocess.run(["micro" if subprocess.run(["which", "micro"], capture_output=True).returncode == 0 else "vim", str(edited_vtt)])
                elif choice == "Legenda (Whisper)":
                    if transcribe_segment_with_whisper(video_url, format_ts(s_ms), format_ts(e_ms), edited_vtt, short_dir):
                        print("✅ Legenda recriada com Whisper (Palavra por Palavra)!")
                    else:
                        print("❌ Erro ao transcrever com Whisper.")
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
