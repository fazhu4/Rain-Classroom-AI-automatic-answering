# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置
构建命令：pyinstaller auto_exam_solver.spec
输出文件：dist/AutoExamSolver.exe
"""

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Playwright CDP 模式需要的内部模块，PyInstaller 可能检测不到
        'playwright.async_api',
        'playwright._impl._api_types',
        'playwright._impl._connection',
        'playwright._impl._browser',
        'playwright._impl._browser_context',
        'playwright._impl._page',
        'playwright._impl._frame',
        'playwright._impl._element_handle',
        'playwright._impl._js_handle',
        'playwright._impl._object_factory',
        'playwright._impl._helper',
        'playwright._impl._transport',
        # PyYAML C 扩展
        'yaml',
        '_yaml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除可能被自动检测的重型包（当前项目不使用）
        'matplotlib', 'numpy', 'scipy', 'pandas', 'PIL',
        'cv2', 'tensorflow', 'torch', 'sympy',
        'tkinter', 'PyQt5', 'PySide2',
        # CDP 模式下不需要浏览器二进制文件
        'playwright.driver',
    ],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AutoExamSolver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 控制台模式：程序使用 print/input 交互
    disable_windowed_traceback=False,
)
