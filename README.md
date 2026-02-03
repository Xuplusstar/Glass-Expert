# CodeV Glass Expert

用于 CodeV 的“玻璃专家（Glass Expert）”中的宏与数据文件集合，面向玻璃/材料相关的分析与优化流程。

**文件说明**
- `xjx.seq`: 优化相关宏文件（具体流程以文件内注释为准）。
- `perf.seq`: 性能分析宏（如 MTF、点列图等）。
- `user.dat`: 用户输入/配置数据。
- `CDGM.dat`: 玻璃库/材料数据库文件。

**使用方法**
1. 将以上文件放置在工作目录中。
2. 根据需求运行相应 `*.seq` 宏。
3. 若宏依赖材料库或用户配置，确保 `user.dat` 与 `CDGM.dat` 已正确配置并可被 CodeV 读取。

**玻璃专家使用**
1. 在 CodeV 中运行 Glass Expert 宏（`GlassExpert.seq`），打开 Glass Expert 对话框。
2. 在对话框中选择/填写 `AUTO sequence`（例如 `xjx.seq`），可选填写性能宏与输入文件。
3. 输入文件中可以选择填写用户自定义玻璃库（`user.dat`）或使用默认材料库（`CDGM.dat`）。
4. 确认参数后执行，生成优化或性能分析结果。



