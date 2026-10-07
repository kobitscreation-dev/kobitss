from sqlalchemy import Column, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.models.base import Base

class GraphNode(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    
    # Node types: FILE, CLASS, FUNCTION, IMPORT
    node_type: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    file_path: Mapped[str] = mapped_column(String, index=True)
    
    # For functions/classes: snippet of code or docstring
    content: Mapped[str] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # Relationships
    edges_out = relationship("GraphEdge", foreign_keys="GraphEdge.source_id", back_populates="source", cascade="all, delete-orphan")
    edges_in = relationship("GraphEdge", foreign_keys="GraphEdge.target_id", back_populates="target", cascade="all, delete-orphan")


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    
    source_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    
    # Edge types: DEFINES, CALLS, IMPORTS, INHERITS
    edge_type: Mapped[str] = mapped_column(String, index=True)
    
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    source = relationship("GraphNode", foreign_keys=[source_id], back_populates="edges_out")
    target = relationship("GraphNode", foreign_keys=[target_id], back_populates="edges_in")
