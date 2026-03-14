# Pydantic 模型

from pydantic import BaseModel, Field
from enum import Enum
from typing import List

class PhishingProbability(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class SuspiciousElement(BaseModel):
    element: str = Field(..., description="可疑元素名稱（請用繁體中文）")
    reason: str = Field(..., description="原因（請用繁體中文）")

class SimplePhishingAnalysis(BaseModel):
    is_potential_phishing: bool
    explanation: str



