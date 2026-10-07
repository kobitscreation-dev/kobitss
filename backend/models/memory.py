from typing import Optional
from sqlalchemy import Column, String, Text, ForeignKey, JSON, Float
from sqlalchemy.orm import relationship, Mapped, mapped_column
from backend.models.base import Base

class CodeDocument(Base):
    __tablename__ = "code_documents"

    id = Column(String, primary_key=True, index=True)
    project_id = Column(String, ForeignKey("projects.id"), index=True)
    file_path = Column(String, index=True)
    content_chunk = Column(Text)
    metadata_json = Column(JSON, default={})
    embedding = Column(JSON, nullable=True) 

    project = relationship("Project")


class AgentMemory(Base):
    __tablename__ = "agent_memory"

    agent_id: Mapped[str] = mapped_column(String, index=True) # ID or Name of agent
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    
    category: Mapped[str] = mapped_column(String)
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)
    
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    knowledge_state: Mapped[str] = mapped_column(String, default='VERIFIED')


class MissionMemory(Base):
    __tablename__ = "mission_memory"

    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

