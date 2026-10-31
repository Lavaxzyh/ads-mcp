# ads-mcp：ADS 2025 功放工作流分支

[English](README.md) · [原作者项目](https://github.com/Lavaxzyh/ads-mcp) · [MIT 许可](LICENSE)

这是基于 **Lavaxzyh/ads-mcp v0.4.0** 的 **ADS 2025 适配与射频功放工作流增强分支**，维护目标为 **`feature/power-amplifier`**。继承原作者的原理图、原生 Layout、Momentum、emModel 和联合仿真基础，保留上游 **49 个工具名称**，当前实际注册 **64 个工具**。

分支版本：**`0.4.0+ads2025.20261030.3`**，标识 `ads2025-share-20261030.3`。它不是原作者主线的新版本。实现基于上游 `72e2793`，此次提交基线为 `0181a5b`；后者只追加了 README 徽章修正。

本分支先独立维护 ADS 2025 兼容处理和功放相关工作流，后续与作者协作整合。重点是可靠执行已有设计、分析和验证步骤，不能替代工程判断，也不是 Keysight 官方产品。

## 相对原项目的适配与强化

| 方向 | 本分支内容 | 适用边界 |
|---|---|---|
| ADS 2025 兼容 | 原生 API 差异适配、Python 3.12/PySide2、有认证的专用 GUI 桥接 | Windows ADS 2025 实测；不接管任意用户窗口 |
| 上游原有工具 | 原理图、原生 Layout/PSN、lookalike、Momentum、emModel 和部分联合仿真 | 保留名称不等于所有版本、元件与操作都已验证 |
| Load-pull 分析 | 保存点全量导出、明确条件比较、Pout/PAE/增益筛选、分频阻抗区域 | 实际保存点与插值分开；数值有效不等于 HB 收敛 |
| 匹配网络搜索 | 批扫、频变目标、预算粗细搜索、标称值/尺寸约束、保守缓存 | 明确两端口 SP 条件；不把匹配评分当作非线性功放性能 |
| 敏感度与容差 | 单参数扰动、角点、固定种子蒙特卡洛 | 指定参数及独立分布假设，不等于实测良率 |
| 已有 HB 复核 | 受支持模板的明确工作点、候选参数及指标复核 | 需要使用者提供模型、探针、端接与授权；不是任意 HB/PDK 自动生成 |
| 单原理图工作流 | 短模板、一次导入、同副本增量修改、变更清单、保存重开、简短摘要 | 原工程只读，不擅自改变拓扑、板材、端口、参考面和单位 |
| 长任务与隔离 | 稳定请求/任务 ID、工作区锁、状态等待、RF 检查点和有限重试 | 超时先核对原任务，不自动重派或终止其他会话 |
| 版本与自检 | 加载代码/磁盘版本区分、实际工具目录及依赖检查 | 不自动重启其他客户端或 ADS 会话 |

代码保留适用的 ADS 2027 官方桥接路径，但**本分支未完成 ADS 2027 全量回归**。原作者在 ADS 2027 上的验证与本分支的 ADS 2025 验证应分别理解，不能写成“全版本通用”。详见 [验证摘要](docs/VALIDATION.zh-CN.md)。

## 安装与配置

需要已授权的 Windows ADS 2025。原生验证环境为 ADS 2025 `610.shp`、ADS Python 3.12.2、PySide2 5.15.10。Keysight Python 模块必须来自对应安装，不随源码分发；不要复制其他 ADS 版本的原生模块来绕过兼容要求。

以下占位符须替换为自己的绝对路径，运行输出放在仓库之外。

```powershell
git clone --branch feature/power-amplifier https://github.com/Lavaxzyh/ads-mcp.git
cd ads-mcp
& '<ADS_INSTALL>/tools/python/python.exe' -m venv --system-site-packages .venv
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
```

在作者合并本次贡献之前，应使用贡献者 Fork 地址或已审核源码包，不能假定上游分支已经包含这些代码。

```json
{
  "mcpServers": {
    "ads-mcp": {
      "command": "<REPO>/.venv/Scripts/python.exe",
      "args": ["-B", "<REPO>/mcp_server.py"],
      "env": {
        "HPEESOF_DIR": "<ADS_INSTALL>",
        "ADS_MCP_WORKROOT": "<OUTPUT_ROOT>",
        "ADS_MCP_STATE_FILE": "<OUTPUT_ROOT>/.ads-mcp/session_state.json"
      }
    }
  }
}
```

- `HPEESOF_DIR` 优先于默认值，默认候选为系统 Program Files 下的 `Keysight/ADS2025`。三个命令行客户端采用同一解析规则。
- `ADS_MCP_WORKROOT` 是工作输出根目录，`ADS_MCP_STATE_FILE` 是可选状态文件。
- Momentum、GUI 联合仿真等需要对应许可和本任务专用会话；仓库不提供 ADS 本体、许可证或第三方模型。
- 运行单元测试时另安装 `requirements-test.txt` 中的依赖。

## 推荐用法：一次一个原理图

```txt
使用 ADS 单原理图工作流，处理指定的目标原理图。
先读取已有条件，在独立副本中只修改我指定的参数或器件。
复用同一专用会话，保留拓扑、板材、模型、端口、参考面和单位。
未明确授权时不仿真。保存验收后，只返回变化、结果、耗时和记录路径。
```

入口为 `ads_design_workflow(action="run", task_directory=..., request_file=...)`：新建或导入一次，后续 `edit` 修改同一个独立工作区。完整短请求、请求 ID、显式 SP 和恢复规则见 [SINGLE-SCHEMATIC.zh-CN.md](SINGLE-SCHEMATIC.zh-CN.md)。

```powershell
& '<REPO>/.venv/Scripts/python.exe' -B '<REPO>/design_workflow_client.py' --task-dir '<TASK_ROOT>' --ads-root '<ADS_INSTALL>' --interactive
```

开始或更新后运行 `ads_runtime_selfcheck`。旧进程可能仍加载旧代码，可在该客户端空闲时重新连接，或新建专用客户端；不自动重启其他会话。

## 验证与文档

本次分享副本通过 **201 项单元检查**，并用**独立合成原理图完成真实 ADS 2025 建图、修改和保存重开**。历史 SP/EM 验证与本次发布检查分开记录；上游公开示例保留出处，没有宣称全部在本分支重测。

```powershell
& '.venv/Scripts/python.exe' -B tools/run_unit_tests.py
```

| 内容 | 文档 |
|---|---|
| 单张原理图入口 | [SINGLE-SCHEMATIC.zh-CN.md](SINGLE-SCHEMATIC.zh-CN.md) |
| 提取、变更清单、落图及版本比较 | [DESIGN-WORKFLOW.zh-CN.md](DESIGN-WORKFLOW.zh-CN.md) |
| 完整建图方案与验收 | [SCHEMATIC-WORKFLOW.zh-CN.md](SCHEMATIC-WORKFLOW.zh-CN.md) |
| Load-pull 与基础扫描 | [RF-WORKFLOWS.zh-CN.md](RF-WORKFLOWS.zh-CN.md) |
| 频变目标、搜索与容差 | [RF-ADVANCED.zh-CN.md](RF-ADVANCED.zh-CN.md) |
| RF 队列与已有 HB 复核 | [RF-JOBS.zh-CN.md](RF-JOBS.zh-CN.md) |
| 原生 EM 与专用 GUI | [EM-WORKFLOW.zh-CN.md](EM-WORKFLOW.zh-CN.md) |
| 真实验证范围、耗时与限制 | [docs/VALIDATION.zh-CN.md](docs/VALIDATION.zh-CN.md) |
| 隐私与运行数据 | [docs/PRIVACY.zh-CN.md](docs/PRIVACY.zh-CN.md) |

前一效率版本中，准备好输入后的“建图＋两次修改＋错误拦截”，复用会话中位数为 8.12 秒，基线为 9.52 秒（各 n=3）；调用 7→4 次，返回文本 5,525→2,158 UTF-8 字节。冷启动没有改善。**这不是完整射频设计耗时，也不是实际 token 节省比例。** 公开合成示例已更换电气参数，不把它冒充原性能测试输入。

## 隐私、贡献与致谢

发布内容不含维护者的原功放工程、模型库、许可证、凭据、聊天记录、虚拟环境或本机原始验证日志。新增原理图样例使用合成参数。以后运行生成的日志、CSV、自检和结果可能含自己的路径、模型名及工程数据，分享前仍须单独脱敏。

原始架构和 ADS 原生工作流来自 **Lavaxzyh 及上游贡献者**，本分支在其基础上补充 ADS 2025 兼容处理与上述工作流。保留 [MIT 许可证](LICENSE)，Keysight 软件及第三方模型遵守各自许可。

贡献优先针对作者的 `feature/power-amplifier` 分支提交 PR，暂不直接合入 main。维护方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。
