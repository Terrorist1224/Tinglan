"""全局设置与上游模型列表接口；本地设置及密钥持久化委托 backend.settings。"""
from fastapi import APIRouter
from urllib.parse import urlsplit
from ..schemas import FetchModelsRequest, Settings
from ..settings import get_api_key, load_settings, public_settings, update_settings

router = APIRouter()


@router.post("/api/settings/models")
def fetch_api_models(body: FetchModelsRequest):
    base_url = (body.base_url.strip() or load_settings().get("base_url", "")).rstrip("/")
    key = body.key.strip() or get_api_key()
    if not base_url:
        raise ValueError("请先填写基础地址")
    if not key:
        raise ValueError("请先填写 API 密钥")
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.netloc:
        raise ValueError("基础地址必须为有效 HTTP(S) 地址")
    models_url = f"{base_url}/models"
    try:
        import httpx

        with httpx.Client(timeout=15, follow_redirects=True, trust_env=False) as client:
            resp = client.get(models_url, headers={"Authorization": f"Bearer {key}"})
            if resp.status_code in (401, 403):
                raise ValueError("API 密钥无效或无权限获取模型列表")
            if resp.is_error:
                raise ValueError(f"获取模型列表失败: HTTP {resp.status_code}")
            data = resp.json()
    except httpx.TimeoutException:
        raise ValueError("请求模型列表超时，请检查基础地址或网络")
    except httpx.HTTPError as exc:
        raise ValueError(f"连接失败: {exc}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"解析模型列表失败: {exc}")

    model_ids = []
    if isinstance(data, dict):
        raw_list = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        raw_list = data
    else:
        raw_list = []

    for item in raw_list:
        if isinstance(item, dict) and "id" in item:
            model_ids.append(str(item["id"]))
        elif isinstance(item, str):
            model_ids.append(item)

    sorted_models = sorted(list(set(model_ids)))
    if not sorted_models:
        raise ValueError("获取成功，但未解析到可用模型 ID")
    return {"models": sorted_models}


@router.get("/api/settings")
def settings():
    return public_settings()


@router.put("/api/settings")
def save_settings(body: Settings):
    return update_settings(body)
