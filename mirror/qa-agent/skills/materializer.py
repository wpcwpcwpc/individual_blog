"""
QA Agent System — Skill Materializer

将用户上传的 skill zip 包校验并物化到本地 ~/.qa-agent/skills/<name>/ 目录，
供 SkillLoader 扫描加载。DB 是用户 skill 真相源，物化是适配层。

校验规则（任一失败抛 ValueError，MUST NOT 写盘）：
- 必含根级 SKILL.md
- 路径穿越拒绝（resolve 后必须以目标目录为前缀）
- 后缀黑名单：.pyd / .exe / .dll / .so / .dylib
- 单文件 5MB / 整包 10MB 上限
"""

from __future__ import annotations

import io
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


# ── 常量 ──────────────────────────────────────────────────────────────────

_BLOCKED_SUFFIXES = {".pyd", ".exe", ".dll", ".so", ".dylib"}
_MAX_SINGLE_FILE_BYTES = 5 * 1024 * 1024   # 5MB
_MAX_TOTAL_BYTES = 10 * 1024 * 1024        # 10MB


# ── 物化 ──────────────────────────────────────────────────────────────────

def materialize_user_skill(zip_data: bytes, name: str, target_dir: Path) -> Path:
    """校验 zip 并物化到 target_dir/<name>/ 目录（覆盖式）。

    Args:
        zip_data: 原始 zip 字节。
        name: skill 名（目录名）。
        target_dir: 父目录（如 ~/.qa-agent/skills/），skill 物化到 target_dir/<name>/。

    Returns:
        物化后的 skill 目录 Path。

    Raises:
        ValueError: 校验失败（缺 SKILL.md / 路径穿越 / 黑名单后缀 / 超限）。
    """
    skill_dir = (target_dir / name).resolve()
    target_dir_resolved = target_dir.resolve()

    # 解析 zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"无效 zip 文件：{e}") from e

    names = zf.namelist()

    # ── 检测 zip 结构：兼容两种压缩方式 ────────────────────────────────
    # 结构 A（压目录）：my-skill/SKILL.md, my-skill/scripts/x.py → prefix='my-skill/'
    # 结构 B（内部全选）：SKILL.md, scripts/x.py → prefix=''
    # 判定：若所有非目录条目共享同一一级目录前缀，且该前缀目录下含 SKILL.md，视为结构 A
    prefix = _detect_skill_prefix(names)

    # ── 校验 1: 必含根级 SKILL.md（剥 prefix 后） ─────────────────────
    has_root_skill_md = any(
        _is_root_skill_md(_strip_prefix(n, prefix)) for n in names
    )
    if not has_root_skill_md:
        raise ValueError("zip 必须含根级 SKILL.md（直接在根目录或唯一一级子目录下）")

    # ── 校验 2: 路径穿越 + 后缀黑名单 + 大小（对剥 prefix 后的路径校验）──
    total_size = 0
    for n in names:
        if n.endswith("/"):
            continue  # 目录条目跳过

        rel = _strip_prefix(n, prefix)

        # 路径穿越：拼到 skill_dir 后 resolve，必须仍以 skill_dir 为前缀
        candidate = (skill_dir / rel).resolve()
        try:
            candidate.relative_to(skill_dir)
        except ValueError as e:
            raise ValueError(f"检测到路径穿越：{n}") from e

        # 绝对路径（zip 内以 / 或盘符开头）也拒绝
        if Path(rel).is_absolute():
            raise ValueError(f"检测到绝对路径：{n}")

        # 后缀黑名单
        suffix = Path(rel).suffix.lower()
        if suffix in _BLOCKED_SUFFIXES:
            raise ValueError(f"禁用文件后缀：{suffix}")

        # 单文件大小
        info = zf.getinfo(n)
        if info.file_size > _MAX_SINGLE_FILE_BYTES:
            raise ValueError(
                f"单文件超限（{info.file_size} > {_MAX_SINGLE_FILE_BYTES}）：{rel}"
            )
        total_size += info.file_size

    if total_size > _MAX_TOTAL_BYTES:
        raise ValueError(
            f"skill 包总大小超限（{total_size} > {_MAX_TOTAL_BYTES}）"
        )

    # ── 校验通过，覆盖物化（剥 prefix 后解压） ────────────────────────
    # 先清空已有同名目录（避免旧文件残留），再重建
    if skill_dir.exists():
        shutil.rmtree(skill_dir, ignore_errors=True)
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 逐条解压，剥掉 prefix 目录（结构 A 时去掉 my-skill/ 前缀）
    for n in names:
        if n.endswith("/"):
            continue
        rel = _strip_prefix(n, prefix)
        if not rel:
            continue
        target_path = skill_dir / rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(n) as src, open(target_path, "wb") as dst:
            dst.write(src.read())

    logger.info("物化 skill '%s' 到 %s（%d 文件，%d 字节，prefix=%r）",
                name, skill_dir, len(names), total_size, prefix)
    return skill_dir


def _detect_skill_prefix(names: list[str]) -> str:
    """检测 zip 结构，返回 skill 内容的公共前缀目录。

    兼容两种压缩方式：
    - 结构 A（压目录）：my-skill/SKILL.md, my-skill/scripts/x.py → 返回 'my-skill/'
    - 结构 B（内部全选）：SKILL.md, scripts/x.py → 返回 ''

    判定规则：若所有非目录条目的第一段路径相同（且仅有一级），且该目录下含 SKILL.md，
    视为结构 A，返回该一级目录名 + '/'。否则返回 ''（结构 B）。
    """
    # 收集所有非目录条目的第一段
    first_segments: set[str] = set()
    for n in names:
        normalized = n.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized or normalized.endswith("/"):
            continue
        parts = normalized.split("/", 1)
        if len(parts) == 2:
            first_segments.add(parts[0] + "/")
        else:
            # 有根级文件（无目录前缀）→ 结构 B
            return ""
    if len(first_segments) != 1:
        # 多个一级目录或混合 → 结构 B（直接物化）
        return ""
    prefix = first_segments.pop()
    # 校验该 prefix 目录下含 SKILL.md
    has_skill_md = any(
        _normalize(n) == prefix + "SKILL.md"
        for n in names
    )
    return prefix if has_skill_md else ""


def _normalize(name: str) -> str:
    """标准化 zip 条目名：反斜杠转正斜杠，剥前导 ./（不剥 ..）。"""
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _strip_prefix(name: str, prefix: str) -> str:
    """剥掉 zip 条目的公共前缀目录（结构 A），结构 B 时原样返回。

    仅剥前导 `./`（单字符），不剥 `..`（避免路径穿越校验失效）。
    """
    normalized = name.replace("\\", "/")
    # 仅剥前导 "./"（不剥 ".."）
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if prefix and normalized.startswith(prefix):
        return normalized[len(prefix):]
    return normalized


def _is_root_skill_md(entry_name: str) -> bool:
    """判断（已剥 prefix 的）条目是否为根级 SKILL.md。"""
    normalized = _normalize(entry_name)
    return normalized == "SKILL.md"


# ── 启动同步 ──────────────────────────────────────────────────────────────

async def sync_user_skills_from_db(
    db,
    current_email: str | None,
    target_dir: Path,
) -> Tuple[int, int]:
    """启动期从 DB 同步用户 skill 到本地物化目录。

    按 current_email 过滤 user_skills，对本地缺失或过期的 skill 调
    materialize_user_skill 物化。单条失败 catch + warn，不阻塞其他。

    Args:
        db: Motor database 对象（qa_agent_db）。
        current_email: 当前用户 email；None 时跳过同步。
        target_dir: 物化父目录（~/.qa-agent/skills/）。

    Returns:
        (物化数, 跳过数)。
    """
    if current_email is None:
        logger.info("current_user_email 未配置，跳过 user_skills 启动同步")
        return (0, 0)

    coll = db["user_skills"]
    cursor = coll.find({"owner_email": current_email})
    docs = await cursor.to_list(length=None)

    materialized = 0
    skipped = 0
    for doc in docs:
        skill_name = doc.get("name")
        zip_data = doc.get("zip_data")
        uploaded_at = doc.get("uploaded_at")
        if not skill_name or not zip_data:
            logger.warning("user_skills 记录字段缺失，跳过：%s", doc.get("_id"))
            skipped += 1
            continue

        skill_md = target_dir / skill_name / "SKILL.md"
        try:
            if skill_md.exists():
                local_mtime = skill_md.stat().st_mtime
                db_time = _to_epoch(uploaded_at)
                if db_time is not None and local_mtime >= db_time:
                    skipped += 1
                    continue

            materialize_user_skill(zip_data, skill_name, target_dir)
            materialized += 1
        except Exception as e:
            logger.warning(
                "物化 skill '%s' 失败：%s", skill_name, e, exc_info=True,
            )
            skipped += 1

    logger.info(
        "user_skills 启动同步完成：%d 物化, %d 跳过（email=%s）",
        materialized, skipped, current_email,
    )
    return (materialized, skipped)


def _to_epoch(uploaded_at) -> float | None:
    """把 datetime / float / int 转 epoch 秒。"""
    import datetime as _dt
    if isinstance(uploaded_at, (int, float)):
        return float(uploaded_at)
    if isinstance(uploaded_at, _dt.datetime):
        return uploaded_at.timestamp()
    return None


# ── 运行时 DB fallback ────────────────────────────────────────────────────

async def load_skill_from_db(
    db,
    owner_email: str,
    name: str,
    skill_loader,
    target_dir: Path,
):
    """运行时 load_skill 本地 miss 后的 DB fallback。

    查 user_skills by (owner_email, name) → 命中调 materialize_user_skill 物化 →
    调 SkillLoader._parse_skill 解析 → 缓存进 SkillLoader._skills → 返回 SkillDefinition。

    Args:
        db: Motor database 对象。
        owner_email: 当前用户 email。
        name: skill 名。
        skill_loader: SkillLoader 实例（用于 _parse_skill 与 _skills 缓存）。
        target_dir: 物化父目录。

    Returns:
        SkillDefinition 或 None（未命中 / 物化失败）。
    """
    coll = db["user_skills"]
    doc = await coll.find_one({"owner_email": owner_email, "name": name})
    if doc is None:
        return None

    zip_data = doc.get("zip_data")
    if not zip_data:
        logger.warning("user_skills 记录 zip_data 为空：%s/%s", owner_email, name)
        return None

    try:
        materialize_user_skill(zip_data, name, target_dir)
    except Exception as e:
        logger.warning(
            "load_skill DB fallback 物化失败（%s/%s）：%s",
            owner_email, name, e, exc_info=True,
        )
        return None

    # 解析物化后的目录并缓存进 SkillLoader
    skill_dir = (target_dir / name).resolve()
    skill_md = skill_dir / "SKILL.md"
    skill = skill_loader._parse_skill(skill_dir, skill_md)
    if skill is None:
        logger.warning("物化后解析 SKILL.md 失败：%s/%s", owner_email, name)
        return None

    skill_loader._skills[skill.name] = skill
    logger.info("load_skill DB fallback 成功物化并加载：%s", skill.name)
    return skill


async def find_user_skills_from_db(
    db,
    owner_email: str,
    query: str,
    top_k: int = 5,
):
    """find_skill 搜索范围扩 DB：从 user_skills 构造临时 SkillDefinition 参与排序。

    仅返回元数据（name/description/when_to_use/agent/load_mode），不含 prompt_template。
    被 load_skill 加载时走 load_skill_from_db fallback 物化。

    Args:
        db: Motor database 对象。
        owner_email: 当前用户 email。
        query: 搜索关键词。
        top_k: 最多返回数。

    Returns:
        List[dict]（每项含 name/description/when_to_use/agent/load_mode）。
    """
    coll = db["user_skills"]
    cursor = coll.find(
        {"owner_email": owner_email},
        {"name": 1, "agent": 1, "description": 1, "_id": 0},
    )
    docs = await cursor.to_list(length=None)

    query_lower = query.lower().strip() if query else ""
    scored: list[tuple[int, dict]] = []
    for d in docs:
        name = d.get("name", "")
        desc = d.get("description", "")
        score = 0
        if query_lower:
            if query_lower in name.lower():
                score += 10
            if query_lower in desc.lower():
                score += 5
        else:
            score = 1
        if score > 0:
            scored.append((score, {
                "name": name,
                "description": desc,
                "when_to_use": "",
                "args": [],
                "load_mode": "on-demand",
                "agent": d.get("agent", ""),
            }))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:top_k]]
