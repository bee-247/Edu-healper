import json
import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from agent import chat_with_agent, chat_with_agent_stream, storage
from auth import authenticate_user, create_access_token, get_current_user, get_db, get_password_hash, resolve_role
from database import SessionLocal
from document_loader import DocumentLoader
from embedding import embedding_service
from education.knowledge_extractor import knowledge_extractor
from education.objective_answer_extractor import (
    extract_answer_rules_from_image,
    extract_answer_rules_from_text,
    extract_objective_answers_from_image,
    extract_objective_answers_from_text,
)
from education.objective_grader import ObjectiveAnswerRule, grade_objective_answers
from education.objective_grading_report import build_objective_grading_report
from education.task_service import generate_grading, generate_lesson, generate_question_set
from milvus_client import MilvusManager
from milvus_writer import MilvusWriter
from knowledge_graph_builder import knowledge_graph_builder
from models import Resource, TeacherArtifact, User
from parent_chunk_store import ParentChunkStore
from schemas import (
    AuthResponse,
    ChatRequest,
    ChatResponse,
    CurrentUserResponse,
    DocumentDeleteJobResponse,
    DocumentDeleteResponse,
    DocumentDeleteStartResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUploadJobResponse,
    DocumentUploadResponse,
    DocumentUploadStartResponse,
    GenerateGradingReferenceRequest,
    GenerateLessonPlanRequest,
    ObjectiveGradingRequest,
    ObjectiveGradingResponse,
    GenerateQuestionsRequest,
    LoginRequest,
    MessageInfo,
    RegisterRequest,
    SessionDeleteResponse,
    SessionInfo,
    SessionListResponse,
    SessionMessagesResponse,
    TeacherArtifactCreate,
    TeacherArtifactInfo,
    TeacherArtifactListResponse,
    TeacherArtifactUpdate,
    TeacherTaskResponse,
)
from upload_jobs import DELETE_STEPS, delete_job_manager, upload_job_manager

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "documents"

loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
milvus_manager = MilvusManager()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)

router = APIRouter()


def _remove_bm25_stats_for_filename(filename: str) -> None:
    """删除 Milvus 中该文件对应 chunk 前，先从持久化 BM25 统计中扣减。"""
    rows = milvus_manager.query_all(
        filter_expr=f'filename == "{filename}"',
        output_fields=["text"],
    )
    texts = [r.get("text") or "" for r in rows]
    embedding_service.increment_remove_documents(texts)


def _normalize_resource_metadata(
    *,
    subject: str = "",
    grade: str = "",
    book_version: str = "",
    resource_type: str = "textbook",
    section_title: str = "",
    knowledge_tags: str | list[str] | None = None,
) -> dict:
    tags: list[str] = []
    if isinstance(knowledge_tags, str):
        tags = [item.strip() for item in re.split(r"[,，、\n]", knowledge_tags) if item.strip()]
    elif isinstance(knowledge_tags, list):
        tags = [str(item).strip() for item in knowledge_tags if str(item).strip()]

    return {
        "subject": (subject or "").strip(),
        "grade": (grade or "").strip(),
        "book_version": (book_version or "").strip(),
        "resource_type": (resource_type or "textbook").strip() or "textbook",
        "section_title": (section_title or "").strip(),
        "knowledge_tags": tags,
    }


def _scoped_filename(filename: str, current_user: User) -> str:
    clean = filename.strip()
    if current_user.role == "admin":
        return clean
    return f"user_{current_user.id}__{clean}"


def _display_filename(resource: Resource) -> str:
    metadata = resource.metadata_json or {}
    return metadata.get("original_filename") or resource.filename


def _assert_can_modify_resource(db: Session, filename: str, current_user: User) -> Resource:
    resource = db.query(Resource).filter(Resource.filename == filename).first()
    if not resource:
        raise HTTPException(status_code=404, detail="文档不存在")
    if current_user.role != "admin" and resource.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能删除自己上传的个人资料")
    return resource


def _detect_file_type(filename: str) -> str:
    file_lower = filename.lower()
    if file_lower.endswith(".pdf"):
        return "PDF"
    if file_lower.endswith((".docx", ".doc")):
        return "Word"
    if file_lower.endswith((".xlsx", ".xls")):
        return "Excel"
    return ""


def _upsert_resource(
    db: Session,
    *,
    filename: str,
    file_path: str,
    metadata: dict,
    owner_id: int | None = None,
    visibility: str = "public",
    status: str = "processing",
    chunk_count: int = 0,
) -> Resource:
    resource = db.query(Resource).filter(Resource.filename == filename).first()
    payload = {
        "source_file": file_path,
        "owner_id": owner_id,
        "visibility": visibility,
        "file_type": _detect_file_type(filename),
        "subject": metadata.get("subject", ""),
        "grade": metadata.get("grade", ""),
        "book_version": metadata.get("book_version", ""),
        "resource_type": metadata.get("resource_type", "textbook"),
        "status": status,
        "chunk_count": chunk_count,
        "updated_at": datetime.utcnow(),
        "metadata_json": {
            "original_filename": metadata.get("original_filename", filename),
            "section_title": metadata.get("section_title", ""),
            "knowledge_tags": metadata.get("knowledge_tags", []),
        },
    }
    if resource:
        for key, value in payload.items():
            setattr(resource, key, value)
    else:
        resource = Resource(filename=filename, **payload)
        db.add(resource)
    db.commit()
    db.refresh(resource)
    return resource


def _update_resource_status(resource_id: int | None, *, status: str, chunk_count: int | None = None) -> None:
    if not resource_id:
        return
    db = SessionLocal()
    try:
        resource = db.query(Resource).filter(Resource.id == resource_id).first()
        if not resource:
            return
        resource.status = status
        resource.updated_at = datetime.utcnow()
        if chunk_count is not None:
            resource.chunk_count = chunk_count
        db.commit()
    finally:
        db.close()


def _resource_graph_payload(resource: Resource | None, *, filename: str = "", file_path: str = "", metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    return {
        "resource_id": resource.id if resource else metadata.get("resource_id"),
        "filename": resource.filename if resource else filename,
        "owner_id": resource.owner_id if resource else metadata.get("owner_id"),
        "visibility": resource.visibility if resource else metadata.get("visibility", "public"),
        "subject": resource.subject if resource else metadata.get("subject", ""),
        "grade": resource.grade if resource else metadata.get("grade", ""),
        "book_version": resource.book_version if resource else metadata.get("book_version", ""),
        "resource_type": resource.resource_type if resource else metadata.get("resource_type", "textbook"),
        "source_file": resource.source_file if resource else file_path,
    }


def _sync_documents_to_neo4j(resource: Resource, docs: list[dict]) -> dict:
    resource_payload = _resource_graph_payload(resource)
    extracted_payload = knowledge_extractor.extract_payload(resource_payload, docs)
    if extracted_payload.get("nodes"):
        counts = knowledge_graph_builder.build_from_payload(extracted_payload)
        counts["extraction_mode"] = "llm"
        return counts

    leaf_docs = [doc for doc in docs if int(doc.get("chunk_level", 0) or 0) == 3]
    graph_docs = leaf_docs or docs
    counts = knowledge_graph_builder.build_minimal_graph_from_documents(resource_payload, graph_docs)
    counts["extraction_mode"] = "metadata_fallback"
    return counts


def _delete_resource_from_neo4j(filename: str) -> None:
    knowledge_graph_builder.delete_resource_by_filename(filename)


@router.post("/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    username = (request.username or "").strip()
    password = (request.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(status_code=409, detail="用户名已存在")

    role = resolve_role(request.role, request.admin_code)
    user = User(username=username, password_hash=get_password_hash(password), role=role)
    db.add(user)
    db.commit()

    token = create_access_token(username=username, role=role)
    return AuthResponse(access_token=token, username=username, role=role)


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(username=user.username, role=user.role)
    return AuthResponse(access_token=token, username=user.username, role=user.role)


@router.get("/auth/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return CurrentUserResponse(username=current_user.username, role=current_user.role)


@router.get("/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str, current_user: User = Depends(get_current_user)):
    """获取指定会话的所有消息"""
    try:
        messages = [
            MessageInfo(
                type=msg["type"],
                content=msg["content"],
                timestamp=msg["timestamp"],
                rag_trace=msg.get("rag_trace"),
            )
            for msg in storage.get_session_messages(current_user.username, session_id)
        ]
        return SessionMessagesResponse(messages=messages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有会话列表"""
    try:
        sessions = [SessionInfo(**item) for item in storage.list_session_infos(current_user.username)]
        sessions.sort(key=lambda x: x.updated_at, reverse=True)
        return SessionListResponse(sessions=sessions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    """删除当前用户的指定会话"""
    try:
        deleted = storage.delete_session(current_user.username, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")
        return SessionDeleteResponse(session_id=session_id, message="成功删除会话")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _artifact_info(artifact: TeacherArtifact) -> TeacherArtifactInfo:
    return TeacherArtifactInfo(
        id=artifact.id,
        artifact_type=artifact.artifact_type,
        title=artifact.title,
        prompt=artifact.prompt,
        content_json=artifact.content_json or {},
        source_chunk_ids=artifact.source_chunk_ids or [],
        created_at=artifact.created_at.isoformat(),
        updated_at=artifact.updated_at.isoformat(),
    )


@router.get("/teacher/artifacts", response_model=TeacherArtifactListResponse)
async def list_teacher_artifacts(
    artifact_type: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(TeacherArtifact).filter(TeacherArtifact.teacher_id == current_user.id)
    if artifact_type:
        query = query.filter(TeacherArtifact.artifact_type == artifact_type)
    rows = query.order_by(TeacherArtifact.updated_at.desc()).limit(100).all()
    return TeacherArtifactListResponse(artifacts=[_artifact_info(row) for row in rows])


@router.post("/teacher/artifacts", response_model=TeacherArtifactInfo)
async def create_teacher_artifact(
    request: TeacherArtifactCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    title = request.title.strip()
    artifact_type = request.artifact_type.strip()
    if not title or not artifact_type:
        raise HTTPException(status_code=400, detail="材料标题和类型不能为空")
    artifact = TeacherArtifact(
        teacher_id=current_user.id,
        artifact_type=artifact_type,
        title=title,
        prompt=request.prompt or "",
        content_json=request.content_json or {},
        source_chunk_ids=request.source_chunk_ids or [],
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return _artifact_info(artifact)


@router.patch("/teacher/artifacts/{artifact_id}", response_model=TeacherArtifactInfo)
async def update_teacher_artifact(
    artifact_id: int,
    request: TeacherArtifactUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    artifact = (
        db.query(TeacherArtifact)
        .filter(TeacherArtifact.id == artifact_id, TeacherArtifact.teacher_id == current_user.id)
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="生成材料不存在")
    for key in ("artifact_type", "title", "prompt", "content_json", "source_chunk_ids"):
        value = getattr(request, key)
        if value is not None:
            setattr(artifact, key, value.strip() if isinstance(value, str) else value)
    artifact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(artifact)
    return _artifact_info(artifact)


@router.delete("/teacher/artifacts/{artifact_id}")
async def delete_teacher_artifact(
    artifact_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    artifact = (
        db.query(TeacherArtifact)
        .filter(TeacherArtifact.id == artifact_id, TeacherArtifact.teacher_id == current_user.id)
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="生成材料不存在")
    db.delete(artifact)
    db.commit()
    return {"id": artifact_id, "message": "生成材料已删除"}


@router.post("/teacher/questions/generate", response_model=TeacherTaskResponse)
async def generate_teacher_questions(
    request: GenerateQuestionsRequest,
    current_user: User = Depends(get_current_user),
):
    if not request.knowledge_topic.strip():
        raise HTTPException(status_code=400, detail="knowledge_topic 不能为空")
    try:
        return TeacherTaskResponse(**generate_question_set(request, current_user.username))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成题目失败: {e}")


@router.post("/teacher/lesson-plans/generate", response_model=TeacherTaskResponse)
async def generate_teacher_lesson_plan(
    request: GenerateLessonPlanRequest,
    current_user: User = Depends(get_current_user),
):
    if not request.teaching_topic.strip():
        raise HTTPException(status_code=400, detail="teaching_topic 不能为空")
    try:
        return TeacherTaskResponse(**generate_lesson(request, current_user.username))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成教案失败: {e}")


@router.post("/teacher/grading/generate", response_model=TeacherTaskResponse)
async def generate_teacher_grading_reference(
    request: GenerateGradingReferenceRequest,
    current_user: User = Depends(get_current_user),
):
    if not request.question.strip() or not request.student_answer.strip():
        raise HTTPException(status_code=400, detail="question 和 student_answer 不能为空")
    try:
        return TeacherTaskResponse(**generate_grading(request, current_user.username))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成批改参考失败: {e}")


def _save_objective_grading_artifact(
    *,
    current_user: User,
    title: str,
    prompt: str,
    content: dict,
    db: Session,
) -> int:
    artifact = TeacherArtifact(
        teacher_id=current_user.id,
        artifact_type="objective_grading_report",
        title=title,
        prompt=prompt,
        content_json=content,
        source_chunk_ids=[],
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact.id


def _run_objective_grading(
    *,
    student_answers: dict[str, str],
    confidence: dict[str, float],
    answer_rules: list[dict],
    warnings: list[str],
) -> dict:
    rules = [ObjectiveAnswerRule.model_validate(rule) for rule in answer_rules]
    grading_result = grade_objective_answers(student_answers=student_answers, answer_rules=rules)
    report = build_objective_grading_report(
        grading_result,
        extraction_confidence=confidence,
        warnings=warnings,
    )
    return {
        "student_answers": student_answers,
        "answer_rules": [rule.model_dump() for rule in rules],
        "report": report,
        "warnings": warnings,
    }


@router.post("/teacher/objective-grading/generate", response_model=ObjectiveGradingResponse)
async def generate_objective_grading(
    request: ObjectiveGradingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    student_extraction = (
        None
        if request.student_answers
        else extract_objective_answers_from_text(request.student_answer_text)
    )
    student_answers = request.student_answers or (student_extraction.answers if student_extraction else {})
    confidence = (
        {question_no: 1.0 for question_no in student_answers}
        if request.student_answers
        else (student_extraction.confidence if student_extraction else {})
    )
    warnings = list(student_extraction.warnings if student_extraction else [])

    if request.answer_rules:
        answer_rules = [item.model_dump() for item in request.answer_rules]
    else:
        rule_extraction = extract_answer_rules_from_text(
            request.answer_key_text,
            default_score=request.default_score,
        )
        answer_rules = [item.model_dump() for item in rule_extraction.answer_rules]
        warnings.extend(rule_extraction.warnings)

    if not student_answers:
        raise HTTPException(status_code=400, detail="未能获取学生答案，请提供 student_answers 或 student_answer_text")
    if not answer_rules:
        raise HTTPException(status_code=400, detail="未能获取标准答案规则，请提供 answer_rules 或 answer_key_text")

    payload = _run_objective_grading(
        student_answers=student_answers,
        confidence=confidence,
        answer_rules=answer_rules,
        warnings=warnings,
    )
    saved_id = None
    if request.save:
        saved_id = _save_objective_grading_artifact(
            current_user=current_user,
            title="客观题批改报告",
            prompt=json.dumps(request.model_dump(), ensure_ascii=False),
            content=payload,
            db=db,
        )
    return ObjectiveGradingResponse(**payload, saved_artifact_id=saved_id)


@router.post("/teacher/objective-grading/images", response_model=ObjectiveGradingResponse)
async def generate_objective_grading_from_images(
    student_image: UploadFile = File(...),
    answer_key_image: UploadFile = File(...),
    save: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for upload in (student_image, answer_key_image):
        content_type = upload.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{upload.filename or '文件'} 不是图片文件")

    try:
        student_bytes = await student_image.read()
        answer_key_bytes = await answer_key_image.read()
        student_extraction = extract_objective_answers_from_image(student_bytes, student_image.filename or "")
        rule_extraction = extract_answer_rules_from_image(answer_key_bytes, answer_key_image.filename or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图片识别失败: {e}")

    warnings = []
    warnings.extend(student_extraction.warnings)
    warnings.extend(rule_extraction.warnings)
    if rule_extraction.needs_teacher_confirmation:
        warnings.append("标准答案规则来自图片识别，建议教师确认后使用。")
    if student_extraction.needs_review:
        warnings.append(f"学生答案低置信度题号：{', '.join(student_extraction.needs_review)}")

    if not student_extraction.answers:
        raise HTTPException(status_code=400, detail="未能从学生答案图片中识别到题号-答案")
    if not rule_extraction.answer_rules:
        raise HTTPException(status_code=400, detail="未能从标准答案图片中识别到答案规则")

    payload = _run_objective_grading(
        student_answers=student_extraction.answers,
        confidence=student_extraction.confidence,
        answer_rules=[item.model_dump() for item in rule_extraction.answer_rules],
        warnings=warnings,
    )
    saved_id = None
    if save:
        saved_id = _save_objective_grading_artifact(
            current_user=current_user,
            title="客观题图片批改报告",
            prompt=json.dumps(
                {
                    "student_image": student_image.filename,
                    "answer_key_image": answer_key_image.filename,
                },
                ensure_ascii=False,
            ),
            content=payload,
            db=db,
        )
    return ObjectiveGradingResponse(
        title="客观题图片批改报告",
        **payload,
        saved_artifact_id=saved_id,
    )


@router.get("/knowledge-graph/search")
async def search_graph_endpoint(keyword: str, limit: int = 10, _: User = Depends(get_current_user)):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword 不能为空")
    try:
        rows = knowledge_graph_builder.client.execute_read(
            """
            MATCH (n)
            WHERE n:KnowledgePoint OR n:Concept OR n:Formula OR n:Method
            WITH n
            WHERE toLower(coalesce(n.name, '')) CONTAINS toLower($keyword)
               OR toLower(coalesce(n.description, '')) CONTAINS toLower($keyword)
            RETURN n.node_id AS node_id,
                   n.name AS name,
                   n.node_type AS node_type,
                   n.subject AS subject,
                   n.grade AS grade,
                   n.description AS description,
                   n.source_chunk_ids AS source_chunk_ids
            LIMIT $limit
            """,
            {"keyword": keyword.strip(), "limit": max(1, min(limit, 50))},
        )
        return {"nodes": rows}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"知识图谱查询失败: {e}")


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    try:
        session_id = request.session_id or "default_session"
        resp = chat_with_agent(request.message, current_user.username, session_id)
        if isinstance(resp, dict):
            return ChatResponse(**resp)
        return ChatResponse(response=resp)
    except Exception as e:
        message = str(e)
        match = re.search(r"Error code:\s*(\d{3})", message)
        if match:
            code = int(match.group(1))
            if code == 429:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "上游模型服务触发限流/额度限制（429）。请检查账号额度/模型状态。\n"
                        f"原始错误：{message}"
                    ),
                )
            if code in (401, 403):
                raise HTTPException(status_code=code, detail=message)
            raise HTTPException(status_code=code, detail=message)
        raise HTTPException(status_code=500, detail=message)


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """跟 Agent 对话 (流式)"""

    async def event_generator():
        try:
            session_id = request.session_id or "default_session"
            async for chunk in chat_with_agent_stream(request.message, current_user.username, session_id):
                yield chunk
        except Exception as e:
            error_data = {"type": "error", "content": str(e)}
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _is_supported_document(filename: str) -> bool:
    file_lower = filename.lower()
    return (
        file_lower.endswith(".pdf")
        or file_lower.endswith((".docx", ".doc"))
        or file_lower.endswith((".xlsx", ".xls"))
    )


async def _save_upload_file(file: UploadFile, file_path: Path) -> None:
    """按块写入上传文件，避免大文件一次性读入内存。"""
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def _process_upload_job(job_id: str, file_path: str, filename: str, resource_id: int | None, metadata: dict) -> None:
    """后台执行耗时的解析、分块、向量化入库，并持续更新任务进度。"""
    failed_step = "cleanup"
    try:
        upload_job_manager.complete_step(job_id, "upload", "文件已保存到服务器")

        failed_step = "cleanup"
        upload_job_manager.update_step(job_id, "cleanup", 10, "running", "正在清理同名旧文档")
        milvus_manager.init_collection()
        delete_expr = f'filename == "{filename}"'
        try:
            _remove_bm25_stats_for_filename(filename)
        except Exception:
            pass
        try:
            milvus_manager.delete(delete_expr)
        except Exception:
            pass
        try:
            parent_chunk_store.delete_by_filename(filename)
        except Exception:
            pass
        try:
            _delete_resource_from_neo4j(filename)
        except Exception:
            pass
        upload_job_manager.complete_step(job_id, "cleanup", "旧版本清理完成")

        failed_step = "parse"
        upload_job_manager.update_step(job_id, "parse", 5, "running", "正在解析文档并执行三级分块")
        doc_metadata = {
            **(metadata or {}),
            "resource_id": resource_id,
        }
        new_docs = loader.load_document(file_path, filename, metadata=doc_metadata)
        if not new_docs:
            raise ValueError("文档处理失败，未能提取内容")

        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise ValueError("文档处理失败，未生成可检索叶子分块")
        upload_job_manager.complete_step(
            job_id,
            "parse",
            f"解析完成：父级分块 {len(parent_docs)} 个，叶子分块 {len(leaf_docs)} 个",
        )

        failed_step = "parent_store"
        upload_job_manager.update_step(job_id, "parent_store", 20, "running", "正在写入父级分块")
        parent_chunk_store.upsert_documents(parent_docs)
        upload_job_manager.complete_step(job_id, "parent_store", f"父级分块已入库：{len(parent_docs)} 个")

        failed_step = "vector_store"
        total_leaf = len(leaf_docs)
        upload_job_manager.update_step(
            job_id,
            "vector_store",
            0,
            "running",
            f"正在向量化入库：0 / {total_leaf}",
            total_chunks=total_leaf,
            processed_chunks=0,
        )

        def _on_vector_progress(processed: int, total: int) -> None:
            percent = round(processed * 100 / total) if total else 100
            upload_job_manager.update_step(
                job_id,
                "vector_store",
                percent,
                "running",
                f"正在向量化入库：{processed} / {total}",
                total_chunks=total,
                processed_chunks=processed,
            )

        milvus_writer.write_documents(leaf_docs, progress_callback=_on_vector_progress)
        upload_job_manager.complete_step(job_id, "vector_store", f"向量化入库完成：{total_leaf} 个叶子分块")

        failed_step = "graph_store"
        upload_job_manager.update_step(job_id, "graph_store", 20, "running", "正在离线抽取知识点并同步到 Neo4j")
        db = SessionLocal()
        try:
            resource = db.query(Resource).filter(Resource.id == resource_id).first() if resource_id else None
            if resource and resource.visibility != "public":
                upload_job_manager.complete_step(job_id, "graph_store", "个人资料不写入全局 Neo4j 图谱，已跳过")
            elif resource:
                graph_counts = _sync_documents_to_neo4j(resource, new_docs)
                upload_job_manager.complete_step(
                    job_id,
                    "graph_store",
                    (
                        "Neo4j 同步完成："
                        f"模式 {graph_counts.get('extraction_mode', 'unknown')}，"
                        f"Resource {graph_counts.get('resources', 0)}，"
                        f"Chunk {graph_counts.get('chunks', 0)}，"
                        f"Node {graph_counts.get('nodes', 0)}，"
                        f"Edge {graph_counts.get('edges', 0)}"
                    ),
                )
            else:
                upload_job_manager.complete_step(job_id, "graph_store", "未找到资源记录，跳过 Neo4j 同步")
        except Exception as graph_err:
            upload_job_manager.complete_step(job_id, "graph_store", f"Neo4j 暂不可用，已跳过：{graph_err}")
        finally:
            db.close()

        _update_resource_status(resource_id, status="processed", chunk_count=total_leaf)
        upload_job_manager.complete_job(job_id, f"成功上传并处理 {filename}")
    except Exception as e:
        _update_resource_status(resource_id, status="failed")
        upload_job_manager.fail_job(job_id, failed_step, str(e))


def _process_delete_job(job_id: str, filename: str) -> None:
    """后台执行文档删除，并把每个删除阶段同步给前端行内进度卡片。"""
    failed_step = "prepare"
    try:
        failed_step = "prepare"
        delete_job_manager.update_step(job_id, "prepare", 20, "running", "正在初始化 Milvus 集合")
        milvus_manager.init_collection()
        delete_expr = f'filename == "{filename}"'
        delete_job_manager.complete_step(job_id, "prepare", "删除任务已创建")

        failed_step = "bm25"
        delete_job_manager.update_step(job_id, "bm25", 20, "running", "正在同步 BM25 统计")
        _remove_bm25_stats_for_filename(filename)
        delete_job_manager.complete_step(job_id, "bm25", "BM25 统计已同步")

        failed_step = "milvus"
        delete_job_manager.update_step(job_id, "milvus", 30, "running", "正在删除 Milvus 向量数据")
        result = milvus_manager.delete(delete_expr)
        deleted_count = result.get("delete_count", 0) if isinstance(result, dict) else 0
        delete_job_manager.complete_step(job_id, "milvus", f"向量数据已删除：{deleted_count} 条")

        failed_step = "parent_store"
        delete_job_manager.update_step(job_id, "parent_store", 30, "running", "正在删除 PostgreSQL 父级分块")
        parent_chunk_store.delete_by_filename(filename)
        db = SessionLocal()
        try:
            resource = db.query(Resource).filter(Resource.filename == filename).first()
            if resource:
                db.delete(resource)
                db.commit()
        finally:
            db.close()
        delete_job_manager.complete_step(job_id, "parent_store", "父级分块已删除")

        failed_step = "graph_store"
        delete_job_manager.update_step(job_id, "graph_store", 40, "running", "正在删除 Neo4j 资源与 chunk 引用")
        try:
            _delete_resource_from_neo4j(filename)
            delete_job_manager.complete_step(job_id, "graph_store", "Neo4j 图谱引用已删除")
        except Exception as graph_err:
            delete_job_manager.complete_step(job_id, "graph_store", f"Neo4j 暂不可用，已跳过：{graph_err}")

        # 完成摘要会由前端保留 3 秒，再自动从文档列表移除。
        delete_job_manager.complete_job(job_id, f"已删除 {filename}，向量数据 {deleted_count} 条")
    except Exception as e:
        delete_job_manager.fail_job(job_id, failed_step, str(e))


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(current_user: User = Depends(get_current_user)):
    """获取当前用户可见的教材资源列表。管理员看全部，教师看公共 + 个人。"""
    try:
        db = SessionLocal()
        try:
            query = db.query(Resource)
            if current_user.role != "admin":
                query = query.filter((Resource.visibility == "public") | (Resource.owner_id == current_user.id))
            resources = query.order_by(Resource.updated_at.desc()).all()
            documents = [
                DocumentInfo(
                    resource_id=item.id,
                    filename=item.filename,
                    display_name=_display_filename(item),
                    visibility=item.visibility,
                    is_owner=item.owner_id == current_user.id,
                    file_type=item.file_type,
                    chunk_count=item.chunk_count,
                    subject=item.subject,
                    grade=item.grade,
                    book_version=item.book_version,
                    resource_type=item.resource_type,
                    status=item.status,
                    uploaded_at=item.created_at.isoformat(),
                )
                for item in resources
            ]
            if not documents:
                if current_user.role != "admin":
                    return DocumentListResponse(documents=[])
                milvus_manager.init_collection()
                results = milvus_manager.query(
                    output_fields=["filename", "file_type"],
                    limit=10000,
                )
                file_stats = {}
                for item in results:
                    filename = item.get("filename", "")
                    file_type = item.get("file_type", "")
                    if filename not in file_stats:
                        file_stats[filename] = {
                            "filename": filename,
                            "file_type": file_type,
                            "chunk_count": 0,
                            "status": "legacy",
                        }
                    file_stats[filename]["chunk_count"] += 1
                documents = [DocumentInfo(**stats) for stats in file_stats.values()]
        finally:
            db.close()
        return DocumentListResponse(documents=documents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取文档列表失败: {str(e)}")

@router.post("/documents/upload/async", response_model=DocumentUploadStartResponse)
async def upload_document_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    subject: str = Form(""),
    grade: str = Form(""),
    book_version: str = Form(""),
    resource_type: str = Form("textbook"),
    section_title: str = Form(""),
    knowledge_tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """轻量版异步上传：文件落盘后立即返回 job_id，后台继续解析和向量化。"""
    original_filename = file.filename or ""
    if not original_filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    if not _is_supported_document(original_filename):
        raise HTTPException(status_code=400, detail="仅支持 PDF、Word 和 Excel 文档")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = _scoped_filename(original_filename, current_user)
    job = upload_job_manager.create_job(filename)
    file_path = UPLOAD_DIR / filename
    metadata = _normalize_resource_metadata(
        subject=subject,
        grade=grade,
        book_version=book_version,
        resource_type=resource_type,
        section_title=section_title,
        knowledge_tags=knowledge_tags,
    )
    visibility = "public" if current_user.role == "admin" else "private"
    owner_id = None if current_user.role == "admin" else current_user.id
    metadata.update({
        "original_filename": original_filename,
        "owner_id": owner_id,
        "visibility": visibility,
    })
    resource = _upsert_resource(
        db,
        filename=filename,
        file_path=str(file_path),
        metadata=metadata,
        owner_id=owner_id,
        visibility=visibility,
        status="uploading",
        chunk_count=0,
    )

    try:
        upload_job_manager.update_step(job["job_id"], "upload", 1, "running", "正在保存文件到服务器")
        await _save_upload_file(file, file_path)
        upload_job_manager.complete_step(job["job_id"], "upload", "文件已上传，等待后台处理")
    except Exception as e:
        upload_job_manager.fail_job(job["job_id"], "upload", f"文件保存失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    _update_resource_status(resource.id, status="processing")
    background_tasks.add_task(_process_upload_job, job["job_id"], str(file_path), filename, resource.id, metadata)
    return DocumentUploadStartResponse(
        job_id=job["job_id"],
        resource_id=resource.id,
        filename=filename,
        message="文件已上传，正在后台解析和向量化入库",
    )


@router.get("/documents/upload/jobs/{job_id}", response_model=DocumentUploadJobResponse)
async def get_upload_job(job_id: str, current_user: User = Depends(get_current_user)):
    job = upload_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="上传任务不存在或已过期")
    if current_user.role != "admin" and not str(job.get("filename", "")).startswith(f"user_{current_user.id}__"):
        raise HTTPException(status_code=404, detail="上传任务不存在或已过期")
    return DocumentUploadJobResponse(**job)


@router.get("/documents/upload/jobs", response_model=list[DocumentUploadJobResponse])
async def list_upload_jobs(current_user: User = Depends(get_current_user)):
    jobs = upload_job_manager.list_jobs()
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    if current_user.role != "admin":
        prefix = f"user_{current_user.id}__"
        jobs = [job for job in jobs if str(job.get("filename", "")).startswith(prefix)]
    return [DocumentUploadJobResponse(**job) for job in jobs]


@router.delete("/documents/delete/async/{filename}", response_model=DocumentDeleteStartResponse)
async def delete_document_async(
    filename: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """轻量版异步删除：立即返回 job_id，实际删除在后台执行。"""
    db = SessionLocal()
    try:
        _assert_can_modify_resource(db, filename, current_user)
    finally:
        db.close()
    job = delete_job_manager.create_job(
        filename,
        steps=DELETE_STEPS,
        current_step="prepare",
        message="等待删除",
        completion_step="graph_store",
    )
    delete_job_manager.update_step(job["job_id"], "prepare", 1, "running", "删除任务已提交")
    background_tasks.add_task(_process_delete_job, job["job_id"], filename)
    return DocumentDeleteStartResponse(
        job_id=job["job_id"],
        filename=filename,
        message=f"正在删除 {filename}",
    )


@router.get("/documents/delete/jobs/{job_id}", response_model=DocumentDeleteJobResponse)
async def get_delete_job(job_id: str, _: User = Depends(get_current_user)):
    job = delete_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="删除任务不存在或已过期")
    return DocumentDeleteJobResponse(**job)


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    subject: str = Form(""),
    grade: str = Form(""),
    book_version: str = Form(""),
    resource_type: str = Form("textbook"),
    section_title: str = Form(""),
    knowledge_tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传文档并进行 embedding（管理员）"""
    try:
        original_filename = file.filename or ""
        file_lower = original_filename.lower()
        if not original_filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")
        if not (
            file_lower.endswith(".pdf")
            or file_lower.endswith((".docx", ".doc"))
            or file_lower.endswith((".xlsx", ".xls"))
        ):
            raise HTTPException(status_code=400, detail="仅支持 PDF、Word 和 Excel 文档")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        milvus_manager.init_collection()
        filename = _scoped_filename(original_filename, current_user)
        metadata = _normalize_resource_metadata(
            subject=subject,
            grade=grade,
            book_version=book_version,
            resource_type=resource_type,
            section_title=section_title,
            knowledge_tags=knowledge_tags,
        )
        visibility = "public" if current_user.role == "admin" else "private"
        owner_id = None if current_user.role == "admin" else current_user.id
        metadata.update({
            "original_filename": original_filename,
            "owner_id": owner_id,
            "visibility": visibility,
        })

        delete_expr = f'filename == "{filename}"'
        try:
            _remove_bm25_stats_for_filename(filename)
        except Exception:
            pass
        try:
            milvus_manager.delete(delete_expr)
        except Exception:
            pass
        try:
            parent_chunk_store.delete_by_filename(filename)
        except Exception:
            pass

        file_path = UPLOAD_DIR / filename
        resource = _upsert_resource(
            db,
            filename=filename,
            file_path=str(file_path),
            metadata=metadata,
            owner_id=owner_id,
            visibility=visibility,
            status="processing",
            chunk_count=0,
        )
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        try:
            new_docs = loader.load_document(
                str(file_path),
                filename,
                metadata={
                    **metadata,
                    "resource_id": resource.id,
                },
            )
        except Exception as doc_err:
            _update_resource_status(resource.id, status="failed")
            raise HTTPException(status_code=500, detail=f"文档处理失败: {doc_err}")

        if not new_docs:
            raise HTTPException(status_code=500, detail="文档处理失败，未能提取内容")

        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise HTTPException(status_code=500, detail="文档处理失败，未生成可检索叶子分块")

        parent_chunk_store.upsert_documents(parent_docs)
        milvus_writer.write_documents(leaf_docs)
        if resource.visibility == "public":
            try:
                _sync_documents_to_neo4j(resource, new_docs)
            except Exception:
                pass
        _update_resource_status(resource.id, status="processed", chunk_count=len(leaf_docs))

        return DocumentUploadResponse(
            resource_id=resource.id,
            filename=filename,
            chunks_processed=len(leaf_docs),
            message=(
                f"成功上传并处理 {filename}，叶子分块 {len(leaf_docs)} 个，"
                f"父级分块 {len(parent_docs)} 个（存入 PostgreSQL），并尝试同步 Neo4j 图谱"
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}")


@router.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
async def delete_document(filename: str, current_user: User = Depends(get_current_user)):
    """删除文档在 Milvus 中的向量（保留本地文件，管理员）"""
    try:
        milvus_manager.init_collection()
        db = SessionLocal()
        try:
            _assert_can_modify_resource(db, filename, current_user)
        finally:
            db.close()

        delete_expr = f'filename == "{filename}"'
        _remove_bm25_stats_for_filename(filename)
        result = milvus_manager.delete(delete_expr)
        parent_chunk_store.delete_by_filename(filename)
        try:
            _delete_resource_from_neo4j(filename)
        except Exception:
            pass
        db = SessionLocal()
        try:
            resource = db.query(Resource).filter(Resource.filename == filename).first()
            if resource:
                db.delete(resource)
                db.commit()
        finally:
            db.close()

        return DocumentDeleteResponse(
            filename=filename,
            chunks_deleted=result.get("delete_count", 0) if isinstance(result, dict) else 0,
            message=f"成功删除文档 {filename} 的向量数据（本地文件已保留）",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除文档失败: {str(e)}")
