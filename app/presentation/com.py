"""只连接运行中的演示软件，并从文稿注册补查其他实例。"""

from pathlib import Path
import threading


_com_state = threading.local()


def ensure_com() -> bool:
    """在当前线程初始化 COM，成功或已初始化时返回 True。"""
    if getattr(_com_state, 'ready', False):
        return True
    try:
        import pythoncom
        try:
            pythoncom.CoInitialize()
        except Exception:
            # Qt 可能已经初始化过，这种情况直接继续。
            pass
        _com_state.ready = True
    except ImportError:
        return False
    return True


def application_pid(application):
    """获取演示软件所属进程；实例不提供窗口句柄时返回 None。"""
    try:
        import win32process
        return win32process.GetWindowThreadProcessId(int(application.HWND))[1]
    except Exception:
        return None


PROG_IDS = (
    ('PowerPoint.Application', 'PowerPoint'),
    ('Kwpp.Application', 'WPS 演示'),
)

# WPS 的 Application.Name 与接口 IID 也会返回 PowerPoint 的兼容值；
# 用类型库标识区分软件，不依赖界面语言或窗口标题。
_TYPELIB_PROVIDERS = {
    '{91493440-5A91-11CF-8700-00AA0060263B}': PROG_IDS[0],
    '{44720440-94BF-4940-926D-4F38FECF2A48}': PROG_IDS[1],
}


def _identity(application):
    import pythoncom
    try:
        return application._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
    except Exception:
        return id(application)


def _rot_applications():
    """仅读取 ROT 中现存的文稿对象；不绑定文件，也不启动程序。"""
    import pythoncom
    import win32com.client
    try:
        rot = pythoncom.GetRunningObjectTable()
        context = pythoncom.CreateBindCtx(0)
        monikers = rot.EnumRunning()
    except Exception:
        return
    for moniker in monikers:
        try:
            if moniker.IsSystemMoniker() != pythoncom.MKSYS_FILEMONIKER:
                continue
            name = moniker.GetDisplayName(context, None)
            if Path(name).suffix.lower() not in ('.ppt', '.pptx'):
                continue
            document = win32com.client.dynamic.Dispatch(
                rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
            if Path(str(document.FullName)).resolve() != Path(name).resolve():
                continue
            application = document.Application
            library = application._oleobj_.GetTypeInfo().GetContainingTypeLib()[0]
            provider = _TYPELIB_PROVIDERS.get(str(library.GetLibAttr()[0]).upper())
            if provider is None:
                continue
            # 注册失效、关闭中的文稿或非演示对象不进入候选列表。
            int(application.Presentations.Count)
            int(application.SlideShowWindows.Count)
            yield provider[0], provider[1], application
        except Exception:
            continue


def running_applications():
    """返回 (ProgID, 显示名, 实例)，按 COM 身份去重；调用线程需初始化 COM。"""
    import win32com.client
    seen = []
    for prog_id, label in PROG_IDS:
        try:
            application = win32com.client.GetActiveObject(prog_id)
            identity = _identity(application)
            if any(identity == item for item in seen):
                continue
            seen.append(identity)
            yield prog_id, label, application
        except Exception:
            continue
    for prog_id, label, application in _rot_applications():
        identity = _identity(application)
        if any(identity == item for item in seen):
            continue
        seen.append(identity)
        yield prog_id, label, application
