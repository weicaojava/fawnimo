---
title: "人工核对模型账单"
description: "在固定个人空间离线导入明确账户的私有原始账单和规范化记录。"
---

# 人工核对模型账单

此工具用于操作者核对已有通用模型调用账目，不会调用模型、支付费用或提高项目预算。单独工作室的文本工作流、文本连接测试和单张图片候选已保存项目费用及响应请求证据；身份网关收费入口仍关闭。没有保存的精确调用身份时，导入只能产生待匹配记录。先阅读[当前账目边界](../../architecture/model-accounting/)。

1. 备份所选身份根与空间的DB、files、media，保持备份私有。停止这个身份根对应的网关；独立旧工作室不需要停止。其他工具仍占锁或空间认领未完成时，先完成对应维护恢复。
2. 从供应商取得原始账单，核对历史供应商ID、连接版本、账户版本、保存的请求标识和金额。保留原始文件，不把密钥或私人账单提交到Git。
3. 人工制作UTF-8 CSV，列名为 `row_reference,provider_request_id,status,currency,amount_minor`。每行是该请求的**总费用**，金额使用整数分，币种目前仅 `CNY`。`charged` 表示明确收费；`not_charged` 必须有明确未收费证据且金额0。没有记录不能填写“未收费”。行和请求标识在同文件内必须唯一。
4. 从 `backend` 运行已升级代码，显式选择原有所有者与身份根。下面是字段格式示例，所有名称/金额均为自作示例，须替换为实际证据。

```csv
row_reference,provider_request_id,status,currency,amount_minor
row-1,example-saved-request,charged,CNY,12
```

```text
python reconcile_model_billing.py --root <identity-root> --account <existing-owner> --original <private-original-file> --normalized <private-normalized.csv> --provider-id <historical-provider-id> --connection-version <historical-version> --account-version <historical-account-version> --statement-key <stable-statement-key>
```

工具输出账单ID和已核对、未匹配、多义、冲突、授权不足、已确认重复的数量，不输出供应商请求标识或原始内容。私有原始/规范化文件按哈希保存于所选空间 `files/billing-evidence/`；保留它们及完整files备份，不能把公开素材目录当账单存储。

同一来源键与证据重试安全；改变内容会冲突，不会覆盖旧账单。资金不足时，项目所有者先在“项目设置 → 项目预算”明确调整本项目上限，再用原账单ID重新核对：

```text
python reconcile_model_billing.py --root <identity-root> --account <existing-owner> --statement-id <saved-statement-id>
```

若另一份账单重复记录同一已结算调用，应先查看私有账目确认冲突行与原收据。完全相同的历史账户、请求、收费状态、币种及金额，可显式记录原因：

```text
python reconcile_model_billing.py --root <identity-root> --account <existing-owner> --resolve-line <conflicting-line-id> --reference-receipt <original-receipt-id> --reason "已人工核对同一请求的重复证据"
```

不同金额、更正、退款或多个调用共用请求标识不能通过这个命令当成重复。它们继续保持未决；不要删除账目、改预留、伪造零费用或关闭保护来绕过。原始证据的完整性不等于供应商自动认证；`provider_bill_verified` 继续为false。
