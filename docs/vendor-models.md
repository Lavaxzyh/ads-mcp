# 官方 0402 厂商模型工作流

历史 4 GHz 案例使用 Kyocera AVX 的 ADS 原厂库。下载来源、SHA256、3.9 pF 数据文件与 DC 限制见 [可执行下载示例](../examples/vendor_avx_0402/README.md)。原厂库和论文 PDF 不打包进仓库。

下载并安全解压官方 `ADSNew.zip`，记录来源和哈希；解析 `AVX_Capacitor_cktName.net` 的 `AVX_C0402` 文件列表，根据 **C0402SE3r9.s2p** 定位 `PartNumber`。历史索引58仅作为记录，不能跨版本写死。

`ads_import_library` 调用 `workspace.add_library_definition_file` 注册厂商 `lib.defs`。重开工程执行库初始化，然后放置 `AVX_RF_Components:AVX_C0402:symbol`，设置所查得的 PartNumber。在最顶层放一次 `AVX_INCLUDE`，检查网表里的定义和路径。重复放在每个子电路会出现作用域或定义问题。

先创建两端口单电容测试台，在数据有效频段验证响应，再接入 EM 模型器件安装端口。封装/焊盘的参考面必须避免重复计入。`ads_import_library` 只注册本地文件，不联网下载、不修改原厂模型。

S2P 从50 MHz开始，无物理 DC 数据。历史常数外推导致电容支路非预期DC电流，电容用途曾在模型两侧明确加 `DC_Block(Mode=1)`。该处理仅描述电容的直流阻断假设，不能当成所有 S 参数模型的通用修复。PIN 的论文参数与此 3.9 pF 器件不是同一个模型。
