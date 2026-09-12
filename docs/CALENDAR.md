# 交易日历：来源、验证与更新

核验日期：2026-09-12。此文说明接口文档核验，不代表已经在用户券商客户端运行过接口。

## 官方资料

1. [迅投 xtdata 官方文档](https://dict.thinktrader.net/nativeApi/xtdata.html)：`get_trading_dates`、`get_holidays`、`get_trading_calendar`、`download_holiday_data`。运行逻辑说明 xtdata 与 MiniQMT 数据端交互；数据可能需要先补充。获取交易日历对未来日期的处理依赖下载好的节假日。
2. [迅投 SDK 下载与变更说明](https://dict.thinktrader.net/nativeApi/download_xtquant.html)：2024-10-17/241014 变更记载 get_trading_calendar 自动下载需要的节假日数据。仍须在当前券商 SDK 核验实际联网和返回格式，不能把调用认作保证离线。
3. [上交所 2026 年部分节假日休市通知](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)：2025-12-22，上证公告〔2025〕45号。
4. [深交所 2026 年部分节假日休市通知](https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html)：2025-12-22，深证会〔2025〕481号。

内置年度包录入元旦 1/1–1/3、春节 2/15–2/23、清明 4/4–4/6、劳动节 5/1–5/5、端午 6/19–6/21、中秋 9/25–9/27、国庆 10/1–10/7；其余周末也休市。按该包计算全年242个工作日交易日期。这是程序依据公告生成的计数，不是来自未获取的 QuantClass 数据。

## 判定顺序和约束

先将运行时间按固定中国时区取日。周六日无须账户或网络即可确定证券市场休市，包括调休上班的周末。

工作日读取 state_dir/calendars/<year>.json（本机更新包），不存在或校验失败时检查安装包中的 calendars/<year>.json，并记录警告；再验证 state_dir/calendar.json。本地现代缓存与年度公告冲突时直接 unknown。有效年度来源可作为丢失/损坏/过期缓存的独立兜底，而非把缓存错误解释为休市。

年度包必须显式声明全年起止、complete_year、七类节日、SH/SZ公告来源和编号、核对日期及SHA-256。日期结构、范围、来源主机和计算结果均校验。人工完整性复核是维护者责任，checksum不替代来源审核。临时休市可经复核写入 extra_closures；有更正公告必须及时更新。

历史QMT缓存只采信实际获取的SH/SZ日期列表及其交集覆盖，禁止使用请求参数的年末冒充可用覆盖。覆盖区间内缺失可表示休市，区间外一律未知。旧版列表无来源及起点，只能与年度包整个声明区间相等后复用；逐个出现的年度节假日不能充当全年证明。

## 可执行维护路径

```powershell
$Root='D:\QMT 打新'
$Python=Join-Path $Root '.venv\Scripts\python.exe'
# 纯本地读取，不连接账户/SDK，不发送消息
& $Python -B "$Root\code\calendar_tool.py" status --config "$Root\config.json"
# 联系数据端读取已有交易日，不调用 broker.connect/交易账户
& $Python -B "$Root\code\calendar_tool.py" refresh --config "$Root\config.json"
# 明确许可SDK下载节假日，并核对当前年度未来列表；不触发任何申购
& $Python -B "$Root\code\calendar_tool.py" refresh --config "$Root\config.json" --allow-download
# 年度公告包必须先经人工逐项核对，才可导入；旧包原字节保留为本机备份
& $Python -B "$Root\code\calendar_tool.py" import --config "$Root\config.json" `
  --annual-file 'D:\Reviewed Calendar\2027.json' --reviewed
```

前三项是可运行的日历维护命令；最后一项路径表示未来由维护者提供的已核对文件，本交付**没有虚构2027包**。先查看该年沪深正式公告，修改 year、覆盖、七类范围及来源，使用源码中的 `sealed(doc)` 生成校验和，再 `validate_document(doc)` 校验后提交审查/导入。例如在源码目录用下列脚本处理已人工准备的 JSON（不能直接复制2026日期到下一年）：

```python
from pathlib import Path
from runtime import read_json, atomic_json
from market_calendar import sealed, validate_document
path = Path(r'D:\Reviewed Calendar\2027.json')
doc = sealed(read_json(path))
validate_document(doc)
atomic_json(path, doc)
```

周期流程遇未知日历可启动独立 calendar_tool 子进程，20秒超时、每1800秒最多一次；仅传状态路径和日期，不传账号或Webhook。取数据成功后再次纯本地判定，再决定是否允许连接交易账户，不存在“日历更新必须先交易账户连接”的死循环。

如果新年度包尚无，SDK只返回到上一交易日，今日仍未知并禁止提交；SDK数据若已真实返回今日，则有当日覆盖的合法路径。没有明示下一年覆盖不能放行下一年。年末应由TARS提前检查下一年公告包是否已更新并测试跨年日期，不能等自动猜测。

QMT历史接口按官方合同接受毫秒时间戳；未来日历比对适配仅接受YYYYMMDD字符串。当前真实券商SDK是否返回这些类型、需要何种行情连接/联网，列为本机待验收。返回其他类型、空列表、非法/跨范围日期、SH/SZ冲突、未来数据与公告不一致都拒绝，不转换成任意真值。

## 告警和通知

未知日历由申购与监控共用 control_dir/monitor.sqlite3 的稳定事件身份。相同年度、原因和证据只进入一次队列；刷新时间不会改变事件身份。该事件消息失败仍按原 outbox 机制重试，这是递送重试而非不断创建新告警。原交易日连接故障和监控告警的节流逻辑继续保留。

只对被证明为休市日的严格纯连接/纯缺跑消息做抑制。必须查到当日无任何委托意图，且正文完全匹配已知安全格式；否则原样保留。新增 notification_suppressions 保存原因、登记时间、日历来源；原行的内容和delivered标志不修改，界面可见抑制计数。缺少可读账本不抑制。

## 未接入的 QuantClass

仓库和本次环境无法访问其客户端或数据库，没有验证 period_offset/offset 的文件/表结构、日期范围或语义。没有实现猜测性适配，也没有把 offset 当休市日历。后续本机可选接入必须提供真实证据和独立完整性检查；目前首次安装、日历刷新和年度导入均不依赖它。
