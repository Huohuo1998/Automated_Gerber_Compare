# Automated Gerber Compare

基于 **Python + Tkinter + Valor NPI RCOM** 的独立桌面应用。选择两份 Gerber ZIP，递归识别和校验全部层，人工确认后驱动本机 Valor 完成逐层图形比较，生成带截图、逐层判定及执行证据的可导出报告。

## 快速启动（Windows）

运行环境：Python **3.11 或更高版本**，安装时保留 Tcl/Tk；本机安装并授权 Valor NPI。应用运行时使用 Python 标准库，无第三方 Python 运行依赖。

1. 克隆仓库后双击 `launch.bat`，或运行 `python run_desktop.py`。
2. 在正确的 Valor 环境中启动专用的 `get -a11000` 会话。RCOM 仅连接本机，端口可以修改。应用继承启动它的环境变量，不配置或代替 Valor 的许可证服务。
3. 在“Valor 配置”中选择实际 `rcom.exe`，填写已有数据库名称（默认 `eda_server_db`）及对应端口。
4. 选择 A / B 两个 ZIP 和工作输出目录，点击“校验资料”。查看层配对、校验信息和完整文件清单。
5. **存在错误时无法开始**；警告允许继续。校验通过后点击“确认并开始对比”，再次确认。
6. 完成后“打开报告”或“另存报告”：可保存独立 HTML，或含日志和图片的 ZIP 证据包。

如机器依赖 Valor 启动脚本设置 `VALOR_DIR` / `VALOR_EDIR` / 许可证等环境变量，请先进入已配置的命令环境再运行 `launch.bat`。运行期间不要在该专用 Valor 会话中进行人工编辑或执行其他自动化工具。

## 覆盖范围与校验规则

- 扫描 ZIP 的所有目录层级，保存每个文件的原始相对路径、分类、大小及 SHA-256。
- 支持 Xpedition 常见 `.gdo`：顶层、底层、内层、阻焊、钢网、丝印、板外形、钻孔图，以及其他带完整文件头的 Gerber。支持常见 `.gbr/.ger/.art/.pho/.gtl/.gbl/...`，也通过文件头发现其他扩展名中的 RS-274X 数据。
- 将 `.ncd/.drl/.xln/.exc` 钻孔数据纳入比较；电镀和非电镀通孔分开识别，避免 `NonPlated` 被 `Plated` 规则覆盖。
- 已知层按明确的层用途唯一配对；其他 Gerber 按不区分大小写的完整文件名配对，并给出警告。**不使用模糊匹配或按文件顺序凑对。**
- 任一侧缺层、同一用途多文件、空数据、无法确定的导入格式等均阻止执行；没有 IPC 文件不影响图形比较。PDF、DWG、IPC 等非 Gerber 文件保留在清单中，不要求配对。
- Gerber 从 `%FS` / `%MO` 读取单位、精度、零抑制和绝对/增量坐标。两侧精度可不同，各自导入；不统一假设 `2.4`。
- 钻孔支持附件的 Xpedition `; Format : 2.4 / Absolute / Inch / Trailing` 声明，以及带单位声明的显式小数格式。隐式小数且缺少明确格式声明时会报错，请重新导出，避免猜错比例。
- 当前接收 ZIP；不自动展开内嵌 ZIP/7z/RAR，会提示警告。ZIP 条目最多 10,000、单文件最多 256 MiB、解压总量最多 2 GiB。拒绝加密、重复路径、路径穿越和特殊链接。

## 执行和判定口径

每次生成唯一 Job，在同一 Step `1` 导入 `.A` / `.B` 层。层名会清理不合法字符、截断并附带稳定短哈希，保留 `.A/.B/.Result` 后缀；Valor 在内部将名称转为小写属于正常行为。

1. 校验完整工作快照、全部静态参数和命令计划，测试 RCOM 连接。
2. 创建 Job / Step，集中设置并执行导入，逐项检查 `giTSTATUS` 和 `giTLAYER`。
3. 打开本次编辑器并选择返回的组号，设置 inch 工作单位，对**全部**配对执行 `compare_layers`。
4. 集中处理全部 Result 层：只使当前 Result 成为受影响层，删除 `s{map_resolution_mils}` 网格线，保留原始 A / B。
5. 逐对显示 Result/A/B 到槽 3/1/2，分别获取两侧 LIMITS，取两层包围框并外扩 `0.2 inch`，使用实际返回的 popview ID 截图。
6. 保存本次 Job、关闭本次窗口，生成报告。Job 留在 Valor 数据库供复查，应用不会删除已有 Job。

默认容差 **1 mil**，差异网格 **100 mil**，截图 **2000 × 2000**。`compare_layers` 返回的是**有差异的网格数量**，不是缺陷数量：0 表示该容差下图形一致，正数表示有差异。保持源文件坐标，不自动平移、旋转或镜像对齐。

截图使用本机 Valor **User 颜色模式**；槽 1/2/3 分别对应 A/B/Result，具体颜色取决于现场配置，报告不会虚构固定颜色。无法取得导入状态、有效计数、边界或截图时，总体结果为失败；未完成层不会被判成一致。

## 输出文件

默认工作目录：用户 `Documents/AutomatedGerberCompare/<时间_随机ID>/`，界面可修改。

| 文件 | 内容 |
| --- | --- |
| `validation.json` | 两份输入包及 SHA-256、完整文件清单、导入参数、唯一配对和校验问题 |
| `command_plan.json` | 执行前完整命令计划；动态窗口 ID / 边界以 `${...}` 标记 |
| `commands.txt` | 实际发送的命令，按真实执行顺序记录 |
| `rcom.jsonl` | 每条命令的 argv、退出码、stdout、stderr、耗时和错误 |
| `import.log` / `bounds_*.txt` | Valor 原始导入报告和每层边界 |
| `images/` | 原始叠加截图 |
| `result.json` | 总体和逐层状态、网格数、错误、运行参数、Job 名称 |
| `report.html` | 图片已内嵌的独立离线 HTML；可使用浏览器打印为 PDF |
| `report_bundle.zip` | HTML、JSON、命令、日志、边界和截图；不包含原始 Gerber 数据 |

输出工作目录不能包含英文逗号、换行或控制字符，因为 LMC 使用逗号分隔参数；输入 ZIP 内的文件名会映射为受控工作文件名，不受此限制。中文与空格路径通过参数列表传给 `rcom`，不经 Shell 拼接；具体 Valor 安装环境的编码兼容性仍应现场确认。

取消或命令超时时会停止 RCOM 客户端。由于客户端退出不能保证 Valor 服务端已停止执行，这种情况下不再发送后续清理命令，并在报告中提示检查现场会话。正常错误会尽量保存本次 Job 和关闭本次会话。应用通过端口锁防止**本程序的多个任务**同时占用同一端口；其他软件或人工操作不受此锁控制。

配置保存在当前用户的 `%LOCALAPPDATA%/AutomatedGerberCompare/settings.json`，不修改项目文件。

## 命令行（可选）

```powershell
python -m pip install -e .

# 只校验，不要求安装 Valor
gerber-compare validate A.zip B.zip --output C:/GerberCompare

# 生成含动态占位符的命令计划，不调用 Valor
gerber-compare prepare A.zip B.zip --output C:/GerberCompare

# 阅读校验结果后显式确认；也可省略参数使用桌面端保存的配置
gerber-compare compare A.zip B.zip --rcom D:/SiemensEDA/Valor2604/vNPI_2604/edir/misc/rcom.exe --database eda_server_db --port 11000 --output C:/GerberCompare --yes
```

`compare` 退出码：`0` 一致、`1` 有差异、`2` 校验未通过/未确认、`3` 执行失败、`130` 取消。未提供子命令时启动桌面应用。

## 测试与 Windows 打包

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

打包输出：`dist/AutomatedGerberCompare/AutomatedGerberCompare.exe`；分发整个目录，运行目标机无需额外安装 Python，但仍需本机 Valor 和许可证。GitHub Actions 在 Linux/Windows 上测试，并在测试通过后生成 Windows 应用目录压缩包，可从 Actions Artifacts 下载。

自动化测试使用人工构造的 Gerber 数据和模拟 RCOM，不把模拟结果作为真实板卡判定。Tk 桌面冒烟测试需要 Windows 或可用的图形显示服务；无显示服务时会跳过。**真实 Valor 的导入、截图效果和现场配色仍需按 [现场验收清单](docs/ACCEPTANCE.md) 验证。**

## 参考与设计

实现参考任务书、附件 `LMC_Demo.txt` / `LMC_Run_Record.txt` 及 Valor NPI 2604 手册中的命令接口，并核对了 [SV-EDAServer-Platform](https://github.com/MobiusTechnology1998/SV-EDAServer-Platform) 的 `gerber_analyzer.py`、Valor 命令模型、渲染器与执行器。采用独立实现；厂商手册、真实 PCB 文件和参考项目源文件未复制到本仓库。

模块职责和关键取舍见 [架构说明](docs/ARCHITECTURE.md)，原始需求见 [task.md](docs/task.md)。
