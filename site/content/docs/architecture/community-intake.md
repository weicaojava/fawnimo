---
title: "公开社区需求接入与版本基础"
description: "默认关闭的独立维护者服务，源时钟与不可变版本。"
---

`run_community.py` 构造独立FastAPI服务，入口为 `/community/github/events`；未挂入个人工作空间网关。默认关闭且不建目录/数据库。开启须显式数字repo ID、完整名称及独立私有webhook secret。专用SQLite目录拒绝studio数据混用、路径重定向或另一repo/label配置；导入和命令行不读取个人settings/ORM、视频、模型或密钥。

先按原始有界UTF8字节验证SHA256 HMAC，恒时比较；投递UUID/事件/签名头必须唯一。最大1MiB，JSON重复字段、非有限数（含1e999）、错误源身份、非公开repository及错误Issue类型均拒绝。只投影人类enhancement公开提案的六种动作 opened/edited/reopened/closed/labeled/unlabeled；其他事件/动作只记最小回执。正文永远是需求数据，没有模型调用或命令执行。

`community_source_clocks` 先保存最小issue身份、时钟、源fingerprint与选择状态；未知或撤回的公开Issue不留无关正文。先观察到较新closed/unlabeled时，随后旧opened不能复活；同钟不同内容暂停，只有新时钟可恢复。已选提案及其已知历史另有不可变版本，更新的A→B→A是新epoch，移除标签/关闭不是软件完成。

投递回执与源版本在单一短事务提交；同delivery相同原字节/上下文重放，同源内容/时钟只建一版本。旧事件不能覆盖新源，INSERT仅允许空初始request，current指针须同源/最新clock，delivery两ID/outcome须配对，主键与自然键REPLACE拒绝。列表还联合clock检查，未确认来源不显示可选择。

`source_eligible` 仅表示最近已选快照，不证明GitHub当前仍存在、公开、未转移或允许执行。后续评估/排队必须独立复查原公开源；这些阶段目前尚未接通。HMAC本机自作事件不等于真实GitHub App/公网投递资格，真实源连接和自动研发继续推进。

43冻结专项、独立43以及原5反例/额外4补测分记于 `capabilities/community-intake-validation.json`；v1两P2一P3关闭，旧失败保留。主工作室OpenAPI234/549和前端同父；独立schema为backend/community-openapi.json（2路径/2schema）。本轮不调用真实模型、不外发GitHub写动作、不部署公网。

协议依据：[GitHub签名校验](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)、[事件与payload](https://docs.github.com/en/webhooks/webhook-events-and-payloads)、[投递去重](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)、[事件乱序](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/troubleshooting-webhooks)。
