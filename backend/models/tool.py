import uuid
import json
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, ForeignKey, JSON, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.models.base import Base


class ToolExecutionType(str, enum.Enum):
    BASH = "bash"
    PYTHON = "python"
    HTTP = "http"  # UNIMPLEMENTED — marked explicitly


class CustomTool(Base):
    __tablename__ = "custom_tools"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str] = mapped_column(Text)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_type: Mapped[str] = mapped_column(String)
    execution_code: Mapped[str] = mapped_column(Text)
    assigned_agents_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(timezone.utc).isoformat())

    organization = relationship("Organization")

    def get_assigned_agents(self) -> list:
        try:
            return json.loads(self.assigned_agents_json)
        except Exception:
            return []
