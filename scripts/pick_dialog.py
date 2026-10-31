"""独立文件选择对话框进程：
在隔离的子进程中运行 Tkinter 界面，防止第三方输入法或 Windows 对话框钩子（如 wetype_tip.dll）
在卸载或窗口销毁时引发 0xC0000005 访问冲突带崩后台 Uvicorn 服务。
"""
import json
import sys
import tkinter as tk
from tkinter import filedialog

MEDIA_PATTERNS = "*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.m4a *.flac *.aac *.ogg *.wma"


def main():
    multiple = "--multiple" in sys.argv
    try:
        root = tk.Tk()
    except Exception as exc:
        print(json.dumps({"error": f"无法初始化图形界面: {exc}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        types = [("音视频文件", MEDIA_PATTERNS), ("所有文件", "*.*")]
        if multiple:
            paths = list(filedialog.askopenfilenames(parent=root, title="选择要导入的音视频", filetypes=types))
            result = {"paths": paths, "path": paths[0] if paths else ""}
        else:
            path = filedialog.askopenfilename(parent=root, title="选择要导入的音视频", filetypes=types)
            result = {"path": path, "paths": [path] if path else []}
        print(json.dumps(result, ensure_ascii=False))
        sys.stdout.flush()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.stdout.flush()
        sys.exit(1)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
