"""
QA Agent System — Skill Upload API

用户级 skill 包上传 / 列表 / 删除接口。

接口：
  POST   /skills/upload          上传 zip skill 包（multipart）
  GET    /skills/user            列表当前用户上传的 skill（按 email 过滤）
  DELETE /skills/user/{name}     删除指定 skill（DB + 本地物化目录）

鉴权同 /sessions（get_current_user，Cookie session email）。
owner_email 取自鉴权上下文，非请求体传入。
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import frontmatter
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from auth.dependencies import get_current_user
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


# ── Response schemas ──────────────────────────────────────────────────────


class UserSkillInfo(BaseModel):
    """用户上传 skill 的元数据（不含 zip_data）。"""
    name: str
    agent: str
    description: str = ""
    paradigm: str = ""
    source: str = "user"
    owner_name: str = ""                           # 上传人显示名（owner_email @ 前段）
    uploaded_at: datetime


class UploadSkillResponse(BaseModel):
    """上传成功响应。"""
    name: str
    agent: str
    description: str = ""
    paradigm: str = ""
    source: str = "user"
    owner_name: str = ""                           # 上传人显示名（owner_email @ 前段）
    uploaded_at: datetime
    materialized: bool = True


# ── Helpers ───────────────────────────────────────────────────────────────


def _user_skills_dir() -> Path:
    """解析用户级 skill 物化目录（~/.qa-agent/skills/）。"""
    return Path(settings.user_skills_dir).expanduser()


def _email_to_name(email: str) -> str:
    """从 email 提取 @ 前段作显示名（如 zhangsan@x.com → zhangsan）。

    无 @ 或空 → 原样返回（如 "system"）。
    """
    if not email:
        return ""
    if "@" in email:
        return email.split("@", 1)[0]
    return email


def _builtin_skill_names() -> set[str]:
    """返回内置 skill 名集合（扫描 project_skills_dir 目录）。

    用于上传时校验 name 不与内置 skill 冲突。
    """
    import sys
    p = Path(settings.project_skills_dir)
    if not p.is_absolute():
        if getattr(sys, "frozen", False):
            p = Path(sys._MEIPASS) / p
    names: set[str] = set()
    if p.exists() and p.is_dir():
        for skill_dir in p.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                names.add(skill_dir.name)
    return names


def _valid_agent_names() -> set[str]:
    """返回合法 agent 标识集合（agent_id 与 name 双写，二者均可用于绑定）。"""
    try:
        from agents.registry import agent_registry
        names: set[str] = set()
        for d in agent_registry.all():
            names.add(d.agent_id or d.name)
            names.add(d.name)
        return names
    except Exception:
        logger.warning("无法读取 agent_registry，agent 校验跳过")
        return set()


def _resolve_skill_agent(fm_agent: str) -> str:
    """确定上传 skill 绑定的 agent。

    frontmatter 声明了 `agent` → 必须是已注册 agent（否则落库即悬空绑定）；
    未声明 → 绑定首个注册 agent（注册顺序即装配顺序，首个为默认入口 agent）。
    """
    valid = _valid_agent_names()
    if fm_agent:
        if valid and fm_agent not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"skill 声明的 agent '{fm_agent}' 未注册；可用：{sorted(valid)}",
            )
        return fm_agent
    if not valid:
        raise HTTPException(status_code=503, detail="agent 注册表为空，无法绑定 skill")
    from agents.registry import agent_registry
    first = agent_registry.all()[0]
    return first.agent_id or first.name


def _parse_zip_skill_md(zip_data: bytes) -> tuple[str, str, str, str, dict, bytes]:
    """从 zip 中解析 SKILL.md frontmatter。

    兼容两种 zip 结构：
    - 结构 A（压目录）：my-skill/SKILL.md, my-skill/scripts/x.py
    - 结构 B（内部全选）：SKILL.md, scripts/x.py

    自动检测结构，剥掉一级目录前缀后定位 SKILL.md。

        Returns:
            (name, description, agent, paradigm, triggers, zip_data)。
            agent 缺失时返回空串（由调用方回填默认 agent）。
            paradigm/triggers 可选（缺失时返回空值，落库默认空）——skill 激活走
            activate_skills 直选，这两字段不参与任何激活/匹配路径，仅解析留档。
    """
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(zip_data))
    names = zf.namelist()

    # 检测 zip 结构（复用 materializer 的逻辑）
    from skills.materializer import _detect_skill_prefix, _strip_prefix
    prefix = _detect_skill_prefix(names)

    # 定位 SKILL.md（剥 prefix 后为根级）
    skill_md_entry = None
    for n in names:
        rel = _strip_prefix(n, prefix).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel == "SKILL.md":
            skill_md_entry = n
            break
    if skill_md_entry is None:
        raise ValueError("zip 必须含根级 SKILL.md（直接在根目录或唯一一级子目录下）")

    content = zf.read(skill_md_entry).decode("utf-8")
    post = frontmatter.loads(content)
    metadata = post.metadata

    description = (metadata.get("description") or "").strip()
    if not description:
        raise ValueError("SKILL.md frontmatter description 必填")

    agent = (metadata.get("agent") or "").strip()
    name = (metadata.get("name") or "").strip()

    # paradigm + triggers 可选解析（填了就存 DB，不填留空；不参与激活路径）
    meta = metadata.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    paradigm = (meta.get("paradigm") or "").strip() if isinstance(meta.get("paradigm"), str) else ""
    raw_triggers = meta.get("triggers")
    triggers = raw_triggers if isinstance(raw_triggers, dict) else {}

    return name, description, agent, paradigm, triggers, zip_data


def _backfill_agent_to_zip(zip_data: bytes, agent_id: str) -> bytes:
    """把 agent 字段回填到 zip 内 SKILL.md frontmatter，返回新 zip 字节。

    兼容两种 zip 结构（自动检测 prefix，按剥 prefix 后的根级 SKILL.md 定位）。
    """
    import zipfile
    from skills.materializer import _detect_skill_prefix, _strip_prefix

    zf_in = zipfile.ZipFile(io.BytesIO(zip_data))
    names = zf_in.namelist()
    prefix = _detect_skill_prefix(names)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf_out:
        for item in zf_in.infolist():
            data = zf_in.read(item.filename)
            rel = _strip_prefix(item.filename, prefix).replace("\\", "/")
            while rel.startswith("./"):
                rel = rel[2:]
            if rel == "SKILL.md":
                content = data.decode("utf-8")
                post = frontmatter.loads(content)
                post.metadata["agent"] = agent_id
                data = frontmatter.dumps(post).encode("utf-8")
            zf_out.writestr(item, data)
    return buf.getvalue()


def _to_info(doc) -> dict[str, Any]:
    """序列化 UserSkill 文档为响应 dict。"""
    return {
        "name": doc.name,
        "agent": doc.agent,
        "description": doc.description,
        "paradigm": doc.paradigm,
        "source": doc.source,
        "owner_name": _email_to_name(getattr(doc, "owner_email", "") or ""),
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadSkillResponse)
async def upload_skill(
    request: Request,
    file: UploadFile = File(..., description="skill zip 包（须含 description，metadata.paradigm / metadata.triggers 可选）"),
    description: Optional[str] = Form(default=None, description="可选描述覆盖前端默认值"),
    current_user: str | None = Depends(get_current_user),
) -> dict[str, Any]:
    """上传用户 skill zip 包。

    绑定的 agent 取自 frontmatter 的 `agent` 字段（必须是已注册 agent）；
    未声明则回填默认 agent（见 `_resolve_skill_agent`）。

    流程：读 zip → 校验 frontmatter（description 必填，metadata.paradigm / metadata.triggers 可选）→
    解析/回填 agent → 校验 name 不与内置冲突 → 物化 → 落 DB → 返回。
    同名重传覆盖（DB upsert + 物化目录覆盖）。
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    zip_data = await file.read()

    # 解析 frontmatter（description 必填；paradigm/triggers 可选，填了就存 DB）
    try:
        skill_name, fm_description, fm_agent, _paradigm, _triggers, _ = _parse_zip_skill_md(zip_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # skill name：frontmatter name 优先，否则用 zip 文件名去后缀
    if not skill_name:
        zip_filename = Path(file.filename or "").stem
        skill_name = zip_filename

    if not skill_name:
        raise HTTPException(status_code=400, detail="无法确定 skill name（frontmatter name 与文件名均空）")

    # agent：frontmatter 声明则必须已注册；未声明则回填默认 agent 并写回 zip
    agent_id = _resolve_skill_agent(fm_agent)
    if not fm_agent:
        zip_data = _backfill_agent_to_zip(zip_data, agent_id)

    # 内置 skill 冲突校验
    if skill_name in _builtin_skill_names():
        raise HTTPException(
            status_code=409,
            detail=f"skill name '{skill_name}' 与内置 skill 冲突",
        )

    # 跨用户同名拦截：loader 缓存 _skills 是全局扁平 dict(name→skill)，
    # 同名不同 owner 会互相覆盖（DB 允许两记录共存，但内存缓存只能存一个）。
    # 仅允许同 owner 重传覆盖，异 owner 同名直接拒绝。
    from db.models import UserSkill as _USkill
    try:
        _same_name = await _USkill.find(_USkill.name == skill_name).to_list()
    except Exception as e:
        logger.warning("upload_skill: 同名 DB 查询失败（放行）：%s", e, exc_info=True)
        _same_name = []
    if any(d.owner_email != current_user for d in _same_name):
        raise HTTPException(
            status_code=409,
            detail=f"skill name '{skill_name}' 已被其他用户占用，请更换名称",
        )

    # 物化
    from skills.materializer import materialize_user_skill
    target_dir = _user_skills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        materialize_user_skill(zip_data, skill_name, target_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"物化失败：{e}")

    # 落 DB（upsert，同名覆盖）
    from db.models import UserSkill
    final_description = description if description is not None else fm_description
    now = datetime.utcnow()
    doc_id = f"{current_user}:{skill_name}"

    existing = await UserSkill.find_one(UserSkill.id == doc_id)
    if existing:
        existing.zip_data = zip_data
        existing.agent = agent_id
        existing.description = final_description
        existing.paradigm = _paradigm
        existing.uploaded_at = now
        await existing.save()
        doc = existing
        logger.info("用户 skill 覆盖更新：%s/%s", current_user, skill_name)
    else:
        doc = UserSkill(
            id=doc_id,
            owner_email=current_user,
            name=skill_name,
            zip_data=zip_data,
            agent=agent_id,
            description=final_description,
            paradigm=_paradigm,
            source="user",
            uploaded_at=now,
        )
        await doc.insert()
        logger.info("用户 skill 新建：%s/%s", current_user, skill_name)

    # ── 刷新内存 loader 缓存（上传后立即生效，无需重启服务）──────────────
    # app.state.skill_loader._skills 是扁平 dict；解析物化目录并写入即可，
    # 使 activate_skills 下次调用能立即解析到该 skill。
    try:
        loader = getattr(request.app.state, "skill_loader", None)
        if loader is not None:
            skill_dir = target_dir / skill_name
            parsed = loader._parse_skill(skill_dir, skill_dir / "SKILL.md")
            if parsed is not None:
                loader._skills[parsed.name] = parsed
                logger.info("upload_skill: loader 缓存已刷新 %s/%s", current_user, skill_name)
            else:
                logger.warning("upload_skill: 物化后解析失败，未刷新缓存 %s/%s", current_user, skill_name)
    except Exception as e:
        logger.warning("upload_skill: loader 缓存刷新失败（不阻塞）：%s", e, exc_info=True)

    return {
        "name": doc.name,
        "agent": doc.agent,
        "description": doc.description,
        "paradigm": doc.paradigm,
        "source": doc.source,
        "owner_name": _email_to_name(doc.owner_email or ""),
        "uploaded_at": doc.uploaded_at,
        "materialized": True,
    }


@router.get("/user", response_model=list[UserSkillInfo])
async def list_user_skills(
    current_user: str | None = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """列表当前用户上传的 skill（按 email 过滤，uploaded_at 倒序）。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    from db.models import UserSkill

    docs = await UserSkill.find(
        UserSkill.owner_email == current_user
    ).sort(-UserSkill.uploaded_at).to_list()
    return [_to_info(d) for d in docs]


@router.delete("/user/{name}", status_code=204, response_model=None)
async def delete_user_skill(
    request: Request,
    name: str,
    current_user: str | None = Depends(get_current_user),
) -> None:
    """删除指定 skill（DB 记录 + 本地物化目录）。

    owner_email 不匹配返回 404（不泄露存在性）。物化目录删除失败 warn 不阻塞。
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    from db.models import UserSkill
    import shutil

    doc = await UserSkill.find_one(
        UserSkill.owner_email == current_user,
        UserSkill.name == name,
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    await doc.delete()

    # 删本地物化目录（失败 warn 不阻塞）
    skill_dir = _user_skills_dir() / name
    if skill_dir.exists():
        try:
            shutil.rmtree(skill_dir, ignore_errors=True)
            logger.info("已删除物化目录：%s", skill_dir)
        except Exception as e:
            logger.warning("删除物化目录失败（不阻塞）：%s err=%s", skill_dir, e, exc_info=True)

    # 清除内存 loader 缓存中的该 skill，避免 stale 条目继续可解析
    try:
        loader = getattr(request.app.state, "skill_loader", None)
        if loader is not None:
            loader._skills.pop(name, None)
    except Exception as e:
        logger.warning("清除 loader 缓存失败（不阻塞）：%s err=%s", name, e, exc_info=True)

    logger.info("用户 skill 删除：%s/%s", current_user, name)
    return None
