from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    organization_name: Optional[str] = "Default Workspace"

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    is_active: bool
    google_id: Optional[str] = None
    github_id: Optional[str] = None
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}

class Token(BaseModel):
    access_token: str
    token_type: str
