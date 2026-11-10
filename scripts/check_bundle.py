"""在独立目录构建发布程序，验证冻结后的依赖、资源与媒体功能。"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_startup(bundle, work):
    """启动正式入口，只关闭本次创建的主窗口并检查退出码。"""
    settings = bundle / 'config' / 'settings.json'
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                'app': {
                    'auto_check_updates': False,
                    'resume_last_session': False,
                    'onboarding_seen': True,
                    'onboarding_tools_seen': True,
                }
            }
        ),
        encoding='utf-8',
    )
    env = dict(os.environ)
    env.pop('QT_QPA_PLATFORM', None)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    process = subprocess.Popen([str(bundle / 'PowerPointReviewer.exe')], cwd=work, env=env)
    try:
        windows = []

        @callback_type
        def collect(hwnd, _parameter):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid and user32.IsWindowVisible(hwnd):
                windows.append(hwnd)
            return True

        deadline = time.monotonic() + 30
        while not windows and process.poll() is None and time.monotonic() < deadline:
            user32.EnumWindows(collect, 0)
            time.sleep(0.1)
        assert windows, f'正式程序未显示窗口，退出码：{process.poll()}'
        time.sleep(3)
        assert user32.PostMessageW(windows[0], 0x0010, 0, 0)
        assert process.wait(timeout=20) == 0, '正式程序退出异常'
        print('正式程序启动与关闭检查通过。', flush=True)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


class SpeechService(BaseHTTPRequestHandler):
    """提供 SDK 测试端点，不使用真实账号或访问云服务。"""

    def log_message(self, *_args):
        pass

    def respond(self, payload, status=200, content_type='application/json'):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/audio.wav':
            self.respond(self.server.audio, content_type='audio/wav')
        elif self.path.startswith('/api/v1/uploads?'):
            self.respond(
                {
                    'output': {
                        'oss_access_key_id': 'test-key',
                        'signature': 'test-signature',
                        'policy': 'test-policy',
                        'upload_dir': 'test',
                        'x_oss_object_acl': 'private',
                        'x_oss_forbid_overwrite': 'true',
                        'upload_host': self.server.base_url + '/upload',
                    }
                }
            )
        else:
            self.respond({'message': 'Unknown test endpoint'}, 404)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.requests.append((self.path, dict(self.headers), data))
        if self.path == '/upload':
            self.respond(b'')
        elif self.headers.get('Authorization') in (
            'Bearer rejected-key',
            'Bearer unknown-error-key',
        ):
            code = (
                'InvalidApiKey'
                if self.headers['Authorization'] == 'Bearer rejected-key'
                else 'TestServiceError'
            )
            self.respond(
                {'code': code, 'message': 'test-service-error', 'request_id': 'test-request'}, 401
            )
        else:
            self.respond(
                {
                    'output': {'audio': {'url': self.server.base_url + '/audio.wav'}},
                    'request_id': 'test-request',
                    'usage': {'characters': 4},
                }
            )


def main():
    temp_root = ROOT / 'temp'
    temp_root.mkdir(exist_ok=True)
    # 与发布 spec 共用同一 Analysis 和 PYZ，检查入口不参与依赖分析，避免补齐漏包。
    with tempfile.TemporaryDirectory(prefix='bundle-check-', dir=temp_root) as folder:
        work = Path(folder)
        entry = work / 'bundle_entry.py'
        entry.write_text(
            'import sys\nfrom pathlib import Path\n'
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "sys.stderr.reconfigure(encoding='utf-8')\n"
            'source = Path(sys.argv[1])\n'
            "exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), "
            "{'__name__': '__main__', '__file__': str(source)})\n",
            encoding='utf-8',
        )
        spec = work / 'bundle_check.spec'
        spec.write_text(
            'from pathlib import Path\nfrom PyInstaller.config import CONF\n'
            f"CONF['specpath'] = {str(ROOT)!r}\n"
            f"CONF['spec'] = {str(ROOT / 'PowerPointReviewer.spec')!r}\n"
            f'exec(compile(Path({str(ROOT / "PowerPointReviewer.spec")!r}).'
            "read_text(encoding='utf-8'), 'PowerPointReviewer.spec', 'exec'))\n"
            'from shutil import copy2\n'
            f'scripts = [item for item in a.scripts if Path(item[1]).resolve() != '
            f'Path({str(ROOT / "main.py")!r})]\n'
            f"scripts.append(('bundle_entry', {str(entry)!r}, 'PYSOURCE'))\n"
            "check = EXE(pyz, scripts, [], exclude_binaries=True, name='BundleCheck', "
            'console=True, upx=False)\n'
            "copy2(check.name, Path(coll.name) / 'BundleCheck.exe')\n",
            encoding='utf-8',
        )
        log = work / 'build.log'
        print('构建隔离的发布程序……', flush=True)
        with log.open('w', encoding='utf-8') as output:
            result = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'PyInstaller',
                    str(spec),
                    '--noconfirm',
                    '--distpath',
                    str(work / 'dist'),
                    '--workpath',
                    str(work / 'build'),
                ],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        if result.returncode:
            print(log.read_text(encoding='utf-8')[-16000:])
            return result.returncode

        audio = BytesIO()
        with wave.open(audio, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(24000)
            writer.writeframes(b'\0\0' * 12000)
        with ThreadingHTTPServer(('127.0.0.1', 0), SpeechService) as server:
            server.audio = audio.getvalue()
            server.base_url = f'http://127.0.0.1:{server.server_port}'
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {
                    **os.environ,
                    'QT_QPA_PLATFORM': 'offscreen',
                    'PYTHONIOENCODING': 'utf-8',
                    'NO_PROXY': '127.0.0.1,localhost',
                }
                bundle = work / 'dist' / 'PowerPointReviewer'
                result = subprocess.run(
                    [
                        str(bundle / 'BundleCheck.exe'),
                        str(ROOT / 'tests' / 'bundle_smoke.py'),
                        str(work),
                        server.base_url,
                    ],
                    cwd=work,
                    env=env,
                    timeout=90,
                )
                if result.returncode:
                    return result.returncode
                paths = [item[0] for item in server.requests]
                assert '/upload' in paths, '参考音频未经过真实 HTTP 上传'
                assert any('multimodal-generation' in path for path in paths), paths
                check_startup(bundle, work)
                print('发布包检查通过。', flush=True)
            finally:
                server.shutdown()
                thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
