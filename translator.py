'''
极简翻译工具 - Double Ctrl to translate selected text
Alt+V 剪贴板历史
'''
import os
import io
import sys
import time
import struct
import asyncio
import sqlite3
import tempfile
import queue
import threading
import configparser
import winreg
import tkinter as tk
import ctypes
import requests
import pyperclip
import pystray
from PIL import Image, ImageDraw, ImageTk
from pynput import keyboard as pynkb
from pynput import mouse as pynmouse
import win32clipboard
import win32con

MODEL = 'glm-4-flash-250414'
DOUBLE_CLICK_INTERVAL = 0.4
STARTUP_NAME = 'Translator'

BG = '#1e1e1e'
BG2 = '#252525'
FG_ORIG = '#555555'
FG_TRANS = '#f0f0f0'
FG_SEP = '#2e2e2e'
SEL_BG = '#3a5a8a'
ACCENT = '#4a9eff'


def _app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE = os.path.join(_app_dir(), 'translator_config.ini')

CLIP_DB = os.path.join(_app_dir(), 'clipboard_history.db')
CLIP_MAX_ITEMS = 200
CLIP_POPUP_ROWS = 30
CLIP_ROW_H = 44
CLIP_POPUP_W = 430
CLIP_TEXT_PREVIEW_LEN = 120
CLIP_TEXT_MAX_STORE = 512 * 1024
CLIP_IMAGE_MAX_STORE = 8 * 1024 * 1024


def load_api_key() -> str:
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE, encoding='utf-8')
        return cfg.get('settings', 'api_key', fallback='').strip()
    return ''


def save_api_key(key: str):
    cfg = configparser.ConfigParser()
    cfg['settings'] = {'api_key': key}
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        cfg.write(f)


def is_startup_enabled() -> bool:
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           'Software\\Microsoft\\Windows\\CurrentVersion\\Run')
        winreg.QueryValueEx(k, STARTUP_NAME)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False


def set_startup(enable: bool):
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           'Software\\Microsoft\\Windows\\CurrentVersion\\Run',
                           0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(k, STARTUP_NAME, 0, winreg.REG_SZ,
                              f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(k, STARTUP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(k)
    except Exception:
        pass


def _make_icon() -> Image.Image:
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 61, 61], fill='#4a9eff')
    d.rectangle([15, 17, 49, 25], fill='white')
    d.rectangle([28, 17, 36, 47], fill='white')
    return img


def setup_tray(root: tk.Tk) -> pystray.Icon:
    def on_change_key(icon, item):
        def _do():
            global API_KEY
            key = show_key_dialog(root, current_key=API_KEY)
            if key:
                save_api_key(key)
                API_KEY = key
        root.after(0, _do)

    def on_toggle_startup(icon, item):
        set_startup(not is_startup_enabled())

    def on_quit(icon, item):
        icon.stop()
        root.after(0, root.quit)

    def on_clip_history(icon, item):
        root.after(0, _open_clipboard_popup)

    def on_clip_clear(icon, item):
        def _do():
            if _clip_store is None:
                return
            import tkinter.messagebox as mb
            if mb.askyesno('翻译工具', '清空全部剪贴板历史？'):
                _clip_store.clear()
        root.after(0, _do)

    menu = pystray.Menu(
        pystray.MenuItem('翻译工具', None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('剪贴板历史 (Alt+V)', on_clip_history),
        pystray.MenuItem('清空剪贴板历史', on_clip_clear),
        pystray.MenuItem('修改 API Key', on_change_key),
        pystray.MenuItem('开机自启', on_toggle_startup,
                         checked=lambda item: is_startup_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出', on_quit),
    )
    icon = pystray.Icon(STARTUP_NAME, _make_icon(),
                        '翻译工具  (双击 Ctrl 翻译选中文字)', menu)
    icon.run_detached()
    return icon


def show_key_dialog(root: tk.Tk, current_key: str = '') -> str | None:
    dialog = tk.Toplevel(root)
    dialog.title('翻译工具 - 设置')
    dialog.resizable(False, False)
    dialog.attributes('-topmost', True)
    dialog.configure(bg=BG)
    dialog.grab_set()
    pad = 24

    tk.Label(dialog, text='翻译工具设置',
             bg=BG, fg='#ffffff', font=('Segoe UI', 13, 'bold')
             ).pack(padx=pad, pady=(pad, 4), anchor='w')

    tk.Label(dialog,
             text='请输入您的 GLM API Key\n免费申请地址：bigmodel.cn  →  API Keys',
             bg=BG, fg='#888888', font=('Segoe UI', 9), justify='left'
             ).pack(padx=pad, anchor='w')

    tk.Frame(dialog, bg='#333333', height=1
             ).pack(fill='x', padx=pad, pady=(14, 0))

    f = tk.Frame(dialog, bg=BG2, padx=12, pady=10)
    f.pack(fill='x', padx=pad, pady=(10, 0))

    tk.Label(f, text='API Key', bg=BG2, fg='#aaaaaa',
             font=('Segoe UI', 9)).pack(anchor='w')

    entry_var = tk.StringVar(value=current_key)
    entry = tk.Entry(f, textvariable=entry_var, bg='#1a1a1a', fg='#f0f0f0',
                     insertbackground='#ffffff', relief='flat',
                     font=('Consolas', 10), width=44, show='*')
    entry.pack(fill='x', padx=(4, 0), ipady=6)

    show_var = tk.BooleanVar()
    tk.Checkbutton(f, text='显示密钥', variable=show_var,
                   command=lambda: entry.config(
                       show='' if show_var.get() else '*'),
                   bg=BG2, fg='#666666', selectcolor=BG2,
                   activebackground=BG2, font=('Segoe UI', 8), borderwidth=0
                   ).pack(anchor='w', pady=(4, 0))

    err_lbl = tk.Label(dialog, text='', bg=BG, fg='#ff6b6b',
                       font=('Segoe UI', 9))
    err_lbl.pack(padx=pad, anchor='w')

    btn_frame = tk.Frame(dialog, bg=BG)
    btn_frame.pack(fill='x', padx=pad, pady=(4, pad))

    result = {'key': None}

    def on_confirm():
        key = entry_var.get().strip()
        if not key:
            err_lbl.config(text='API Key 不能为空')
            return
        if '.' not in key or len(key) < 10:
            err_lbl.config(text='Key 格式不正确，请检查')
            return
        result['key'] = key
        dialog.destroy()

    tk.Button(btn_frame, text='取消', bg='#333333', fg='#888888',
              activebackground='#3a3a3a', relief='flat',
              font=('Segoe UI', 10), padx=16, pady=6, cursor='hand2',
              command=dialog.destroy).pack(side='right', padx=(8, 0))

    tk.Button(btn_frame, text='  确认  ', bg=ACCENT, fg='#ffffff',
              activebackground='#3a8eef', relief='flat',
              font=('Segoe UI', 10, 'bold'), padx=16, pady=6,
              cursor='hand2', command=on_confirm).pack(side='right')

    dialog.bind('<Return>', lambda e: on_confirm())
    dialog.bind('<Escape>', lambda e: dialog.destroy())

    dialog.update_idletasks()
    w, h = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
    dialog.geometry(f'+{(sw - w) // 2}+{(sh - h) // 2}')
    entry.focus_set()
    dialog.wait_window()
    return result['key']


API_KEY = ''


def detect_lang(text: str) -> str:
    for ch in text:
        if '一' <= ch <= '鿿':
            return 'zh'
    return 'en'


def translate(text: str) -> str:
    prompt = (f'请将以下中文翻译成英文，只输出翻译结果：\n{text}'
              if detect_lang(text) == 'zh'
              else f'请将以下英文翻译成中文，只输出翻译结果：\n{text}')
    try:
        r = requests.post('https://open.bigmodel.cn/api/paas/v4/chat/completions',
                          headers={'Authorization': f'Bearer {API_KEY}'},
                          json={'model': MODEL,
                                'messages': [{'role': 'user', 'content': prompt}],
                                'max_tokens': 512,
                                'temperature': 0.3},
                          timeout=30)
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        return f'[Error: {e}]'


def speak_text(text: str, btn: 'tk.Button', popup: 'tk.Toplevel'):
    """TTS via edge-tts → temp MP3 → Windows MCI playback."""
    def _do():
        path = None
        try:
            import edge_tts
            lang = detect_lang(text)
            voice = ('zh-CN-XiaoxiaoNeural' if lang == 'zh'
                     else 'en-US-JennyNeural')
            with tempfile.NamedTemporaryFile(suffix='.mp3',
                                             delete=False) as f:
                path = f.name
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    edge_tts.Communicate(text, voice).save(path))
            finally:
                loop.close()

            wm = ctypes.windll.winmm
            wm.mciSendStringW('close tts1', None, 0, None)
            wm.mciSendStringW(f'open "{path}" type mpegvideo alias tts1',
                              None, 0, None)
            wm.mciSendStringW('play tts1 wait', None, 0, None)
            wm.mciSendStringW('close tts1', None, 0, None)
        except Exception:
            pass
        finally:
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass
            try:
                if popup.winfo_exists():
                    popup.after(0, lambda: btn.config(
                        text='🔊', state='normal'))
            except Exception:
                pass
    threading.Thread(target=_do, daemon=True).start()


def get_mouse_pos() -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def send_ctrl_c():
    global _simulating
    _simulating = True
    VK_CTRL, VK_C, KEYUP = 17, 67, 2
    ctypes.windll.user32.keybd_event(VK_CTRL, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_C, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_C, 0, KEYUP, 0)
    ctypes.windll.user32.keybd_event(VK_CTRL, 0, KEYUP, 0)
    time.sleep(0.15)
    _simulating = False


def apply_rounded_corners(hwnd):
    try:
        v = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


_ui_queue: queue.Queue = queue.Queue()
_simulating = False
_ctrl_pressed = False
_ctrl_press_time = 0.0
_last_ctrl_click_t = 0.0
_other_key_in_ctrl = False

_clip_ignore_upto = 0
_alt_pressed = False
_alt_v_fired = False
_clip_store = None
_clip_manager = None


def _mark_own_clip_write():
    """抬高自家写入的剪贴板序号水位线，让监听线程跳过。"""
    global _clip_ignore_upto
    try:
        seq = ctypes.windll.user32.GetClipboardSequenceNumber()
        if seq > _clip_ignore_upto:
            _clip_ignore_upto = seq
    except Exception:
        pass


def translation_worker():
    mx, my = get_mouse_pos()
    try:
        old_clip = pyperclip.paste()
    except Exception:
        old_clip = ''
    pyperclip.copy('')
    _mark_own_clip_write()
    send_ctrl_c()
    text = pyperclip.paste().strip()
    try:
        if old_clip:
            pyperclip.copy(old_clip)
            _mark_own_clip_write()
    except Exception:
        pass
    if not text:
        return
    _ui_queue.put(('show', text, '翻译中…', mx, my))
    _ui_queue.put(('update', translate(text)))


MAX_HOLD = 0.35


_ALT_KEYS = (pynkb.Key.alt, pynkb.Key.alt_l, pynkb.Key.alt_r, pynkb.Key.alt_gr)


def _is_v_key(key):
    if getattr(key, 'vk', None) == 86:
        return True
    return getattr(key, 'char', None) in ('v', 'V')


def _open_clipboard_popup():
    """pynput 钩子线程不能直接调 Tk，走 _ui_queue 由主线程轮询执行。"""
    if _clip_manager is None:
        return
    _ui_queue.put(('cliptoggle',))


def on_key_press(key):
    global _ctrl_pressed, _ctrl_press_time, _other_key_in_ctrl
    global _alt_pressed, _alt_v_fired
    if _simulating:
        return
    if key in _ALT_KEYS:
        _alt_pressed = True
        if _ctrl_pressed:
            _other_key_in_ctrl = True
        return
    if _alt_pressed and not _alt_v_fired and _is_v_key(key):
        _alt_v_fired = True
        _open_clipboard_popup()
        return
    if key in (pynkb.Key.ctrl_l, pynkb.Key.ctrl_r):
        if _ctrl_pressed: return
        _ctrl_pressed = True
        _ctrl_press_time = time.time()
        _other_key_in_ctrl = False
    if _ctrl_pressed:
        _other_key_in_ctrl = True
        return


def on_key_release(key):
    global _ctrl_pressed, _last_ctrl_click_t
    global _alt_pressed, _alt_v_fired
    if _simulating:
        return
    if key in _ALT_KEYS:
        _alt_pressed = False
        _alt_v_fired = False
        return
    if key not in (pynkb.Key.ctrl_l, pynkb.Key.ctrl_r):
        return
    if not _ctrl_pressed:
        return
    hold = time.time() - _ctrl_press_time
    _ctrl_pressed = False
    if hold > MAX_HOLD or _other_key_in_ctrl:
        _last_ctrl_click_t = 0.0
        return
    now = time.time()
    if now - _last_ctrl_click_t < DOUBLE_CLICK_INTERVAL:
        _last_ctrl_click_t = 0.0
        threading.Thread(target=translation_worker, daemon=True).start()
        return
    _last_ctrl_click_t = now


class PopupManager:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.popup = None
        self.result_text = None
        self.copy_btn = None
        self.speak_btn = None
        self._translation = ''
        self._mouse_listener = None
        self._poll()

    def _poll(self):
        try:
            while True:
                msg = _ui_queue.get_nowait()
                if msg[0] == 'show':
                    _, text, placeholder, x, y = msg
                    self._show(text, placeholder, x, y)
                elif msg[0] == 'update':
                    if (self.result_text and self.popup
                            and self.popup.winfo_exists()):
                        self._set_result(msg[1])
                elif msg[0] == 'cliptoggle':
                    if _clip_manager is not None:
                        _clip_manager.toggle()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _set_result(self, text: str):
        tw = self.result_text
        tw.config(state='normal')
        tw.delete('1.0', 'end')
        tw.insert('1.0', text)
        tw.update_idletasks()
        lines = tw.count('1.0', 'end', 'displaylines')
        if lines:
            tw.config(height=max(1, min(lines[0], 10)))
        tw.config(state='disabled')
        self._translation = text
        if self.copy_btn and self.popup and self.popup.winfo_exists():
            self.copy_btn.config(text='⧉')
        if self.speak_btn and self.popup and self.popup.winfo_exists():
            self.speak_btn.config(state='normal', fg='#888888')

    def _show(self, text: str, placeholder: str, x: int, y: int):
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        popup.attributes('-alpha', 0.94)
        popup.configure(bg=BG)

        frame = tk.Frame(popup, bg=BG, padx=16, pady=14)
        frame.pack(fill='both', expand=True)

        orig = text[:80] + '…' if len(text) > 80 else text
        tk.Label(frame, text=orig, bg=BG, fg=FG_ORIG,
                 font=('Segoe UI', 9), wraplength=340, justify='left'
                 ).pack(anchor='w')

        tk.Frame(frame, bg=FG_SEP, height=1).pack(fill='x', pady=(8, 8))

        result_text = tk.Text(
            frame, bg=BG, fg='#ffffff',
            font=('Microsoft YaHei UI', 11),
            width=36, height=1, wrap='word', relief='flat', borderwidth=0,
            highlightthickness=0, selectbackground=SEL_BG,
            selectforeground='#ffffff', inactiveselectbackground=SEL_BG,
            undo=False, cursor='xterm', spacing1=1, spacing3=2)
        result_text.insert('1.0', placeholder)
        result_text.config(state='disabled')
        result_text.pack(anchor='w', fill='x')
        self.result_text = result_text

        def enable_copy(e):
            if e.keysym.lower() == 'c':
                try:
                    sel = result_text.get('sel.first', 'sel.last')
                except tk.TclError:
                    sel = result_text.get('1.0', 'end-1c')
                popup.clipboard_clear()
                popup.clipboard_append(sel)
            elif e.keysym.lower() == 'a':
                result_text.tag_add('sel', '1.0', 'end')
            return 'break'
        result_text.bind('<Control-KeyPress>', enable_copy)

        btn_row = tk.Frame(frame, bg=BG)
        btn_row.pack(fill='x', pady=(6, 0))

        def do_copy():
            txt = self._translation
            if txt:
                pyperclip.copy(txt)
                copy_btn.config(text='✓')
                popup.after(1500, lambda: copy_btn.config(text='⧉')
                            if popup.winfo_exists() else None)

        copy_btn = tk.Button(
            btn_row, text='⧉', bg=BG, fg='#888888',
            activebackground=BG, activeforeground='#bbbbbb',
            relief='flat', borderwidth=0, font=('Segoe UI', 12),
            cursor='hand2', padx=2, pady=0, command=do_copy)
        copy_btn.pack(side='right')
        self.copy_btn = copy_btn

        def do_speak():
            txt = self._translation
            if txt:
                speak_btn.config(text='…', state='disabled')
                speak_text(txt, speak_btn, popup)

        speak_btn = tk.Button(
            btn_row, text='🔊', bg=BG, fg='#444444',
            activebackground=BG, activeforeground='#aaaaaa',
            relief='flat', borderwidth=0, font=('Segoe UI', 11),
            cursor='hand2', padx=2, pady=0, state='disabled',
            command=do_speak)
        speak_btn.pack(side='right', padx=(0, 6))
        self.speak_btn = speak_btn

        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        popup.geometry(f'+{min(x + 16, sw - pw - 8)}+{min(y + 16, sh - ph - 8)}')
        popup.update()

        try:
            apply_rounded_corners(
                ctypes.windll.user32.GetParent(popup.winfo_id()))
        except Exception:
            pass

        self.popup = popup
        popup.after(300, lambda: self._watch_outside_click(popup))

    def _watch_outside_click(self, popup):
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None

        if not popup.winfo_exists():
            return

        def on_click(x, y, button, pressed):
            if not pressed:
                return
            if not popup.winfo_exists():
                return False
            self.root.after(0, lambda: self._check_outside(popup))

        listener = pynmouse.Listener(on_click=on_click)
        listener.daemon = True
        listener.start()
        self._mouse_listener = listener

    def _check_outside(self, popup):
        if not popup.winfo_exists():
            return
        cx, cy = get_mouse_pos()
        px, py = popup.winfo_rootx(), popup.winfo_rooty()
        pw, ph = popup.winfo_width(), popup.winfo_height()
        if not (px <= cx <= px + pw and py <= cy <= py + ph):
            popup.destroy()
            if self._mouse_listener:
                try:
                    self._mouse_listener.stop()
                except Exception:
                    pass
                self._mouse_listener = None


# ---------------- 剪贴板历史 (Alt+V) ----------------

class ClipStore:
    """SQLite 持久化的剪贴板历史。kind: text / image / files"""

    def __init__(self, db_path=CLIP_DB):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            'CREATE TABLE IF NOT EXISTS history ('
            ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' kind TEXT NOT NULL,'
            ' payload NOT NULL,'
            ' preview TEXT,'
            ' created REAL)')
        self._db.commit()

    def add(self, kind, payload, preview):
        """新增条目（与最新条目重复则仅上浮），返回条目 id。"""
        with self._lock:
            row = self._db.execute(
                'SELECT id, payload FROM history'
                ' ORDER BY id DESC LIMIT 1').fetchone()
            if row is not None and row[1] == payload:
                self._db.execute('UPDATE history SET created=? WHERE id=?',
                                 (time.time(), row[0]))
                self._db.commit()
                return row[0]
            cur = self._db.execute(
                'INSERT INTO history (kind, payload, preview, created)'
                ' VALUES (?, ?, ?, ?)',
                (kind, payload, preview, time.time()))
            self._db.execute(
                'DELETE FROM history WHERE id NOT IN'
                ' (SELECT id FROM history ORDER BY id DESC LIMIT ?)',
                (CLIP_MAX_ITEMS,))
            self._db.commit()
            return cur.lastrowid

    def items(self, limit=CLIP_MAX_ITEMS):
        with self._lock:
            return self._db.execute(
                'SELECT id, kind, payload, preview FROM history'
                ' ORDER BY id DESC LIMIT ?', (limit,)).fetchall()

    def delete(self, item_id):
        with self._lock:
            self._db.execute('DELETE FROM history WHERE id=?', (item_id,))
            self._db.commit()

    def clear(self):
        with self._lock:
            self._db.execute('DELETE FROM history')
            self._db.commit()


def _open_clipboard(retries=8):
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.03)
    return False


def _dib_to_image(dib):
    with io.BytesIO(dib) as f:
        return Image.open(f).convert('RGB')


def _image_to_dib(img):
    """PIL Image → CF_DIB（BMP 去掉 14 字节文件头）。"""
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'BMP')
    return buf.getvalue()[14:]


def _make_hdrop(paths):
    """构造 CF_HDROP 的 DROPFILES 结构。"""
    blob = ('\0'.join(paths) + '\0\0').encode('utf-16-le') + b'\0\0'
    return struct.pack('IIIii', 20, 0, 0, 0, 1) + blob


def _read_clipboard_now():
    """读取当前剪贴板 → (kind, payload, preview) 或 None。"""
    if not _open_clipboard():
        return None
    try:
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                files = list(win32clipboard.GetClipboardData(win32con.CF_HDROP))
                if files:
                    first = os.path.basename(files[0].rstrip('\\/'))
                    if len(files) > 1:
                        preview = '📄 %s 等 %d 项' % (first, len(files))
                    else:
                        preview = '📄 ' + first
                    return ('files', '\n'.join(files), preview)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
                img = _dib_to_image(
                    win32clipboard.GetClipboardData(win32con.CF_DIB))
                buf = io.BytesIO()
                img.save(buf, 'PNG')
                png = buf.getvalue()
                if len(png) > CLIP_IMAGE_MAX_STORE:
                    return None
                return ('image', png, '🖼 %dx%d 像素' % img.size)
            if win32clipboard.IsClipboardFormatAvailable(
                    win32con.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(
                    win32con.CF_UNICODETEXT).strip()
                if not text or len(text) > CLIP_TEXT_MAX_STORE:
                    return None
                one = ' '.join(text.split())
                if len(one) > CLIP_TEXT_PREVIEW_LEN:
                    one = one[:CLIP_TEXT_PREVIEW_LEN] + '…'
                return ('text', text, one)
        except Exception:
            return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
    return None


def _write_clipboard_item(item):
    """把历史条目写回剪贴板（自动忽略自家写入）。"""
    kind, payload = item[1], item[2]
    try:
        if not _open_clipboard():
            return False
        try:
            win32clipboard.EmptyClipboard()
            if kind == 'text':
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT,
                                                payload)
            elif kind == 'image':
                img = Image.open(io.BytesIO(payload))
                win32clipboard.SetClipboardData(win32con.CF_DIB,
                                                _image_to_dib(img))
            elif kind == 'files':
                win32clipboard.SetClipboardData(
                    win32con.CF_HDROP,
                    _make_hdrop(payload.split('\n')))
            return True
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            _mark_own_clip_write()
    except Exception:
        return False


def _clipboard_listener():
    """轮询剪贴板序号，把新内容入库。"""
    last = 0
    while True:
        try:
            seq = ctypes.windll.user32.GetClipboardSequenceNumber()
            if seq != last:
                if last and seq > _clip_ignore_upto:
                    item = _read_clipboard_now()
                    if item and _clip_store is not None:
                        _clip_store.add(*item)
                last = seq
        except Exception:
            pass
        time.sleep(0.4)


def _get_foreground_window():
    try:
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return None


def _paste_to_window(hwnd):
    """把焦点交还目标窗口并模拟 Ctrl+V。"""
    global _simulating
    kb = ctypes.windll.user32.keybd_event
    VK_CONTROL, VK_MENU, VK_V, KEYUP = 17, 18, 86, 2
    try:
        if hwnd and ctypes.windll.user32.IsWindow(int(hwnd)):
            ctypes.windll.user32.SetForegroundWindow(int(hwnd))
    except Exception:
        pass
    time.sleep(0.06)
    _simulating = True
    try:
        kb(VK_MENU, 0, KEYUP, 0)
        time.sleep(0.02)
        kb(VK_CONTROL, 0, 0, 0)
        kb(VK_V, 0, 0, 0)
        kb(VK_V, 0, KEYUP, 0)
        kb(VK_CONTROL, 0, KEYUP, 0)
    finally:
        time.sleep(0.05)
        _simulating = False


class ClipHistoryManager:
    """Alt+V 呼出的剪贴板历史浮窗。

    ↑↓ 选择 · Enter 粘贴 · Shift+Enter 仅复制 · Del 删除 · Esc 关闭
    """

    def __init__(self, root, store):
        self.root = root
        self.store = store
        self.popup = None
        self.canvas = None
        self.rows = []
        self.index = 0
        self.target_hwnd = None
        self._thumbs = []
        self._row_frames = []
        self._inner = None
        self._mouse_listener = None

    def toggle(self):
        if self.popup is not None and self.popup.winfo_exists():
            self._close()
        else:
            self.show_popup()

    def show_popup(self):
        if self.popup is not None and self.popup.winfo_exists():
            return
        self.rows = self.store.items(CLIP_POPUP_ROWS)
        if not self.rows:
            return
        self.target_hwnd = _get_foreground_window()
        self.index = 0
        self._thumbs = []

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        popup.attributes('-alpha', 0.96)
        popup.configure(bg=FG_SEP)

        outer = tk.Frame(popup, bg=BG)
        outer.pack(fill='both', expand=True, padx=1, pady=1)

        tk.Label(outer, text='剪贴板历史   ↑↓ 选择 · Enter 粘贴 · '
                             'Shift+Enter 仅复制 · Del 删除 · Esc 关闭',
                 bg=BG, fg='#666666', font=('Segoe UI', 8),
                 anchor='w').pack(fill='x', padx=10, pady=(6, 2))

        body = tk.Frame(outer, bg=BG)
        body.pack(fill='both', expand=True)

        canvas = tk.Canvas(body, bg=BG, width=CLIP_POPUP_W,
                           highlightthickness=0, bd=0)
        sb = tk.Scrollbar(body, orient='vertical', command=canvas.yview,
                          width=10)
        self._inner = tk.Frame(canvas, bg=BG)
        self._inner.bind('<Configure>', lambda e: canvas.configure(
            scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=self._inner, anchor='nw',
                             width=CLIP_POPUP_W)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.canvas = canvas

        self._row_frames = []
        for row in self.rows:
            self._row_frames.append(self._build_row(row))

        popup.bind('<Escape>', lambda e: self._close())
        popup.bind('<Return>', lambda e: self._activate(True))
        popup.bind('<Shift-Return>', lambda e: self._activate(False))
        popup.bind('<Up>', lambda e: self._move(-1))
        popup.bind('<Down>', lambda e: self._move(1))
        popup.bind('<Delete>', lambda e: self._delete_current())
        canvas.bind('<MouseWheel>', self._on_wheel)

        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        mx, my = get_mouse_pos()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        popup.geometry(f'+{min(mx + 16, sw - pw - 8)}'
                       f'+{min(my + 16, sh - ph - 8)}')
        try:
            apply_rounded_corners(
                ctypes.windll.user32.GetParent(popup.winfo_id()))
        except Exception:
            pass

        self.popup = popup
        self._highlight()
        popup.focus_set()
        self._watch_outside_click(popup)

    def _build_row(self, row):
        item_id, kind, payload, preview = row
        fr = tk.Frame(self._inner, bg=BG, height=CLIP_ROW_H)
        fr.pack(fill='x', padx=4, pady=1)
        fr.pack_propagate(False)
        if kind == 'image':
            try:
                img = Image.open(io.BytesIO(payload))
                img.thumbnail((CLIP_ROW_H - 8, 140))
                ph = ImageTk.PhotoImage(img)
                self._thumbs.append(ph)
                cv = tk.Canvas(fr, bg=BG, width=ph.width(),
                               height=ph.height(), highlightthickness=0)
                cv.create_image(ph.width() // 2, ph.height() // 2, image=ph)
                cv.pack(side='left', padx=(8, 6), pady=4)
            except Exception:
                pass
            lbl = tk.Label(fr, text=preview, bg=BG, fg='#aaaaaa',
                           font=('Segoe UI', 9), anchor='w')
        else:
            lbl = tk.Label(fr, text=preview, bg=BG, fg='#cccccc',
                           font=('Segoe UI', 9), anchor='w')
        lbl.pack(side='left', fill='both', expand=True, padx=2)
        for w in (fr, lbl):
            w.bind('<Button-1>', lambda e, r=row: self._click(r))
        return fr

    def _highlight(self):
        for i, fr in enumerate(self._row_frames):
            sel = (i == self.index)
            fr.configure(bg=SEL_BG if sel else BG)
            for child in fr.winfo_children():
                try:
                    child.configure(bg=SEL_BG if sel else BG)
                except Exception:
                    pass
        if self.canvas is not None and self._row_frames:
            unit = CLIP_ROW_H + 1
            self.canvas.yview_moveto(
                max(0, self.index * unit - 88)
                / max(1, len(self._row_frames) * unit))

    def _move(self, d):
        if not self.rows:
            return
        self.index = max(0, min(len(self.rows) - 1, self.index + d))
        self._highlight()

    def _click(self, row):
        try:
            self.index = [r[0] for r in self.rows].index(row[0])
        except ValueError:
            return
        self._highlight()
        self._activate(True)

    def _activate(self, paste):
        if 0 <= self.index < len(self.rows):
            row = self.rows[self.index]
            self._close()
            threading.Thread(target=self._do_paste, args=(row, paste),
                             daemon=True).start()

    def _delete_current(self):
        if 0 <= self.index < len(self.rows):
            self.store.delete(self.rows[self.index][0])
            self._close()
            self.show_popup()

    def _do_paste(self, row, paste):
        if not _write_clipboard_item(row):
            return
        if paste and self.target_hwnd:
            _paste_to_window(self.target_hwnd)

    def _on_wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-e.delta / 120), 'units')
        except Exception:
            pass

    def _close(self):
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None
        try:
            if self.popup is not None and self.popup.winfo_exists():
                self.popup.destroy()
        except Exception:
            pass
        self.popup = None

    def _watch_outside_click(self, popup):
        def on_click(x, y, button, pressed):
            if not pressed:
                return
            if not popup.winfo_exists():
                return False
            self.root.after(0, lambda: self._check_outside(popup, x, y))
        listener = pynmouse.Listener(on_click=on_click)
        listener.daemon = True
        listener.start()
        self._mouse_listener = listener

    def _check_outside(self, popup, cx, cy):
        if not popup.winfo_exists():
            return
        px, py = popup.winfo_rootx(), popup.winfo_rooty()
        pw, ph = popup.winfo_width(), popup.winfo_height()
        if not (px <= cx <= px + pw and py <= cy <= py + ph):
            self._close()


def main():
    root = tk.Tk()
    root.withdraw()

    is_first_run = not os.path.exists(CONFIG_FILE)

    global API_KEY
    API_KEY = load_api_key()
    while not API_KEY:
        key = show_key_dialog(root)
        if key is None:
            root.destroy()
            return
        save_api_key(key)
        API_KEY = key

    if is_first_run and getattr(sys, 'frozen', False):
        set_startup(True)

    global _clip_store, _clip_manager
    try:
        _clip_store = ClipStore()
        _clip_manager = ClipHistoryManager(root, _clip_store)
        threading.Thread(target=_clipboard_listener, daemon=True).start()
    except Exception:
        _clip_store = None
        _clip_manager = None

    pynkb.Listener(on_press=on_key_press, on_release=on_key_release,
                   daemon=True).start()
    setup_tray(root)
    PopupManager(root)
    root.mainloop()


if __name__ == '__main__':
    main()
