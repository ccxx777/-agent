"""合同文件上传和解析状态 API。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.schemas.contract_review import ContractReviewDetail, ContractReviewSummary
from app.services.contract_review_service import (
    ContractReviewService,
    ContractUploadError,
)

logger = logging.getLogger(__name__)


def create_contract_review_router(service: ContractReviewService) -> APIRouter:
    router = APIRouter(prefix="/api/contract-reviews", tags=["Contract Reviews"])

    @router.post("", response_model=ContractReviewSummary, status_code=202)
    async def upload_contract(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency declaration
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractReviewSummary:
        """上传 PDF，先落私有存储，再异步创建页级解析结果。"""

        try:
            content = await file.read(service.max_upload_bytes + 1)
            result = await service.create_review(
                user_id=user["user_id"],
                filename=file.filename or "contract.pdf",
                content_type=file.content_type,
                content=content,
            )
        except ContractUploadError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            logger.exception("Contract upload failed")
            raise HTTPException(status_code=500, detail="合同上传失败，请稍后重试") from error

        # 文件路径只留在服务内部，API 响应不暴露私有存储位置。
        background_tasks.add_task(
            service.process_review,
            result.review_id,
            storage_path=service.storage.path_for(result.review_id),
        )
        return result

    @router.get("/{review_id}", response_model=ContractReviewDetail)
    async def get_contract_review(
        review_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractReviewDetail:
        """查询任务状态和脱敏后的页级文本。"""

        result = await service.get_review(review_id, user["user_id"])
        if result is None:
            raise HTTPException(status_code=404, detail="合同审查任务不存在")
        return result

    return router
