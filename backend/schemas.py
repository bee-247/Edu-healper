from pydantic import BaseModel, Field
from typing import Any, Optional, List


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"
    admin_code: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class CurrentUserResponse(BaseModel):
    username: str
    role: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default_session"


class RetrievedChunk(BaseModel):
    filename: str
    resource_id: Optional[int] = None
    subject: Optional[str] = None
    grade: Optional[str] = None
    book_version: Optional[str] = None
    resource_type: Optional[str] = None
    section_title: Optional[str] = None
    knowledge_tags: Optional[List[str]] = None
    page_number: Optional[str | int] = None
    text: Optional[str] = None
    score: Optional[float] = None
    rrf_rank: Optional[int] = None
    rerank_score: Optional[float] = None


class RagTrace(BaseModel):
    tool_used: bool
    tool_name: str
    query: Optional[str] = None
    expanded_query: Optional[str] = None
    step_back_question: Optional[str] = None
    step_back_answer: Optional[str] = None
    expansion_type: Optional[str] = None
    hypothetical_doc: Optional[str] = None
    retrieval_stage: Optional[str] = None
    grade_score: Optional[str] = None
    grade_route: Optional[str] = None
    rewrite_needed: Optional[bool] = None
    rewrite_strategy: Optional[str] = None
    rewrite_query: Optional[str] = None
    rerank_enabled: Optional[bool] = None
    rerank_applied: Optional[bool] = None
    rerank_model: Optional[str] = None
    rerank_endpoint: Optional[str] = None
    rerank_error: Optional[str] = None
    retrieval_mode: Optional[str] = None
    candidate_k: Optional[int] = None
    leaf_retrieve_level: Optional[int] = None
    auto_merge_enabled: Optional[bool] = None
    auto_merge_applied: Optional[bool] = None
    auto_merge_threshold: Optional[int] = None
    auto_merge_replaced_chunks: Optional[int] = None
    auto_merge_steps: Optional[int] = None
    retrieved_chunks: Optional[List[RetrievedChunk]] = None
    initial_retrieved_chunks: Optional[List[RetrievedChunk]] = None
    expanded_retrieved_chunks: Optional[List[RetrievedChunk]] = None


class ChatResponse(BaseModel):
    response: str
    rag_trace: Optional[RagTrace] = None
    agent_route: Optional[str] = None


class MessageInfo(BaseModel):
    type: str
    content: str
    timestamp: str
    rag_trace: Optional[RagTrace] = None


class SessionMessagesResponse(BaseModel):
    messages: List[MessageInfo]


class SessionInfo(BaseModel):
    session_id: str
    updated_at: str
    message_count: int


class SessionListResponse(BaseModel):
    sessions: List[SessionInfo]


class SessionDeleteResponse(BaseModel):
    session_id: str
    message: str


class DocumentInfo(BaseModel):
    resource_id: Optional[int] = None
    filename: str
    display_name: Optional[str] = None
    visibility: Optional[str] = None
    is_owner: bool = False
    file_type: str
    chunk_count: int
    subject: Optional[str] = None
    grade: Optional[str] = None
    book_version: Optional[str] = None
    resource_type: Optional[str] = None
    status: Optional[str] = None
    uploaded_at: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: List[DocumentInfo]


class DocumentUploadResponse(BaseModel):
    resource_id: Optional[int] = None
    filename: str
    chunks_processed: int
    message: str


class DocumentUploadStartResponse(BaseModel):
    job_id: str
    resource_id: Optional[int] = None
    filename: str
    message: str


class UploadStepInfo(BaseModel):
    key: str
    label: str
    percent: int
    status: str
    message: str = ""


class DocumentUploadJobResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    current_step: str
    message: str
    total_chunks: int = 0
    processed_chunks: int = 0
    error: Optional[str] = None
    created_at: str
    updated_at: str
    steps: List[UploadStepInfo]


class DocumentDeleteStartResponse(BaseModel):
    job_id: str
    filename: str
    message: str


class DocumentDeleteJobResponse(DocumentUploadJobResponse):
    pass


class DocumentDeleteResponse(BaseModel):
    filename: str
    chunks_deleted: int
    message: str


class KnowledgeGraphNode(BaseModel):
    node_id: str
    name: str
    node_type: str
    subject: Optional[str] = None
    grade: Optional[str] = None
    description: Optional[str] = None
    source_chunk_ids: Optional[List[str]] = None


class KnowledgeGraphRelation(BaseModel):
    source_node_id: str
    source_name: str
    target_node_id: str
    target_name: str
    relation_type: str
    confidence: Optional[float] = None


class KnowledgeGraphSearchResponse(BaseModel):
    nodes: List[KnowledgeGraphNode]
    relations: List[KnowledgeGraphRelation] = []


class TeacherArtifactCreate(BaseModel):
    artifact_type: str = Field(..., description="question_set / lesson_plan / homework / grading_reference / chapter_summary")
    title: str
    prompt: str = ""
    content_json: dict = Field(default_factory=dict)
    source_chunk_ids: List[str] = Field(default_factory=list)


class TeacherArtifactUpdate(BaseModel):
    artifact_type: Optional[str] = None
    title: Optional[str] = None
    prompt: Optional[str] = None
    content_json: Optional[dict] = None
    source_chunk_ids: Optional[List[str]] = None


class TeacherArtifactInfo(BaseModel):
    id: int
    artifact_type: str
    title: str
    prompt: str
    content_json: dict
    source_chunk_ids: List[str]
    created_at: str
    updated_at: str


class TeacherArtifactListResponse(BaseModel):
    artifacts: List[TeacherArtifactInfo]


class TeacherTaskSourceChunk(BaseModel):
    chunk_id: str = ""
    filename: str = ""
    page_number: Optional[str | int] = None
    section_title: str = ""
    text: str = ""


class TeacherTaskResponse(BaseModel):
    artifact_type: str
    title: str
    content: dict
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_chunks: List[TeacherTaskSourceChunk] = Field(default_factory=list)
    verifier_notes: str = ""
    agent_route: str = ""
    saved_artifact_id: Optional[int] = None


class GenerateQuestionsRequest(BaseModel):
    knowledge_topic: str
    subject: str = ""
    grade: str = ""
    book_version: str = ""
    resource_type: str = ""
    question_type: str = "short_answer"
    difficulty: str = "medium"
    count: int = 3
    save: bool = False


class GenerateLessonPlanRequest(BaseModel):
    teaching_topic: str
    subject: str = ""
    grade: str = ""
    book_version: str = ""
    resource_type: str = ""
    lesson_duration: int = 45
    teaching_style: str = ""
    save: bool = False


class GenerateGradingReferenceRequest(BaseModel):
    question: str
    student_answer: str
    standard_answer: str = ""
    rubric: str = ""
    max_score: int = 10
    knowledge_topic: str = ""
    subject: str = ""
    grade: str = ""
    book_version: str = ""
    resource_type: str = ""
    question_type: str = ""
    save: bool = False


class ObjectiveAnswerRuleInfo(BaseModel):
    question_no: str
    question_type: str = "single_choice"
    max_score: float = Field(default=1, gt=0)
    grading_mode: str = "any_of"
    acceptable_answers: List[str] = Field(default_factory=list)
    tolerance: Optional[float] = None
    partial_score_per_option: Optional[float] = None
    penalty_wrong_option: float = 0
    wrong_option_policy: str = ""
    case_sensitive: bool = False
    ignore_spaces: bool = True


class ObjectiveGradingRequest(BaseModel):
    student_answer_text: str = ""
    student_answers: dict[str, str] = Field(default_factory=dict)
    answer_key_text: str = ""
    answer_rules: List[ObjectiveAnswerRuleInfo] = Field(default_factory=list)
    default_score: float = Field(default=1, gt=0)
    save: bool = False


class ObjectiveGradingResponse(BaseModel):
    artifact_type: str = "objective_grading_report"
    title: str = "客观题批改报告"
    student_answers: dict[str, str] = Field(default_factory=dict)
    answer_rules: List[dict[str, Any]] = Field(default_factory=list)
    report: dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    saved_artifact_id: Optional[int] = None
