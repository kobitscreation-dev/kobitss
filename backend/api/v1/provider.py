from fastapi import APIRouter
from backend.core.config import settings
from backend.services.llm import get_llm_provider

router = APIRouter()

@router.get("/health")
async def get_provider_health():
    """
    Returns the current LLM provider health and configuration status.
    Never exposes API keys.
    """
    provider_name = settings.LLM_PROVIDER.lower()
    
    status_info = {
        "provider": provider_name,
        "configured": False,
        "available": False,
        "model": "unknown",
        "last_error": None
    }
    
    try:
        provider = get_llm_provider()
        
        if provider_name == "mock":
            status_info["configured"] = True
            status_info["available"] = True
            status_info["model"] = "mock-model"
        
        elif provider_name == "anthropic":
            status_info["configured"] = getattr(provider, "configured", False)
            status_info["available"] = status_info["configured"]
            status_info["model"] = settings.ANTHROPIC_MODEL
            
        elif provider_name == "deepseek":
            status_info["configured"] = getattr(provider, "configured", False)
            status_info["available"] = status_info["configured"]
            status_info["model"] = settings.DEEPSEEK_MODEL
            
        elif provider_name in ["bedrock", "aws", "aws_bedrock", "claude"]:
            status_info["configured"] = (
                getattr(provider, "configured", False)
                or os.environ.get("USE_ONLY_CLAUDE") == "1"
                or bool(
                    getattr(settings, "AWS_BEDROCK_API_KEY", None)
                    or getattr(settings, "ANTHROPIC_API_KEY", None)
                    or (getattr(settings, "AWS_ACCESS_KEY_ID", None) and getattr(settings, "AWS_SECRET_ACCESS_KEY", None))
                )
            )
            status_info["available"] = status_info["configured"]
            status_info["model"] = getattr(provider, "default_model", settings.BEDROCK_MODEL)

        elif provider_name == "gemini":
            status_info["configured"] = getattr(provider, "configured", False)
            status_info["available"] = status_info["configured"]
            status_info["model"] = "gemini-2.5-pro"
            
    except Exception as e:
        status_info["last_error"] = str(e)
        
    return status_info
