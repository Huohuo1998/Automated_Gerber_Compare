开发一个程序，目标是：实现调用Valor NPI程序实现自动化Gerber对比功能，并输出报告

# 初步工作流程
1. 收集整理用户输入的资料
2. 基于输入资料和预设模板，生成成套的Valor行命令
3. 执行Valor行命令，驱动Valor完成Gerber对比
4. 收集输出，编写报告

# 行命令说明
关于Valor行命令的使用方式，可以重点参考项目 git@github.com:MobiusTechnology1998/SV-EDAServer-Platform.git 的src/eda_server/core/worker/gerber_netlist_check和src/eda_server/libs/eda_executor/valor。
如果需要Valor NPI的远程控制与行命令模式的官方文档，可以参考附件包中的《Valor NPI System Management User Guide.pdf》

# 用户输入说明
1. 提供上传界面，要求用户必须上传两份Gerber文件压缩包
2. 两份压缩包都上传完成后，按下文要求校验其内容是否符合要求
3. 校验结果展示给用户
4. 基于结果决定是否允许用户继续下一步
5. 校验通过（不存在错误，允许警告），则等待用户确认，并开始Gerber对比
6. 校验不通过（存在错误），则拦截，要求更新Gerber文件压缩包

# Gerber文件压缩包说明及要求
1. Gerber文件压缩包可能存在多个子文件夹，需获取所有层级的文件清单
2. 识别压缩包内的所有Gerber文件并分类，识别和分类的规则和方法重点参考项目 git@github.com:MobiusTechnology1998/SV-EDAServer-Platform.git 的 src/eda_server/core/worker/gerber_netlist_check/pre_processor/gerber_analyzer.py
3. 两个压缩包内的Gerber文件都必须成功建立一对一的对应关系，否则视为错误。非Gerber文件不做要求。

# Valor操作说明
1. 先创建job，导入Gerber资料，两份Gerber资料导入到同个job，但是在层的命名上需要通过增加.A/.B后缀用于区分来源
2. 对每一对匹配的Gerber文件执行compare_layers，生成对比结果层，对比结果层使用.Result作为后缀
全部对比完成后，统一处理.Result层的多余内容，然后同时点亮同一对gerber的.A/.B/.Result层，然后截图保存为图片，作为报告素材来源
3. 操作所需的行命令，命令对应的作用和参数说明，参考附件包的LMC_Demo.txt（仅命令说明）和LMC_Run_Record.txt（Powershell实际运行记录）文件

# 程序输出
全部Gerber对比完成后，生成报告，列出对比的记录，附带图片，和判定结果（是否一致），报告可下载

# 程序要求
1. 独立的桌面应用程序
2. 主技术栈使用pyhton
