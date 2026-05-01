from flask import Flask, render_template, request, send_file, jsonify, after_this_request, Response
import os, uuid, yt_dlp, shutil, subprocess, json, threading, time, re
from pathlib import Path

app = Flask(__name__)
BASE_DIR = 'downloads'
os.makedirs(BASE_DIR, exist_ok=True)

# --- ffmpeg path fix ---
FFMPEG_DIR = os.getcwd()
os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

ARIA2C_AVAILABLE = shutil.which('aria2c') is not None

# ---------- Job progress store ----------
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

def job_create(job_id: str):
    with _jobs_lock:
        _jobs[job_id] = {
            'status': 'pending',
            'percent': 0.0,
            'speed': '',
            'eta': '',
            'msg': 'Starting…',
            'done': False,
            'error': None,
        }

def job_update(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)

def job_get(job_id: str) -> dict | None:
    with _jobs_lock:
        return dict(_jobs.get(job_id, {}))

def job_delete(job_id: str):
    with _jobs_lock:
        _jobs.pop(job_id, None)

# Cleanup stale jobs/folders on startup
def _startup_cleanup():
    cutoff = time.time() - 3600  # 1 hour
    try:
        for d in Path(BASE_DIR).iterdir():
            if d.is_dir() and d.name.startswith('job-'):
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

_startup_cleanup()

# ---------- helpers ----------

def ydl_base_opts(progress_cb=None):
    hooks = []
    if progress_cb:
        hooks.append(progress_cb)

    opts = {
        'verbose': False,
        'noplaylist': True,
        'retries': 10,
        'fragment_retries': 50,
        'ffmpeg_location': FFMPEG_DIR,
        'ignoreconfig': True,
        'sleep_interval': 0,
        'max_sleep_interval': 0,
        'http_chunk_size': 20971520,
        'sleep_interval_requests': 0,
        'throttledratelimit': 0,
        'nocheckcertificate': True,
        'concurrent_fragment_downloads': 16,
        'buffersize': 16384,
        'socket_timeout': 30,
        'progress_hooks': hooks,
        'remote_components': ['ejs:github'],
    }

    if ARIA2C_AVAILABLE:
        opts['external_downloader'] = 'aria2c'
        opts['external_downloader_args'] = {
            'aria2c': [
                '--max-connection-per-server=16',
                '--min-split-size=5M',
                '--split=16',
                '--max-concurrent-downloads=16',
                '--file-allocation=none',
                '--auto-file-renaming=false',
                '--continue=true',
                '--summary-interval=0',
                '--console-log-level=warn',
            ]
        }

    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'

    return opts


def safe(s: str) -> str:
    return ''.join(c for c in s if c not in r'\/:*?"<>|' and ord(c) >= 32).strip()


def sz_bytes_est(fmt: dict, duration: float) -> int:
    known = fmt.get('filesize') or fmt.get('filesize_approx') or 0
    if known:
        return int(known)
    br = fmt.get('tbr') or fmt.get('vbr') or fmt.get('abr')
    if br and duration:
        return int(br * 1000.0 / 8.0 * float(duration))
    return 0


def parse_ts(s: str):
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    parts = s.split(':')
    try:
        if len(parts) == 1:
            return float(parts[0])
        parts = [float(p) for p in parts]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    except Exception:
        return None


def find_existing(base_tpl: str):
    for ext in ('mp4', 'mkv', 'webm'):
        p = base_tpl.replace('%(ext)s', ext)
        if os.path.exists(p):
            return p
    return base_tpl.replace('%(ext)s', 'mp4')


def needs_reencode_from_fmt(fmt: dict) -> bool:
    """Determine re-encode need from yt-dlp format metadata (no ffprobe needed).
    Conservative: if unsure, returns True so ffmpeg always produces a valid MP4."""
    if fmt is None:
        return True
    vcodec = (fmt.get('vcodec') or '').lower()
    acodec = (fmt.get('acodec') or '').lower()
    # If vcodec info is missing entirely, assume re-encode needed
    if not vcodec or vcodec == 'none':
        return True
    # Only h264/avc streams can be stream-copied into MP4 without re-encode
    if not (vcodec.startswith('avc') or vcodec.startswith('h264')):
        return True  # hevc, vp9, av01, bytevc1, etc.
    if acodec and acodec != 'none':
        if not any(acodec.startswith(x) for x in ('aac', 'mp4a', 'mp3')):
            return True
    return False


def get_cpu_threads() -> int:
    try:
        import multiprocessing
        return min(multiprocessing.cpu_count(), 8)
    except Exception:
        return 4


def encode_mp4(src_path: str, dst_path: str, t_start, t_end, reencode: bool,
               job_id: str = None, audio_bitrate: str = '192k'):
    threads = get_cpu_threads()
    do_trim = t_start is not None or t_end is not None

    if job_id:
        job_update(job_id, msg='Processing with ffmpeg…', percent=90.0)

    args = ['ffmpeg', '-y', '-threads', str(threads)]

    if t_start is not None:
        args += ['-ss', str(t_start)]

    args += ['-i', src_path]

    if t_end is not None:
        if t_start is not None:
            args += ['-t', str(t_end - t_start)]
        else:
            args += ['-to', str(t_end)]

    if not reencode:
        args += ['-c', 'copy']
        if do_trim:
            args += ['-avoid_negative_ts', '1']
        args += ['-movflags', '+faststart', dst_path]
    else:
        args += [
            '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'fastdecode',
            '-crf', '22', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', audio_bitrate, '-ac', '2',
            '-threads', str(threads), '-movflags', '+faststart', dst_path
        ]

    subprocess.run(args, check=True, timeout=3600)


def fetch_info(url: str) -> dict:
    info_opts = {
        'quiet': True, 'noplaylist': True,
        'nocheckcertificate': True, 'socket_timeout': 20,
        'remote_components': ['ejs:github'],
    }
    if os.path.exists('cookies.txt'):
        info_opts['cookiefile'] = 'cookies.txt'
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        return ydl.extract_info(url, download=False)


# ---------- transcription ----------

def transcribe_video(url: str, job_id: str, work: str) -> str:
    """Download audio and transcribe via whisper (if available) or yt-dlp subtitles."""
    job_update(job_id, msg='Fetching subtitles/transcript…', percent=10.0)

    # Try yt-dlp subtitles first (fastest)
    info_opts = {
        'quiet': True, 'noplaylist': True, 'nocheckcertificate': True,
        'socket_timeout': 20, 'writesubtitles': True, 'writeautomaticsub': True,
        'subtitlesformat': 'vtt', 'skip_download': True,
        'outtmpl': os.path.join(work, 'sub.%(ext)s'),
    }
    if os.path.exists('cookies.txt'):
        info_opts['cookiefile'] = 'cookies.txt'

    try:
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Find downloaded vtt/srt
        for f in Path(work).glob('sub*.vtt'):
            text = vtt_to_text(f.read_text(encoding='utf-8', errors='ignore'))
            if len(text.strip()) > 20:
                job_update(job_id, msg='Transcript ready (auto-captions)', percent=100.0, done=True)
                return text

        for f in Path(work).glob('sub*.srt'):
            text = srt_to_text(f.read_text(encoding='utf-8', errors='ignore'))
            if len(text.strip()) > 20:
                job_update(job_id, msg='Transcript ready (auto-captions)', percent=100.0, done=True)
                return text

    except Exception as e:
        job_update(job_id, msg=f'Subtitles unavailable, trying Whisper… ({e})', percent=30.0)

    # Fallback: download audio + whisper
    whisper_bin = shutil.which('whisper') or shutil.which('whisper-ctranslate2')
    if not whisper_bin:
        # Try openai-whisper python
        try:
            import whisper as _whisper
            job_update(job_id, msg='Downloading audio for Whisper…', percent=40.0)
            audio_path = _download_audio_for_transcription(url, work)
            job_update(job_id, msg='Transcribing with Whisper (this may take a while)…', percent=60.0)
            model = _whisper.load_model('base')
            result = model.transcribe(audio_path)
            text = result.get('text', '')
            job_update(job_id, msg='Transcript ready (Whisper)', percent=100.0, done=True)
            return text
        except ImportError:
            raise RuntimeError(
                'No transcript available for this video and Whisper is not installed. '
                'Install it with: pip install openai-whisper'
            )

    # whisper CLI fallback
    job_update(job_id, msg='Downloading audio for Whisper…', percent=40.0)
    audio_path = _download_audio_for_transcription(url, work)
    job_update(job_id, msg='Transcribing with Whisper…', percent=60.0)
    result = subprocess.run(
        [whisper_bin, audio_path, '--model', 'base', '--output_format', 'txt',
         '--output_dir', work],
        capture_output=True, text=True, timeout=600
    )
    txt_path = Path(work) / (Path(audio_path).stem + '.txt')
    if txt_path.exists():
        text = txt_path.read_text(encoding='utf-8', errors='ignore')
        job_update(job_id, msg='Transcript ready (Whisper)', percent=100.0, done=True)
        return text
    raise RuntimeError(f'Whisper failed: {result.stderr[:200]}')


def _download_audio_for_transcription(url: str, work: str) -> str:
    out_tpl = os.path.join(work, 'audio.%(ext)s')
    opts = {
        'quiet': True, 'noplaylist': True, 'nocheckcertificate': True,
        'format': 'bestaudio/best', 'outtmpl': out_tpl,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}],
    }
    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'
    yt_dlp.YoutubeDL(opts).download([url])
    for f in Path(work).glob('audio.*'):
        return str(f)
    raise RuntimeError('Audio download failed')


def vtt_to_text(vtt: str) -> str:
    lines, seen = [], set()
    for line in vtt.splitlines():
        line = line.strip()
        if '-->' in line or not line or line.startswith('WEBVTT') or line.startswith('NOTE'):
            continue
        # Strip vtt tags
        clean = re.sub(r'<[^>]+>', '', line)
        clean = re.sub(r'&amp;', '&', clean)
        clean = re.sub(r'&lt;', '<', clean)
        clean = re.sub(r'&gt;', '>', clean)
        clean = clean.strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return ' '.join(lines)


def srt_to_text(srt: str) -> str:
    lines, seen = [], set()
    for line in srt.splitlines():
        line = line.strip()
        if re.match(r'^\d+$', line) or '-->' in line or not line:
            continue
        clean = re.sub(r'<[^>]+>', '', line).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return ' '.join(lines)


# ---------- core download ----------

def make_progress_hook(job_id: str, stage_offset: float = 0.0, stage_weight: float = 85.0):
    def hook(d):
        if d['status'] == 'downloading':
            pct_raw = d.get('_percent_str', '').strip().replace('%', '')
            try:
                pct = float(pct_raw)
            except (ValueError, TypeError):
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                pct = (downloaded / total * 100) if total else 0

            speed = d.get('_speed_str', '').strip()
            eta = d.get('_eta_str', '').strip()
            mapped = stage_offset + (pct / 100.0) * stage_weight
            job_update(job_id, percent=mapped, speed=speed, eta=eta,
                       msg=f'Downloading… {pct:.0f}%', status='downloading')

        elif d['status'] == 'finished':
            job_update(job_id, percent=stage_offset + stage_weight,
                       msg='Download complete, processing…', status='processing')

        elif d['status'] == 'error':
            job_update(job_id, status='error', error=str(d.get('error', 'Unknown error')), done=True)

    return hook


def download_one(url: str, height: int, audio_only: bool, do_trim: bool,
                 t_start, t_end, work: str, job_id: str = None,
                 no_watermark: bool = False, audio_bitrate: str = '192k'):

    job_update(job_id, msg='Fetching video info…', percent=2.0)
    info = fetch_info(url)
    title = safe(info.get('title', 'video'))
    stub = f'{title}-{uuid.uuid4().hex[:6]}'
    tiktok_domain = 'tiktok.com' in url or 'vm.tiktok.com' in url
    instagram_domain = 'instagram.com' in url

    hook = make_progress_hook(job_id, stage_offset=5.0, stage_weight=80.0) if job_id else None

    if audio_only:
        out_tpl = os.path.join(work, f'{stub}.%(ext)s')
        opts = ydl_base_opts(hook)
        # Strip aria2c for audio (overkill for small files)
        opts.pop('external_downloader', None)
        opts.pop('external_downloader_args', None)
        opts.update({
            'format': 'bestaudio/best',
            'outtmpl': out_tpl,
            'postprocessors': [{'key': 'FFmpegExtractAudio',
                                'preferredcodec': 'mp3',
                                'preferredquality': audio_bitrate.replace('k', '')}],
        })
        yt_dlp.YoutubeDL(opts).download([url])
        final = out_tpl.replace('%(ext)s', 'mp3')

        if do_trim and (t_start is not None or t_end is not None):
            if job_id:
                job_update(job_id, msg='Trimming audio…', percent=92.0)
            trimmed = os.path.join(work, f'{stub}_trim.mp3')
            trim_args = ['ffmpeg', '-y']
            if t_start is not None:
                trim_args += ['-ss', str(t_start)]
            trim_args += ['-i', final]
            if t_end is not None:
                trim_args += ['-t', str(t_end - t_start)] if t_start else ['-to', str(t_end)]
            trim_args += ['-c', 'copy', trimmed]
            subprocess.run(trim_args, check=True, timeout=600)
            os.replace(trimmed, final)

        return final, f'{title}.mp3'

    # --- Video ---
    H = int(height)
    raw_tpl = os.path.join(work, f'{stub}_raw.%(ext)s')

    opts = ydl_base_opts(hook)
    opts.update({
        'outtmpl': raw_tpl,
        'merge_output_format': 'mp4',
    })

    if tiktok_domain:
        # TikTok: always force re-encode since bytevc1/h265 needs transcoding anyway.
        # Use simple "best" selection — avoid complex format strings that can produce audio-only.
        # The watermark-free path uses yt-dlp's download_ranges / format_id filtering.
        if no_watermark:
            # Prefer the non-watermarked h264 stream when available, else best video+audio
            opts['format'] = (
                f'bestvideo[height<={H}][vcodec^=avc1]+bestaudio'
                f'/bestvideo[height<={H}]+bestaudio'
                f'/best[height<={H}]'
                f'/best'
            )
        else:
            opts['format'] = (
                f'bestvideo[height<={H}]+bestaudio'
                f'/best[height<={H}]'
                f'/best'
            )
        # Always re-encode TikTok — bytevc1/hevc → h264 for broad compatibility
        reencode = True
    elif instagram_domain:
        # Instagram: always re-encode — formats are typically h264 but may vary;
        # yt-dlp merges video+audio as needed.
        opts['format'] = (
            f'bestvideo[height<={H}]+bestaudio'
            f'/best[height<={H}]'
            f'/best'
        )
        reencode = True
    else:
        # YouTube / other: prefer h264 for stream-copy speed
        opts['format'] = (
            f'bestvideo[height<={H}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]'
            f'/bestvideo[height<={H}][ext=mp4]+bestaudio[ext=m4a]'
            f'/bv*[height<={H}]+ba'
            f'/b[height<={H}]/b'
        )
        opts['format_sort'] = ['res:desc', 'ext:mp4:m4a', 'vcodec:h264', 'acodec:aac', 'br:desc']

        # For trimmed downloads, let yt-dlp fetch only the needed segment
        if do_trim and (t_start is not None or t_end is not None):
            from yt_dlp.utils import download_range_func
            opts['download_ranges'] = download_range_func(None, [(t_start or 0, t_end or float('inf'))])
            opts['force_keyframes_at_cuts'] = True
            # aria2c doesn't support range downloads reliably
            opts.pop('external_downloader', None)
            opts.pop('external_downloader_args', None)

        # Determine if re-encode needed from format metadata
        fmts = info.get('formats', [])
        best_fmt = None
        for f in sorted(fmts, key=lambda x: sz_bytes_est(x, info.get('duration', 0)), reverse=True):
            if f.get('height') and f['height'] <= H and f.get('vcodec', 'none') != 'none':
                best_fmt = f
                break
        reencode = needs_reencode_from_fmt(best_fmt)

    yt_dlp.YoutubeDL(opts).download([url])

    raw_path = find_existing(raw_tpl)
    final_mp4 = os.path.join(work, f'{stub}.mp4')

    # If yt-dlp's download_ranges already trimmed the segment, don't re-apply timestamps
    ydlp_trimmed = do_trim and 'download_ranges' in opts
    encode_mp4(
        raw_path, final_mp4,
        None if ydlp_trimmed else (t_start if do_trim else None),
        None if ydlp_trimmed else (t_end if do_trim else None),
        reencode, job_id,
        audio_bitrate=audio_bitrate,
    )

    return final_mp4, f'{title}.mp4'


# ---------- routes ----------

@app.get('/resolutions')
def resolutions():
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'no url'}), 400

    try:
        info = fetch_info(url)
        fmts = info.get('formats', [])
    except Exception as e:
        return jsonify({'error': f'yt-dlp failed: {e}'}), 500

    duration = info.get('duration') or 0

    def is_real_video(f):
        if f.get('vcodec') in (None, 'none'): return False
        if not f.get('height'): return False
        note = (f.get('format_note') or '').lower()
        if 'storyboard' in note or note == 'sb' or 'preview' in note: return False
        ext = (f.get('ext') or '').lower()
        if ext in ('mhtml', 'jpg', 'png', 'webp'): return False
        return True

    video_fmts = [f for f in fmts if is_real_video(f)]
    audio_fmts = [f for f in fmts if f.get('vcodec') == 'none']
    audio_sz = 0
    if audio_fmts:
        audio_best = max(audio_fmts, key=lambda f: sz_bytes_est(f, duration))
        audio_sz = sz_bytes_est(audio_best, duration)

    heights = sorted({f['height'] for f in video_fmts if f.get('height')}, reverse=True)

    def std(h):
        for t in [2160, 1440, 1080, 720, 480, 360, 240, 144]:
            if h >= t: return t
        return h

    lst, added = [], set()
    for h in heights:
        label = std(h)
        if label in added: continue
        added.add(label)
        group = [f for f in video_fmts if f.get('height') and std(f['height']) == label]
        if not group: continue
        best = max(group, key=lambda f: sz_bytes_est(f, duration))
        best_bytes = sz_bytes_est(best, duration)
        total_bytes = best_bytes + (0 if best.get('acodec') != 'none' else audio_sz)
        if total_bytes <= 0: continue
        vcodec = best.get('vcodec', '')
        needs_encode = not (vcodec.startswith('avc') or vcodec.startswith('h264'))
        lst.append({'height': label, 'size': round(total_bytes / 1048576.0, 1), 'fast': not needs_encode})

    # Chapters
    chapters = []
    for ch in (info.get('chapters') or []):
        chapters.append({
            'title': ch.get('title', f'Chapter'),
            'start': ch.get('start_time', 0),
            'end': ch.get('end_time', duration),
        })

    return jsonify({
        'title': info.get('title', ''),
        'thumb': info.get('thumbnail', ''),
        'duration': duration,
        'native': heights[0] if heights else None,
        'audio_mb': round(audio_sz / 1048576.0, 1),
        'list': lst,
        'aria2c': ARIA2C_AVAILABLE,
        'chapters': chapters,
    })


@app.post('/start_download')
def start_download():
    """Start async download job, return job_id immediately."""
    url = request.form.get('url', '').strip()
    hgt = request.form.get('resolution')
    aud = request.form.get('audio_only') == 'on'
    do_trim = request.form.get('do_trim') == 'on'
    t_start = parse_ts(request.form.get('t_start', ''))
    t_end = parse_ts(request.form.get('t_end', ''))
    platform = request.form.get('platform', 'youtube')
    no_watermark = request.form.get('no_watermark') == 'on'
    # Validate and sanitise bitrate — only allow known safe values
    _allowed_bitrates = {'64k', '96k', '128k', '192k', '256k', '320k'}
    audio_bitrate = request.form.get('audio_bitrate', '192k')
    if audio_bitrate not in _allowed_bitrates:
        audio_bitrate = '192k'

    if not url or (not aud and not hgt):
        return jsonify({'error': 'Missing URL or resolution'}), 400

    job_id = uuid.uuid4().hex[:12]
    work = os.path.join(BASE_DIR, f'job-{job_id}')
    os.makedirs(work, exist_ok=True)
    job_create(job_id)

    def run():
        try:
            fp, name = download_one(url, hgt or 2160, aud, do_trim, t_start, t_end, work, job_id,
                                    no_watermark=no_watermark, audio_bitrate=audio_bitrate)
            job_update(job_id, status='done', percent=100.0, msg='Ready!', done=True,
                       filepath=fp, filename=name)
        except Exception as e:
            job_update(job_id, status='error', error=str(e), done=True, msg=f'Error: {e}')
            shutil.rmtree(work, ignore_errors=True)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.get('/job_status/<job_id>')
def job_status(job_id: str):
    """SSE stream for real-time progress."""
    def generate():
        while True:
            info = job_get(job_id)
            if not info:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            yield f"data: {json.dumps(info)}\n\n"
            if info.get('done'):
                break
            time.sleep(0.4)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.get('/download_file/<job_id>')
def download_file(job_id: str):
    info = job_get(job_id)
    if not info or info.get('status') != 'done':
        return 'Not ready', 404

    fp = info.get('filepath')
    name = info.get('filename', 'download')
    work = os.path.dirname(fp)

    if not fp or not os.path.exists(fp):
        return 'File not found', 404

    @after_this_request
    def clean(r):
        job_delete(job_id)
        shutil.rmtree(work, ignore_errors=True)
        return r

    return send_file(fp, as_attachment=True, download_name=name)


@app.post('/start_transcribe')
def start_transcribe():
    url = request.form.get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    job_id = uuid.uuid4().hex[:12]
    work = os.path.join(BASE_DIR, f'job-{job_id}')
    os.makedirs(work, exist_ok=True)
    job_create(job_id)

    def run():
        try:
            text = transcribe_video(url, job_id, work)
            job_update(job_id, status='done', percent=100.0, msg='Transcript ready!',
                       done=True, transcript=text)
        except Exception as e:
            job_update(job_id, status='error', error=str(e), done=True, msg=f'Error: {e}')
        finally:
            shutil.rmtree(work, ignore_errors=True)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/')
def home():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True, threaded=True)