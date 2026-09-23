# Translator — 双击 Ctrl 的划词翻译工具

选中任意文字后**快速按两次 Ctrl**，即可在鼠标附近弹出翻译结果，支持一键复制与语音朗读。中英互译，基于智谱 GLM 大模型。

## 功能特性

- **双击 Ctrl 划词翻译**：自动抓取选中文本，中英自动识别、互相翻译
- **智谱 GLM 驱动**：调用 `glm-4-flash` 模型，翻译自然流畅
- **轻量浮窗**：深色半透明卡片跟随鼠标弹出，高度自适应，点外部自动关闭
- **一键复制** ⧉：翻译结果直接进剪贴板
- **语音朗读** 🔊：中文（晓晓）/ 英文（Jenny）真人音色 TTS
- **系统托盘**：修改 API Key、开机自启开关、退出
- **首次运行引导**：自动弹出 API Key 配置窗口

## 环境要求

- Windows 10 / 11
- 智谱 AI API Key（免费申请：[bigmodel.cn](https://open.bigmodel.cn) → API Keys）

## 快速开始

**方式一：直接使用** — 下载 Release 中的 `translator.exe`，双击运行，首次启动按提示填入 API Key。

**方式二：源码运行**

```bat
pip install -r requirements.txt
python translator.py
```

## 打包 exe

```bat
build.bat
```

（等价于 `pyinstaller --onefile --noconsole --name translator translator.py`）

## 配置说明

| 配置项 | 位置 | 说明 |
|---|---|---|
| API Key | 程序同目录 `translator_config.ini` | 托盘菜单「修改 API Key」可随时更改 |
| 开机自启 | 注册表 `HKCU\...\CurrentVersion\Run` | 托盘菜单「开机自启」勾选切换 |

## 使用提示

- 双击 Ctrl 的间隔需在 0.4 秒内；按住 Ctrl 不放或其他按键混入不会触发
- 弹窗内支持 `Ctrl+C` 复制选中内容、`Ctrl+A` 全选
- 翻译请求超时 30 秒，出错时结果区会显示错误信息
