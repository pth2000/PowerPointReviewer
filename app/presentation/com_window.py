"""从 COM 类型信息读取窗口 HWND，不生成整套 Office 类型库包装代码。"""

import ctypes


def _dispatch_from_window(window):
    """通过标准 COM 封送连接两套 Python 包，始终使用传入的放映对象。"""
    import pythoncom
    from comtypes import GUID, IUnknown
    from comtypes.automation import IDispatch

    stream = pythoncom.CreateStreamOnHGlobal()
    pythoncom.CoMarshalInterface(stream, pythoncom.IID_IDispatch, window._oleobj_,
                                 pythoncom.MSHCTX_INPROC, pythoncom.MSHLFLAGS_NORMAL)
    unmarshalled = False
    try:
        stream.Seek(0, 0)
        data = stream.Read(stream.Stat()[2])
        create = ctypes.WinDLL('shlwapi').SHCreateMemStream
        create.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        create.restype = ctypes.POINTER(IUnknown)
        memory = ctypes.create_string_buffer(data)
        native_stream = create(memory, len(data))
        if not native_stream:
            raise MemoryError('Cannot create COM stream')
        unmarshal = ctypes.OleDLL('ole32').CoUnmarshalInterface
        unmarshal.argtypes = [ctypes.POINTER(IUnknown), ctypes.POINTER(GUID),
                              ctypes.POINTER(ctypes.POINTER(IDispatch))]
        unmarshal.restype = ctypes.HRESULT
        dispatch = ctypes.POINTER(IDispatch)()
        unmarshal(native_stream, ctypes.byref(IDispatch._iid_), ctypes.byref(dispatch))
        unmarshalled = True
        return dispatch
    finally:
        if not unmarshalled:
            stream.Seek(0, 0)
            pythoncom.CoReleaseMarshalData(stream)


def slideshow_hwnd(window):
    """调用类型库中声明的 HWND getter；不支持该接口时返回 0。"""
    try:
        from comtypes import HRESULT, IUnknown
        from comtypes.automation import IDispatch, VT_HRESULT, VT_I4, VT_INT, VT_PTR
        from comtypes.typeinfo import INVOKE_PROPERTYGET, TKIND_INTERFACE

        dispatch = _dispatch_from_window(window)
        info = dispatch.GetTypeInfo(0)
        if info.GetTypeAttr().typekind != TKIND_INTERFACE:
            info = info.GetRefTypeInfo(info.GetRefTypeOfImplType(-1))
        attributes = info.GetTypeAttr()
        if attributes.typekind != TKIND_INTERFACE:
            return 0
        base = info.GetRefTypeInfo(info.GetRefTypeOfImplType(0))
        if base.GetTypeAttr().guid != IDispatch._iid_:
            return 0
        descriptors = [info.GetFuncDesc(position) for position in range(attributes.cFuncs)]
        if not descriptors:
            return 0
        # IDispatch has seven slots. The first declared method follows them.
        # Read the layout from ITypeInfo: Office may widen offsets for this client
        # while its containing typelib still reports SYS_WIN32; WPS keeps 32-bit offsets.
        first_offset = min(descriptor.oVft for descriptor in descriptors)
        if first_offset not in (7 * 4, 7 * 8):
            return 0
        pointer_size = first_offset // 7
        for descriptor in descriptors:
            if (info.GetDocumentation(descriptor.memid)[0].casefold() != 'hwnd'
                    or descriptor.invkind != INVOKE_PROPERTYGET):
                continue
            # Validate the signature before accessing the vtable. Slot offsets come
            # from this installed software's type library, never a fixed Office version.
            if (descriptor.cParams != 1 or descriptor.elemdescFunc.tdesc.vt != VT_HRESULT
                    or descriptor.oVft < 3 * pointer_size or descriptor.oVft % pointer_size
                    or descriptor.oVft + pointer_size > attributes.cbSizeVft):
                return 0
            parameter = descriptor.lprgelemdescParam[0]
            if (parameter.tdesc.vt != VT_PTR or not parameter.tdesc.lptdesc
                    or parameter.tdesc.lptdesc.contents.vt not in (VT_I4, VT_INT)
                    or parameter._.paramdesc.wParamFlags & 10 != 10):  # [out, retval]
                return 0
            native = dispatch.QueryInterface(IUnknown, attributes.guid)
            table = ctypes.cast(native, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            address = table[descriptor.oVft // pointer_size]
            if not address:
                return 0
            getter = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long))(address)
            hwnd = ctypes.c_long()
            if getter(native, ctypes.byref(hwnd)) < 0:
                return 0
            # Office declares HWND as a 32-bit signed long, including in 64-bit Office.
            return hwnd.value & 0xffffffff
        return 0
    except Exception:
        # Unsupported type descriptions and RPC failures leave manual binding available.
        return 0
