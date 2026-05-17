from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    teacher_artifacts = relationship("TeacherArtifact", back_populates="teacher", cascade="all, delete-orphan")
    teacher_memory = relationship("TeacherMemory", back_populates="teacher", uselist=False, cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (UniqueConstraint("user_id", "session_id", name="uq_user_session"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_ref_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    rag_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    session = relationship("ChatSession", back_populates="messages")


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False, index=True)
    source_file: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    grade: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    book_version: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), default="textbook", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="processing", nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ParentChunk(Base):
    __tablename__ = "parent_chunks"

    chunk_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[int | None] = mapped_column(ForeignKey("resources.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_type: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subject: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    grade: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    book_version: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), default="textbook", nullable=False)
    section_title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    knowledge_tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    parent_chunk_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    root_chunk_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    chunk_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_idx: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TeacherArtifact(Base):
    __tablename__ = "teacher_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_chunk_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    teacher = relationship("User", back_populates="teacher_artifacts")


class TeacherMemory(Base):
    __tablename__ = "teacher_memories"

    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    memory_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    preferred_subjects: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    preferred_grades: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    teaching_style: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    question_difficulty_preference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    output_format_preference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    teacher = relationship("User", back_populates="teacher_memory")
