"""HTTP request schemas shared by the API routers."""
from pydantic import BaseModel, Field
from .translation import DEFAULT_POLISH_PROMPT, DEFAULT_TRANSLATE_PROMPT


class ModelDownload(BaseModel):
    model: str
    token: str = ""


class ModelImport(BaseModel):
    model: str
    path: str


class FetchModelsRequest(BaseModel):
    base_url: str = ""
    key: str = ""


class Settings(BaseModel):
    base_url: str = Field(max_length=2000)
    model: str = Field(max_length=300)
    target_language: str = Field(default="简体中文", min_length=1, max_length=100)
    topic: str = Field(default="", max_length=2000)
    glossary: str = Field(default="", max_length=10000)
    default_diarize: bool = True
    default_auto_polish_translate: bool = False
    key: str = Field(default="", max_length=3000)
    save_key: bool = False
    clear_key: bool = False


class PickFile(BaseModel):
    multiple: bool = False


class MediaPath(BaseModel):
    path: str


class Params(BaseModel):
    audio_track: int
    subtitle_track: int | None = None
    language: str = Field(default="", max_length=10)
    device: str = "cuda"
    glossary: str = Field(default="", max_length=10000)
    topic: str = Field(default="", max_length=5000)
    target_language: str = Field(default="简体中文", max_length=50)
    polish_prompt: str = Field(default=DEFAULT_POLISH_PROMPT, max_length=5000)
    translate_prompt: str = Field(default=DEFAULT_TRANSLATE_PROMPT, max_length=5000)
    context_mode: str = "batch"
    diarize: bool = True
    auto_polish_translate: bool = False
    num_speakers: int | None = Field(default=None, ge=1, le=20)
    speakers: dict[str, dict[str, str]] = Field(default_factory=dict)


class Action(BaseModel):
    phase: str
    ids: list[str] | None = None
    overwrite: bool = False
    resume: bool = False


class Edit(BaseModel):
    version: int
    start: int
    end: int
    source: str
    translation: str = ""
    speaker: str = ""
    confirmed: bool = False


class Split(BaseModel):
    version: int
    at: int
    position: int


class Merge(BaseModel):
    ids: list[str]
    versions: list[int]


class Restore(BaseModel):
    version: int
