# CODE V 分组玻璃专家：研究与实现

更新：2026-10-30；依据本机 CODE V 2025.03。


**推荐“外层按组调度原生GE，按需加跨组双片试换”。** 原生GlassExpert.seq保持原样，用户AUT继续决定光学目标、制造约束和几何优化。本目录已提供离线准备器、调度代码、实例及离线验证；尚未启动CODE V验证新调用，也没有用户镜头的性能改善结果。

## 1. 你的需求如何表达

第1、2片只用A清单，第3～5片只用B清单。A/B可以是不同厂商，也可以是同一厂商筛出的不同子集，例如“大口径可供料”和“可模压材料”。

需要同时选择离散材料和连续几何参数：

\[
\min_{g_i\in C_i,\;x\in\Omega} F(x,g_1,\ldots,g_n).
\]

其中，C_i是第i片允许的材料集合，g_i是实际牌号，x是可变曲率、厚度、非球面等，Ω是结构与制造约束，F由AUT定义。材料归属在候选集合上保证，光学性能由重新优化后的系统评价。

配置填写**材料所在的表面号**，不是镜片序号。单透镜的材料通常记在前表面之后；胶合结构应按实际处方填写，不能机械换算为1、3、5。

身份以“目录名＋牌号”判断。两份DAT都来自CDGM时，单看 `(GLA Sk CAT)` 无法区分A/B，必须查牌号白名单。[S1，物理页212、217、221–222]

## 2. 方案比较

| 方法 | 材料限制与搜索能力 | 判断 |
|---|---|---|
| 官方AUT罚函数 | 合并A/B后给越组材料很差评价，仍会试算违规组合 | 可作原生绕法，不作为本版合法性基础 |
| 分组轮换原生GE | 每次只替换一组并使用本组候选表，其他组保持牌号 | 复用原生算法，最适合作为基础 |
| 完整外层枚举 | 每片只从自己的清单取值，逐组合做AUT | 候选少时可作基准，组合数量增长很快 |
| 分组GE＋跨组双片试换 | 同时改动不同组的两片，分别遵守各自清单 | 可探索单片分别更换都无改善的配合关系 |
| 遗传算法、模拟退火等 | 外层优化合法牌号索引，内层AUT优化几何 | 值得研究，但不能预先保证比原生更快、更好 |
| K2realm多清单扩展 | 厂商宣称可对不同元件指定不同清单 | 可对照产品功能，内部算法与本机兼容性未核对 |

原生接口和罚函数依据[S1]；K2realm功能声明依据[S4]。搜索策略的比较是本任务的工程分析，不是光学性能实测。

## 3. 已实现的基础方案

```text
恢复输入镜头，检查所有组的起始材料合法
同一份AUT优化，建立有效基线
每轮：
    A组：只允许A组材料面参加GE，使用A.dat
    B组：只允许B组材料面参加GE，使用B.dat
    如启用：执行一批跨组双片试换
    整轮改善足够小或达到轮数上限时停止
```

配置可以这样写：

```json
"groups": [
  {"name": "A组", "surfaces": [1, 3], "candidates": "A.dat"},
  {"name": "B组", "surfaces": [5, 7, 9], "candidates": "B.dat"}
]
```

prepare生成各组独立的Surfaces和User Catalog设置，再通过官方接口调用：

```text
IN GlassExpert AUT文件 性能回调文件 本组输入文件 邻居数 N
```

这是**一次外层启动、内部多次原生GE调用**；没有给原生宏增加未公开的每片候选参数。参数和Inputs格式见[S1，物理页213–217、220]。

只限制牌号变化范围，其他组允许变化的曲率、厚度仍一起优化。真实目录玻璃本来自动冻结，不需要冻结整套几何变量；可变几何继续由输入镜头与AUT决定。[S1，物理页29]

### 保留合法的改进

不把原生返回时的最后一次试算当成最佳结果，也不猜测_best文件名。本版提供独立的Performance回调：

1. 按目录＋牌号检查材料归属，检查未参与本轮的牌号没有改变。
2. 检查所有组态，目标面各组态须用同一材料。
3. 检查 `(OPT ERF)=1`，读取 `(AUT.ERF ALL)`，排除不可恢复评价。
4. 只把合法且改善的状态保存到本轮独立LEN快照。
5. 原生调用结束后恢复此快照，再核对材料和评价。

Performance file允许执行指定SEQ，AUT评价可以随LEN保存并查询有效性。[S1，物理页212–213；S2，物理页200]

**待实机核对**：原生调用回调的时机、回调保存LEN对原生文件名和流程的影响，以及COM诊断文本。本轮没有把这些标为已在CODE V跑通。

## 4. 联合试换如何改善搜索

逐组择优可能遇到：A组某片单独换变差，B组某片单独换也变差，但两片同时换能改善色差分配。联合阶段从不同组各选一个面，各自从本组清单选材料，同时更换，再执行同一份AUT。

- `joint_pairs`指定重点探索的跨组面号对；留空时使用所有跨组面号对。
- `joint_trials`限制每轮总试算数。组合少于预算就遍历，超过预算按固定种子抽样，避免只试DAT开头的材料。
- 同批候选从同一个父镜头快照恢复，避免前一个候选的几何状态污染下一个。
- 改善方案单独保存；失败或不合规试算不作为结果，随后恢复已接受快照。

本版每阶段沿用当前最佳解，**没有实现多解束搜索或自动多起点**。可用同一清单/AUT对不同起始LEN分别准备任务；组顺序由JSON中groups顺序决定。

不保证全局最优。假设五片各有20种候选，材料组合就有20^5＝3,200,000种，每种组合还需几何优化，而且AUT结果依赖初始几何状态。候选预筛、统一条件和预算控制应先落实；这个数量是示意计算，不是本机材料统计。

### 各阶段必须使用一致条件

- 所有阶段调用准备时复制的同一AUT。焦距等应为固定设计指标；每次按当前 `(EFL)` 重新定义目标，会使不同组合的目标漂移。
- PERF只展示性能。画了MTF不等于在优化MTF，本版按AUT总评价择优。[S1，物理页211、213、216、221]
- 默认由现有玻璃库工具预筛供货、等级和透过率，原生输入不再自动排除已明确列入DAT的停产/特殊牌号。是否列入由你准备清单时决定。原生仍按自身规则检查材料在系统波长下是否可用。
- 原生内部的透过率、成本、CTE筛选不会自动进入外层联合试换。本版拒绝同时使用自定义native_settings和联合阶段；应把要求落实到DAT/AUT，或设joint_trials＝0。
- 原生Relative Weight以每次GE初始系统为基准，多次运行会重置。本版要求该项为0，绝对重量要求可放在统一AUT中。[S1，物理页216]

## 5. 文件与示例

| 文件 | 内容 |
|---|---|
| [grouped_glass.py](grouped_glass.py) | 离线prepare和显式run；只有run启动COM |
| [dbgauss_groups.json](examples/dbgauss_groups.json) | 官方DBGAUSS分组示例 |
| [demo_A.dat](examples/demo_A.dat)、[demo_B.dat](examples/demo_B.dat) | 各4种已安装材料，包含示例起始材料 |
| [demo_aut.seq](examples/demo_aut.seq) | DBGAUSS示意AUT，不是你的产品约束 |
| [tests/test_grouped_glass.py](tests/test_grouped_glass.py) | 调度模拟器、回调子集解释器、约束与回滚测试 |

官方DBGAUSS实际含6片：S1/S3的前两片分到A，随后S4/S7/S8三片分到B，第6片S10保持原牌号。面号来自安装目录lens/dbgauss.seq。

历史DAT与PERF.seq、user.dat作为参考保留；user.dat引用的CDGMH.dat未随仓库提供，使用新分组功能时从examples/dbgauss_groups.json开始配置。

## 6. 怎样使用

准备起始LEN、自己的AUT和A/B候选DAT，复制示例JSON并修改路径和材料面号。AUT及可选setup/performance按原字节复制；其内部引用的依赖仍须能从英文路径或CODE V搜索路径找到，本版不递归打包函数库。

prepare只做文件检查，不启动CODE V。在本目录终端执行，输出目录须尚不存在：

```powershell
# 在下载后的仓库目录运行以下命令。
python .\grouped_glass.py prepare .\examples\dbgauss_groups.json --out C:\CVUSER\GroupedGlass\demo_job
```

它核对候选在已安装XML中的目录名/牌号，复制LEN与AUT，生成每组DAT/Inputs及job.json和哈希。**名字存在不等于软件已识别色散、热学数据或镜头可追迹。** prepare目录可以含中文，run目录须为英文；可把本机准备好的整个目录复制到英文路径。

下面命令才会启动独立CODE V COM会话并优化；本轮未执行：

```powershell
python .\grouped_glass.py run C:\CVUSER\GroupedGlass\demo_job\job.json
```

prepare只依赖Python标准库；run另需Windows、pywin32和可用CODE V COM许可。工具自己的SAV只写任务results目录。用户提供的AUT应限于本任务优化，不包含另存覆盖原文件、恢复另一个镜头等与调度冲突的操作。

当前用户入口是PowerShell中的Python程序；不在CODE V宏管理器中选择grouped_glass.py。自带JSON使用官方DBGAUSS示例。换成自己的项目时，另存一份JSON，修改lens、aut以及各组surfaces和candidates，再将命令中的JSON路径换成自己的文件。代码不读取当前CODE V窗口里尚未保存的镜头。

### 输出与失败后查阅

results/summary.json记录基线、各组、联合候选及拒绝原因；commands.log保存命令返回文本。source_before.len为优化前工作副本，分阶段LEN为接受的快照，final.len为正常完成的结果。失败记录last_accepted和source_snapshot，不标为完成。

已有results的任务不覆盖重跑；重新prepare到新目录。最终仍需按统一视场、波长、孔径与采样，核对MTF、透过率、制造条件及公差，不能把AUT下降当作良率提高。

## 7. 验证范围

已通过17项离线测试：

```powershell
python -B -m unittest discover -s tests -v
```

覆盖独立分组输入、非法引用/重叠、合法改进快照、越组拒绝、最后试算不被误认为最佳、过期/不可恢复评价、双片配合、同批同父镜头、失败回滚、组态一致性、文件变化及原文件保护。

模拟器中“评价10→2”是人为构造的配合问题，不是实际镜头结果。回调解释器只检查生成的IF/赋值/SAV子集，不能代替CODE V语法编译或GE实测。

仍待软件确认：Inputs兼容性、回调保存行为、组态/拾取关系、实际AUT执行、诊断与收敛时间及光学效果。起始材料须已满足各组清单，本版不自动做最近玻璃初始化。主要面向普通顺序GLA材料面，未扩展NSS两侧GL1/GL2、镜面基底GLM及隐藏模块材料。

原生宏SHA256仍为 `F0AB147E611AFFBF01A252735F008CC59422C8D30D065DFB90C77D6731A6213A`。本轮没有启动CODE V、COM会话或窗口。

## 8. 依据

- **S1**：CODE V 2025.03 Optimization Reference Manual，PDF物理页29（真实玻璃冻结）、211–217（GE参数及文件）、220（复用Inputs）、221–222（按面限制与罚函数）。
- **S2**：Macro-PLUS Reference Manual，物理页25（变量作用域）、175（数据库）、185（材料名/目录）、200（AUT.ERF及OPT ERF）。
- **S3**：COM API Reference，物理页32、44–45（Command、EvaluateExpression等接口）。
- **S4**：[K2realm多清单功能](https://k2realm.com/tools/glass-expert/)及[2021年功能介绍](https://k2realm.com/optical-system-design-and-glass-expert-work-smarter-not-harder/)，2026-10-30核对厂商搜索可读摘要；直接打开返回403。仅用于确认其功能声明，不证明算法和本机兼容性。
- 本机原件：C:\CODEV202503\macro\GlassExpert.seq头部接口；C:\CODEV202503\lens\dbgauss.seq处方；所选安装目录glass/*.xml的实际Name与GlassName。
