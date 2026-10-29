"""Validate the feature register and render its public, reproducible documentation.

The register records claims; this tool never infers acceptance from source presence,
HTTP success, a merged PR, or a fixture. No application database or secrets are read.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "capabilities/features.json"
BEGIN = "<!-- features:begin -->"
END = "<!-- features:end -->"
LABELS = {
    "implemented": "已实现", "partial": "部分实现", "planned": "待实现",
    "verified": "本阶段已验证", "baseline_verified": "历史软件基线通过",
    "not_verified": "未验收", "not_required": "不适用",
    "local_only": "仅本机", "not_deployed": "未部署", "public_verified": "公网已验收",
    "in_progress": "推进中", "pending": "待验收", "passed": "通过",
}


def label(value: str) -> str:
    """Keep generated status language identical across every public entry point."""
    return LABELS.get(value, value)


def cell(value: str) -> str:
    """Escape content that would otherwise corrupt a generated Markdown table."""
    return value.replace("|", "\\|").replace("\n", " ")


def local_path(value: str) -> Path:
    """Reject absolute paths and traversal before validating public repository links."""
    path = Path(value)
    if path.is_absolute() or ":" in value or "\\" in value or ".." in path.parts:
        raise ValueError(f"Repository path must be relative: {value}")
    resolved = (ROOT / path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"Repository path escapes root: {value}")
    return resolved


def validate(data: dict) -> None:
    """Check referential integrity and reject unsupported acceptance/deployment claims."""
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported register schema_version")
    groups = {row["id"] for row in data["groups"]}
    if len(groups) != len(data["groups"]):
        raise ValueError("Duplicate feature group")
    evidence = {row["id"]: row for row in data["evidence"]}
    requirements = {row["id"] for row in data["requirements"]}
    milestones = {row["id"] for row in data["milestones"]}
    if len(requirements) != len(data["requirements"]) or len(milestones) != len(data["milestones"]):
        raise ValueError("Duplicate requirement or milestone ID")
    if len(evidence) != len(data["evidence"]):
        raise ValueError("Duplicate evidence ID")
    features = {row["id"]: row for row in data["features"]}
    if len(features) != len(data["features"]):
        raise ValueError("Duplicate stable feature ID")
    for item in evidence.values():
        if item["kind"] not in {"historical_software", "historical_source_integrity", "register_check", "software", "live_model", "deployment"}:
            raise ValueError(f"Invalid evidence kind: {item['id']}")
        if item["visibility"] not in {"repository", "private_external", "redacted_public"}:
            raise ValueError(f"Invalid evidence visibility: {item['id']}")
        if item["visibility"] == "repository" and not local_path(item["location"]).is_file():
            raise ValueError(f"Missing repository evidence: {item['location']}")
        if set(item["features"]) - set(features):
            raise ValueError(f"Evidence references unknown feature: {item['id']}")
    for item in features.values():
        fid = item["id"]
        if not re.fullmatch(r"F-\d{3}\.\d{2}", fid) or item["group"] not in groups or not fid.startswith(item["group"] + "."):
            raise ValueError(f"Invalid feature identity: {fid}")
        if item["milestone"] not in milestones:
            raise ValueError(f"Unknown milestone: {fid}")
        if not item["requirements"] or set(item["requirements"]) - requirements:
            raise ValueError(f"Unknown/missing requirement: {fid}")
        if set(item["depends_on"]) - set(features):
            raise ValueError(f"Unknown dependency: {fid}")
        if item["implementation"] not in {"implemented", "partial", "planned"}:
            raise ValueError(f"Invalid implementation status: {fid}")
        software = item["validation"]["software"]
        model = item["validation"]["model"]
        if software not in {"verified", "baseline_verified", "not_verified"} or model not in {"not_required", "not_verified", "verified"}:
            raise ValueError(f"Invalid validation status: {fid}")
        if item["deployment"]["status"] not in {"local_only", "not_deployed", "public_verified"}:
            raise ValueError(f"Invalid deployment status: {fid}")
        if not item["limits"] or not item["acceptance"]:
            raise ValueError(f"Missing limits/acceptance procedure: {fid}")
        for path in item["code"] + item["tests"]:
            if not local_path(path).exists():
                raise ValueError(f"Missing code/test target for {fid}: {path}")
        if item["implementation"] == "implemented" and not item["code"]:
            raise ValueError(f"Implemented feature has no code: {fid}")
        if item["implementation"] == "planned" and software != "not_verified":
            raise ValueError(f"Planned feature cannot be accepted: {fid}")
        if item["implementation"] == "planned" and item["deployment"]["status"] != "not_deployed":
            raise ValueError(f"Planned feature cannot be deployed: {fid}")
        if item["delivery_scope"] not in {"current_delivery", "long_term"}:
            raise ValueError(f"Invalid delivery scope: {fid}")
        refs = item["validation"]["evidence"]
        if set(refs) - set(evidence):
            raise ValueError(f"Unknown evidence: {fid}")
        if any(fid not in evidence[ref]["features"] for ref in refs):
            raise ValueError(f"Evidence scope does not include feature: {fid}")
        kinds = {evidence[ref]["kind"] for ref in refs}
        if software == "baseline_verified" and "historical_software" not in kinds:
            raise ValueError(f"Historical acceptance needs historical evidence: {fid}")
        if software == "verified" and not kinds.intersection({"software", "register_check"}):
            raise ValueError(f"New acceptance needs software evidence: {fid}")
        if "register_check" in kinds and fid not in {"F-015.01", "F-015.02"}:
            raise ValueError(f"Register check cannot qualify application software: {fid}")
        if model == "verified" and "live_model" not in kinds:
            raise ValueError(f"Model qualification needs live-model evidence: {fid}")
        if item["deployment"]["status"] == "public_verified":
            parsed = urlparse(str(item["deployment"].get("url", "")))
            if "deployment" not in kinds or parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(f"Public deployment needs URL and evidence: {fid}")
            if parsed.hostname == "localhost" or parsed.hostname.endswith((".localhost", ".local", ".test")) or parsed.username or parsed.password:
                raise ValueError(f"Public deployment URL is not public: {fid}")
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                address = None
            if address and not address.is_global:
                raise ValueError(f"Public deployment URL is not public: {fid}")
        for url in item["prs"] + item["issues"]:
            if not url.startswith("https://"):
                raise ValueError(f"Invalid external traceability URL: {fid}")
    # Stable IDs form an acyclic dependency graph; stages can be planned independently.
    visiting, visited = set(), set()

    def visit(fid: str) -> None:
        """Detect dependency cycles before they can silently deadlock a delivery queue."""
        if fid in visiting:
            raise ValueError(f"Feature dependency cycle at {fid}")
        if fid in visited:
            return
        visiting.add(fid)
        for dep in features[fid]["depends_on"]:
            visit(dep)
        visiting.remove(fid)
        visited.add(fid)

    for fid in features:
        visit(fid)


def heading(title: str, description: str) -> str:
    """Create consistent Hugo frontmatter without introducing generated timestamps."""
    return f'---\ntitle: "{title}"\ndescription: "{description}"\n---\n\n<!-- Generated by scripts/features.py; edit capabilities/features.json. -->\n\n# {title}\n\n'


def catalog(data: dict) -> str:
    """Render every feature, status dimension, source location and acceptance reference."""
    out = heading("功能总表与验证矩阵", "稳定功能编号、代码入口、实施与验证及部署的独立状态。")
    out += f"登记版本：`{data['register_version']}`，更新日期：{data['updated_at']}。稳定编号不因改名复用。新增、拆分、弃用均在此登记，再生成文档。\n\n"
    out += "历史基线只证明登记的有限软件协议。上轮后端166项、浏览器206项和32路由巡检通过；当前代码含未提交成果，不能把基线HEAD当作这些成果的发布版本。所有真实模型与完整成片资格仍未通过。\n\n"
    out += "“仅本机”只记录本地入口存在；未提供可验收的本项目公网平台。合理需求、优先级、实现、验证、部署独立记录；PR合并不能替代上线验收。\n\n"
    if data.get("current_state"):
        out += data["current_state"]["overview"] + "\n\n" + data["current_state"]["verification_checkpoint"] + "\n\n"
    for group in data["groups"]:
        out += f"## {group['id']} {group['title']}\n\n{group['description']}\n\n"
        out += "| 编号 / 子功能 | 实现 | 软件验证 | 真实模型 | 部署 | 实际入口 |\n| --- | --- | --- | --- | --- | --- |\n"
        items = [row for row in data["features"] if row["group"] == group["id"]]
        for item in items:
            route = item["entry"]["route"] or "尚无入口"
            out += f"| [{item['id']} {cell(item['title'])}](#{item['id'].lower().replace('.', '-')}) | {label(item['implementation'])} | {label(item['validation']['software'])} | {label(item['validation']['model'])} | {label(item['deployment']['status'])} | {cell(route)} · {cell(item['entry']['label'])} |\n"
        out += "\n"
        for item in items:
            anchor = item["id"].lower().replace(".", "-")
            out += f'<a id="{anchor}"></a>\n\n### {item["id"]} {item["title"]}\n\n'
            scope = "本阶段交付" if item["delivery_scope"] == "current_delivery" else "长期扩展"
            out += f"需求：{', '.join(item['requirements'])}；验收阶段：{item['milestone']}；范围：{scope}；实现版本：`{item['version']['implementation'] or '未实现'}`；发布版本：`{item['version']['release'] or '未发布'}`。\n\n"
            if item["code"]:
                out += "代码：" + "、".join(f"[`{p}`](../../../../{p})" for p in item["code"]) + "。\n\n"
            out += "限制：" + "；".join(item["limits"]) + "。\n\n"
            out += "验证依据：" + ("、".join(f"`{v}`" for v in item["validation"]["evidence"]) or "尚无通过证据") + "。测试文件是待运行目标，文件存在不代表测试通过。\n\n"
            out += "需求/PR：" + ("、".join(item["issues"] + item["prs"]) or "尚未发布到用户目标仓库") + "。\n\n"
    out += "## 证据索引与公开边界\n\n| 证据编号 | 类型 | 可见性 / 位置 | 证明范围 |\n| --- | --- | --- | --- |\n"
    for ev in data["evidence"]:
        out += f"| {ev['id']} | {ev['kind']} | {ev['visibility']} · `{ev['location']}` | {cell(ev['scope'])} |\n"
    out += "\n私有外部证据保留在既有工作记录中，仓库中没有打包原片、密钥、私人数据库或专用账号凭据。公开发布前需新增脱敏证据，不可将这些工作记录路径当作公众可访问链接。\n\n## 需求索引\n\n"
    for req in data["requirements"]:
        out += f"- `{req['id']}`：{req['description']}\n"
    return out


def acceptance(data: dict) -> str:
    """Generate repeatable user-facing acceptance steps without executing paid actions."""
    out = heading("功能验收指南", "按稳定功能编号复验；区分软件夹具、真实模型和部署证据。")
    out += data["current_state"]["acceptance_setup"] + "不能用用户原片或既有私人项目充当公开示例。\n\n"
    out += "每次验收记录代码清单hash、数据库迁移版本、功能编号、操作/断言、软件或真实模型类别、结果、限制、证据位置。旧证据冻结，新阶段新建记录。涉及真实模型或云资源时先固定已授权额度；账本预留、已花费0、HTTP200、可解码素材均不等于免费或成片合格。\n\n"
    out += "基础登记检查：在仓库根目录执行 `python scripts/features.py --check`。API变更后执行 `pnpm run openapi:update`；前端变更执行 `pnpm exec tsc --noEmit`，并做实际浏览器验收。下列测试目标按变更选择运行，不要求无变化时重跑全部冻结测试。\n\n"
    for group in data["groups"]:
        out += f"## {group['id']} {group['title']}\n\n"
        for item in [row for row in data["features"] if row["group"] == group["id"]]:
            out += f"### {item['id']} {item['title']}\n\n入口：`{item['entry']['route'] or '尚无入口'}`，{item['entry']['label']}。当前：{label(item['implementation'])} / {label(item['validation']['software'])} / {label(item['deployment']['status'])}。\n\n"
            for index, step in enumerate(item["acceptance"], 1):
                out += f"{index}. {step}\n"
            out += "\n"
            if item["tests"]:
                out += "软件回归目标：" + "、".join(f"`{p}`" for p in item["tests"]) + "。\n\n"
            out += "验收限制：" + "；".join(item["limits"]) + "。\n\n"
    return out


def delivery(data: dict) -> str:
    """Generate the delivery gates from the same register used for feature claims."""
    out = heading("平台分阶段开发与验收计划", "A至F交付范围、依赖、验收门槛与剩余外部输入。")
    out += "Fawnimo 鹿帧是可自行部署的AI短视频工具，项目名和仓库名fawnimo已由用户确认。Jellyfish代码与来源保留；电影工作室A视觉及中性亮暗主题继续使用。分镜编辑负责准备、工作室负责生成、任务中心保持轻量。\n\n"
    out += "执行顺序：A建立基线和功能登记，B先形成可信身份与资源隔离，再完成C示例与账号。D软件缺口与E社区研发在各自隔离环境推进，F以真实目标仓库和稳定/预览部署联合验收收口。每阶段完成实现、适用测试、文档和独立审核后才通过；发现P1或越权问题不晋级。独立审核不能由开发执行器自己取消。\n\n"
    for stage in data["milestones"]:
        out += f"## {stage['id']} {stage['title']}\n\n状态：{label(stage['status'])}；依赖：{', '.join(stage['depends_on']) or '无'}。\n\n{stage['scope']}\n\n"
        if stage.get("progress"):
            out += "当前进展：" + stage["progress"] + "\n\n"
        out += "通过条件：\n\n"
        for step in stage["gates"]:
            out += f"- {step}\n"
        out += "\n证据交付：" + "；".join(stage["evidence_outputs"]) + "。\n\n"
        covered = [row["id"] for row in data["features"] if row["milestone"] == stage["id"] and row["delivery_scope"] == "current_delivery"]
        out += "功能范围：" + "、".join(covered) + "。\n\n"
    out += "## 外部输入与资源界限\n\n"
    for line in data["external_inputs"]:
        out += f"- {line}\n"
    out += "\n本地独立开发继续推进。未取得用户指定目标前，不向上游仓库推送、创建事项或发布；未授权的其他业务资源不能借用。真实模型调用与云资源开支需要单独费用上限。用户私人原片、个人资料、测试专用账号凭据不公开。\n"
    return out


def roadmap(data: dict) -> str:
    """Publish delivery priorities without inventing dates or treating accepted ideas as shipped."""
    out = heading("公开路线图", "近期交付与长期扩展分开记录，优先级不等于已经上线。")
    out += "产品方向：创意/脚本/参考视频 → 策划与分镜 → 资产、声音与提示词 → 图像/视频候选 → 确认采用 → 剪辑、字幕与声音 → 导出。中间结果可编辑、可追溯。短剧和参考视频拆解服务于短视频主线。\n\n"
    out += "需求合理性、优先级、实现、验证、部署分别记录。项目名称为Fawnimo 鹿帧，仓库名为fawnimo；仓库创建、代码发布与公网平台分别核验，尚无已发布的Issue/PR记录。上游链接仅用于来源说明。排期按依赖、价值、成本和容量推进，未设未经验证的日历承诺。\n\n## 近期交付\n\n| 阶段 | 目标 | 当前状态 |\n| --- | --- | --- |\n"
    for stage in data["milestones"]:
        out += f"| {stage['id']} | {cell(stage['title'])} | {label(stage['status'])} |\n"
    out += "\n完整门槛见[分阶段开发与验收计划](../platform-delivery/)，逐项状态见[功能总表](/docs/reference/feature-catalog/)。\n\n"
    if data.get("current_state"):
        out += data["current_state"]["overview"] + "\n\n"
    out += "## 长期扩展\n\n"
    for item in data["long_term"]:
        out += f"- **{item['title']}**：{item['scope']}；前置条件：{item['gate']}。\n"
    out += "\n## 社区需求的公开处理规则\n\n"
    for rule in data["community_policy"]:
        out += f"- {rule}\n"
    return out


def readme_block(data: dict) -> str:
    """Provide a short generated README entry that cannot drift from the catalog."""
    counts = Counter(row["implementation"] for row in data["features"])
    return (f"{BEGIN}\n## 当前开发与验收状态\n\n"
            f"本分支继续开发开源AI短视频制作工具，登记{len(data['groups'])}个功能区、{len(data['features'])}项子功能："
            f"{counts['implemented']}项已实现、{counts['partial']}项部分实现、{counts['planned']}项待实现。"
            "实现、软件验证、真实模型验证和部署分别记录，数量不表示全部验收通过。\n\n"
            "- [功能总表与代码/入口/验证矩阵](site/content/docs/reference/feature-catalog.md)\n"
            "- [逐项验收指南](site/content/docs/guide/feature-acceptance.md)\n"
            "- [A–F分阶段开发与验收计划](site/content/docs/plans/platform-delivery.md)\n"
            "- [公开路线图](site/content/docs/plans/public-roadmap.md)\n"
            "- [单一功能登记源](capabilities/features.json)，检查/生成：`python scripts/features.py --check` / `python scripts/features.py`\n\n"
            + data["current_state"]["readme_status"] +
            "本机地址、截图或上游镜像不能作为本项目公网部署。Docker/MySQL配置不等于迁移、并发与恢复已经验收。\n\n"
            "代码保留并基于[Forget-C/Jellyfish](https://github.com/Forget-C/Jellyfish)继续开发，遵循[现有许可证](LICENSE)，"
            "来源和发布边界见[UPSTREAM.md](UPSTREAM.md)。现有代码包含上游、既有改动和AI辅助开发成果，不能宣称全部从零AI原创。"
            "仓库里的上游Issues、Actions、Pages与镜像链接属于上游，不是已交付的自有平台。\n"
            f"{END}")


def outputs(data: dict) -> dict[Path, str]:
    """Render deterministic files and update only the owned README marker block."""
    readme = (ROOT / "README.md").read_text("utf-8")
    block = readme_block(data)
    if BEGIN in readme or END in readme:
        if readme.count(BEGIN) != 1 or readme.count(END) != 1:
            raise ValueError("README feature markers must appear exactly once")
        start, end = readme.index(BEGIN), readme.index(END) + len(END)
        if start >= end:
            raise ValueError("README feature markers are out of order")
        readme = readme[:start] + block + readme[end:]
    else:
        marker = "## 📷 Screenshots"
        if marker not in readme:
            raise ValueError("README insertion marker missing")
        readme = readme.replace(marker, block + "\n\n" + marker, 1)
    return {
        ROOT / "site/content/docs/reference/feature-catalog.md": catalog(data),
        ROOT / "site/content/docs/guide/feature-acceptance.md": acceptance(data),
        ROOT / "site/content/docs/plans/platform-delivery.md": delivery(data),
        ROOT / "site/content/docs/plans/public-roadmap.md": roadmap(data),
        ROOT / "README.md": readme,
    }


def main() -> int:
    """Check for stale outputs in CI, or regenerate all public feature views locally."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate and fail if generated files drift; do not write")
    args = parser.parse_args()
    try:
        data = json.loads(REGISTER.read_text("utf-8"))
        validate(data)
        stale = []
        for path, content in outputs(data).items():
            if args.check:
                if not path.is_file() or path.read_text("utf-8") != content:
                    stale.append(str(path.relative_to(ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
        if stale:
            raise ValueError("Generated feature views are stale: " + ", ".join(stale))
        print(json.dumps({"ok": True, "mode": "check" if args.check else "generate", "groups": len(data["groups"]), "features": len(data["features"]), "generated_views": 5}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Feature register failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
