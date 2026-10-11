# Stride VR 本地化工具链

**给 Xenko/Stride 引擎 VR 应用做本地化的全套工具。**

最初用于把 Virtual Desktop（PICO 4 头显端）本地化成中文：187 条 UI 文本 +
从零重建的字体图集。整个逆向过程、所有踩过的坑都写在这里，其他人可以照着
做自己的语言、自己的应用，或者两者都做。

```
PICO 4（Android 10，已 root）
  Virtual Desktop 1.34.22.0 —— Stride (Xenko) 3.2.0.2，.NET NativeAOT
  两个 .bundle 文件里 2120 个序列化对象 · 8 个 UI 页面对象 · 8 个字体
```

[完整文档](docs/index.md) · [踩坑清单](docs/07-pitfalls.md) ·
[完整复盘](docs/10-case-study.md) · [排障](docs/11-troubleshooting.md) ·
[添加新语言](docs/08-new-language.md) · [测试](tests/smoke_test.py)

---

## 这个工具做什么

重写 APK 里的两类对象：

| | 原始 | 中文版 |
|---|---|---|
| 8 个 `UIPage` 对象里的 UI 字符串 | 英文 | 187 条已翻译 |
| 3 个字体对象 | 224 字形，图集 256×212 | 621 字形，图集 256×1235 |

字体的做法是：用你提供的 TTF 光栅化新字形，码放到**原图集下方**的新区域。
原有字形一个字节都不动，所以原来能正常显示的内容不可能被弄坏。

## 让这一切成立的三个关键发现

**1. APK 里有两个 bundle，而引擎读的是那个小的。**

```
default.b65fc36561f156d9d380862e7304cc55.bundle   1 GB    数据
default.bundle                                    224 KB  索引
```

那个 224 KB 的索引文件里，存着**第二份完整的 2120 条对象记录表**。引擎就是
通过它来定位对象的。只改大数据 bundle，应用会在启动 15 秒后以字体系统
`NullReferenceException` + `SIGSEGV` 崩溃。本项目历史上每一次失败构建，
最后都能追溯到这一个事实。`bundle_writer.py` 会同步两份记录表，并且在动手
前先断言两份表一致。

**2. UIPage 字符串的长度可以随便改，但匹配必须精确。**

把整个 `varint(长度) + 文本` 模式作为整体替换，长度想改多少改多少。但如果
直接 `bytes.replace("Environment", "环境")`，你会同时改掉
`itemEnvironment`、`btnSettings` 和
`UI/ControlPanel/EnvironmentSheet` —— 控件查找失败，进程崩溃。
（本项目早期曾得出"必须等长替换"的结论，那是错的；文档里保留了推翻它的
完整证据链。）

`--mode strict-field` 额外提供同样带长度锚点的第二遍扫描，用于处理字段内部
带填充空格的标签；在本项目参考数据上实测与 strict 命中数完全相同（40 条），
因为那里的残留全部是控件名子串。

**3. 字体字典条目是变长的，count 的偏移也不固定。**

键是 `UTF-8(码位) + 0x00`，所以固定 38 字节步进一过 U+007F 就会错位。解析
器改成锚定空格字形自同步走位。

完整说明：[bundle 格式](docs/02-bundle-format.md)、
[字体格式](docs/03-font-format.md)、
[字符串替换](docs/05-string-patching.md)，以及
[踩坑清单](docs/07-pitfalls.md) —— 如果你要手工改任何二进制格式，先读它。

## 快速开始

```bash
pip install -r requirements.txt

cp config.example.json config.json          # 按你的设备/应用版本修改
mkdir -p work fonts
cp /path/to/base.apk work/base.apk          # 你自己持有的应用副本
cp /path/to/NotoSans*.ttf fonts/            # 任何你有权使用的字体

python tools/extract.py                     # APK -> work/*.bundle
python tools/probe.py                       # 勘查：字体与 UI 页面记录号

python tools/dump_strings.py --out work/strings.tsv    # 导出英文文本
# ……翻译，另存为 locales/<你的语言>/strings.tsv……

python tools/build.py --tag my1
python tools/apksurgery.py --src work/base.apk \
    --new-bundle work/my1-data.bundle  --entry <数据条目>  --dst work/my1.apk
python tools/apksurgery.py --src work/my1.apk \
    --new-bundle work/my1-index.bundle --entry <索引条目> --dst work/my1-final.apk
tools/devtest.sh work/my1-final.apk my1
```

`apksurgery.py` 必须跑两遍 —— 先数据 bundle，后索引 bundle。原因见
[APK 打包](docs/09-apk-packaging.md)。

没有设备也能验证：`python tests/smoke_test.py` 用合成 bundle 跑 9 个测试，
覆盖双记录表重写，以及一个专门演示"不安全替换模式如何毁掉控件名"的测试。

## 目录结构

```
tools/
  stride_bundle.py     bundle 容器：头部、记录表、LZ4 分块流
  stride_font.py       OfflineRasterizedSpriteFont + 图集读取
  bundle_writer.py     重写两个 bundle，保持两份表一致
  atlas_repack.py      光栅化 + 货架 packing，新字形码到图集下方
  string_patcher.py    精确 varint 替换（以及不安全的模式，留作研究）
  build.py             完整管线
  extract.py           APK -> bundle
  probe.py             勘查全部对象记录及其类型
  dump_strings.py      导出可翻译字符串为 TSV
  apksurgery.py        替换 APK 内的 bundle，签名不动
  devtest.sh           装机并验证存活
locales/
  zh-CN/               参考翻译（187 条）
  _template/           新语言起点
docs/                  12 篇逆向笔记与操作文档，含测试矩阵
tests/                 合成数据测试
```

## 添加一种语言

你只需要一个 TSV 文件和一个字体配置。见
[docs/08-new-language.md](docs/08-new-language.md)。

西里尔、希腊、拉丁扩展字符用 Noto 开箱即用。阿拉伯语和希伯来语需要预整形
的位图（引擎不做 RTL shaping）。泰文和印度系脚本需要真正的整形步骤 ——
逐码位光栅化渲染不出正确文本。

## 适用范围与限制

* 实测于 Virtual Desktop 1.34.22.0 / PICO 4 / Android 10。应用版本之间记录
  号会变 —— `tools/probe.py` 帮你找。
* 假设引擎使用同一套 Stride/Xenko 序列化格式。其他引擎不在范围内。
* 只覆盖 UI 文本。应用内渲染的 3D 文字、PC 端服务端程序不在范围内。

## 法律声明

本仓库**不包含任何专有材料**：没有 APK、没有 bundle、没有游戏资产、没有
商业字体。这里只有工具和文档。应用副本和字体由你自己提供，你也只修改你
自己的设备。

修改应用可能违反其服务条款，在某些司法辖区可能违法。你为自己的行为负责。

WTFPL v2 —— 想干嘛干嘛。见 [LICENSE](LICENSE)。
