"""从 COM 类型信息读取窗口 HWND，不生成整套 Office 类型库包装代码。"""

import ctypes


def slideshow_hwnd(prog_id, index):
    """调用类型库中声明的 HWND getter；不支持该接口时返回 0。"""
    try:
        import comtypes.client
        from comtypes import HRESULT, IUnknown
        from comtypes.automation import IDispatch, VT_HRESULT, VT_I4, VT_INT, VT_PTR
        from comtypes.typeinfo import INVOKE_PROPERTYGET, TKIND_INTERFACE

        application = comtypes.client.GetActiveObject(prog_id, dynamic=True)
        dispatch = application.SlideShowWindows.Item(index)._comobj
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
