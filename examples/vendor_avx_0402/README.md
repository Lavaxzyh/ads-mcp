# AVX 0402 3.9 pF 厂商模型接入

官方来源：[Kyocera AVX ADS kit](https://www.kyocera-avx.com/download/software/ADSNew.zip)。模型文件及原厂库不随仓库再分发，使用`download_vendor.py`显式下载、记录SHA256并解压。历史归档SHA256为`ef1cd8556352ef2d5db9d4e165ed1ffe4ef4a89c77b0fda74a02f99d0ee1bc9f`，上游文件可能更新。

1. `python examples/vendor_avx_0402/download_vendor.py <新vendor目录>`。
2. 用`ads_import_library`注册解压包内的`AVX_RF_Components/lib.defs`，关闭并重开目标工作区使库初始化生效。
3. 选择`AVX_RF_Components:AVX_C0402`。解析厂商模型列表以确认`C0402SE3r9.s2p`对应的PartNumber；历史版本为58，脚本不写死索引。
4. 在顶层测试台放置一次`AVX_RF_Components:AVX_INCLUDE`，让子电路共享厂商模型定义；检查生成网表中的Include路径和作用域。
5. 先在两端口电容测试台确认真实数据加载，再将器件模型连接到EM模型的安装端口；核对焊盘、封装及参考面覆盖范围。

S2P文件从50 MHz开始，不含DC。历史常数外推曾产生非预期电容支路电流。后续电容用途的单态检查在S2P两侧明确增加`DC_Block(Mode=1)`，保留RF数据而阻断DC；这不是补全厂商物理DC模型，也不应作为所有S参数器件的自动默认处理。
