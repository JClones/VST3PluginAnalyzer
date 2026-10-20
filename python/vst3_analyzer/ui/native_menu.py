# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Optional macOS menu correction for the system-generated Hide shortcut."""
import sys


def clear_native_hide_shortcut():
    """Clear Cmd+H from the native Hide item; return whether a shortcut was removed.

    Qt exposes no action for this automatically generated item. Other platforms and
    non-native Qt menus require no adjustment. Call only from the GUI thread.
    """
    if sys.platform != "darwin":
        return False
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "cocoa":
        return False
    # Use only the system Objective-C runtime; no additional dependency or helper executable is needed.
    import ctypes
    runtime = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    runtime.objc_getClass.argtypes = [ctypes.c_char_p]
    runtime.objc_getClass.restype = ctypes.c_void_p
    runtime.sel_registerName.argtypes = [ctypes.c_char_p]
    runtime.sel_registerName.restype = ctypes.c_void_p
    address = ctypes.cast(runtime.objc_msgSend, ctypes.c_void_p).value

    def send(receiver, selector, result=ctypes.c_void_p, arguments=(), values=()):
        """Invoke a public Cocoa accessor with an explicitly typed ABI.

        Args:
            receiver: Borrowed Objective-C object pointer.
            selector: Public Cocoa method name.
            result: ctypes return type, or None for void.
            arguments: ctypes types for the method's explicit arguments.
            values: Values corresponding to those argument types.
        """
        signature = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p, *arguments)
        return signature(address)(receiver, runtime.sel_registerName(selector.encode()), *values)

    # Locate Hide by its selector rather than the translated application/menu title.
    application = send(runtime.objc_getClass(b"NSApplication"), "sharedApplication")
    menu = send(application, "mainMenu")
    if not menu or send(menu, "numberOfItems", ctypes.c_long) == 0:
        return False
    item = send(menu, "itemAtIndex:", arguments=(ctypes.c_long,), values=(0,))
    application_menu = send(item, "submenu")
    if not application_menu:
        return False
    hide = runtime.sel_registerName(b"hide:")
    for index in range(send(application_menu, "numberOfItems", ctypes.c_long)):
        item = send(application_menu, "itemAtIndex:", arguments=(ctypes.c_long,), values=(index,))
        if send(item, "action") != hide:
            continue
        key = send(item, "keyEquivalent")
        if not key or send(key, "length", ctypes.c_ulong) == 0:
            return False
        # Retain the Hide menu command but free its key equivalent for Harmonic Response.
        empty = send(runtime.objc_getClass(b"NSString"), "string")
        send(item, "setKeyEquivalent:", result=None, arguments=(ctypes.c_void_p,), values=(empty,))
        return True
    return False
