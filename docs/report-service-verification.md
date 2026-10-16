# 报告服务验证记录

验证日期：2026-10-16。验证环境：macOS，使用工作台现有的真实完成报告，通过应用的 `make_snapshot`、`Publisher.upload`、`payload` 和 `send_report` 执行上传及结果提交。

## 服务与配置

| 项目 | 地址与验证结果 |
|---|---|
| 平台页面 | `http://aitest_engine.ugreencloud.com/`，HTTP 200 |
| 平台健康检查 | `/api/health`，HTTP 200，返回 `status: ok` |
| 结果上报接口 | `http://aitest_engine.ugreencloud.com/api/tasks/external-reports`，真实提交成功，业务回执校验通过 |
| 报告存储 | `http://aiot-testing.ugreeniot.com:19000/autotest-reports`，真实写入及公开读取通过 |

正式平台域名直接解析到 `192.168.75.208`。默认配置同时更新平台页面与结果接口；报告存储仍使用单独配置的 MinIO 端点。

## 真实报告与回执

使用运行 `kea2-70e8ac4ea9df4992a24dd400a7c6fdd3`：APP-绿联声学 `com.ugreen.iot`，Pixel 9 Pro，实际探索 360.6 秒、342 步，Crash / ANR 为 0，最终状态为已完成，已取得上报资格。保留原始执行时间、运行编号和统计数据。

应用生成不含连接凭据的上传快照并上传 14 个文件。对每个文件重新执行 HTTP GET，均返回 HTTP 200，且 SHA-256 与本地上传快照一致，包括工作台报告、原生报告及其本地依赖、执行日志和 JSON 数据。

结果提交沿用原始外部运行编号，以 revision 6 更新同一平台任务，避免创建重复测试记录。回执确认项目、服务与能力、外部运行编号、任务名、状态、统计及同步版本；状态为 `succeeded`，统计为 total 1、passed 1、failed 0、steps 342、crashesAndAnrs 0，`final: true`。

- [在线工作台报告](http://aiot-testing.ugreeniot.com:19000/autotest-reports/kea2/runs/kea2-70e8ac4ea9df4992a24dd400a7c6fdd3/index.html)
- [在线 Kea2 原生报告](http://aiot-testing.ugreeniot.com:19000/autotest-reports/kea2/runs/kea2-70e8ac4ea9df4992a24dd400a7c6fdd3/raw/res_kea2-70e8ac4ea9df4992a24dd400a7c6fdd3/bug_report.html)
- [平台任务记录](http://aitest_engine.ugreencloud.com/task-center?taskId=task-7476283e6ffc44e4a72fea9828db3754)

浏览器已实际打开工作台报告和原生报告，能显示执行统计及覆盖率内容。平台任务中心页面需要正常登录；本次访客态未验证登录后的任务详情，结果入库以服务返回并经应用严格校验的业务回执为证。

本机联网验证材料保存在 `data/address-validation-20261016/`：请求、成功回执、逐文件哈希及验证结果。该目录属于本地数据，不随源码分发；原始运行目录及其连接快照保留。
