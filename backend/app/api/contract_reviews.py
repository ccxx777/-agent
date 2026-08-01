"""合同文件上传和解析状态 API。"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)

from app.api.deps import get_current_user
from app.infrastructure.contract_review_repository import SessionOwnershipError
from app.schemas.contract_confirmation import (
    ContractConfirmationResponse,
    FactConfirmationRequest,
)
from app.schemas.chat import ContractReviewHistoryResponse, SessionReviewSummary
from app.schemas.contract_review import ContractReviewDetail, ContractReviewSummary
from app.schemas.contract_review_workflow import (
    ContractReviewReport,
    ContractReviewWorkflowResponse,
)
from app.services.contract_confirmation_service import (
    ContractConfirmationError,
    ContractFactConfirmationService,
)
from app.services.contract_review_service import (
    ContractReviewService,
    ContractUploadError,
)
from app.services.contract_review_workflow_service import (
    ContractReviewWorkflowError,
    ContractReviewWorkflowService,
)

logger = logging.getLogger(__name__)


def create_contract_review_router(
    service: ContractReviewService,
    confirmation_service: ContractFactConfirmationService | None = None,
    workflow_service: ContractReviewWorkflowService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/contract-reviews", tags=["Contract Reviews"])

    @router.get("/history", response_model=ContractReviewHistoryResponse)
    async def get_contract_review_history(
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractReviewHistoryResponse:
        """返回当前用户可恢复的合同审查会话元数据。"""

        repository = getattr(service, "repository", None)
        if repository is None or not hasattr(repository, "list_user_reviews"):
            raise HTTPException(status_code=503, detail="合同审查历史暂不可用")
        reviews = await repository.list_user_reviews(user["user_id"], limit=50)
        return ContractReviewHistoryResponse(
            reviews=[SessionReviewSummary.model_validate(review) for review in reviews],
        )

    @router.post("", response_model=ContractReviewSummary, status_code=202)
    async def upload_contract(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency declaration
        session_id: str | None = Form(default=None),
        retention_policy: Literal["short", "long_opt_in"] = Form(default="short"),
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractReviewSummary:
        """上传 PDF，先落私有存储，再异步创建页级解析结果。"""

        try:
            content = await file.read(service.max_upload_bytes + 1)
            result = await service.create_review(
                user_id=user["user_id"],
                session_id=session_id,
                filename=file.filename or "contract.pdf",
                content_type=file.content_type,
                content=content,
                retention_policy=retention_policy,
            )
        except ContractUploadError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except SessionOwnershipError as error:
            raise HTTPException(status_code=403, detail="无权使用该会话") from error
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

    @router.get("/{review_id}/confirmation", response_model=ContractConfirmationResponse)
    async def get_contract_confirmation(
        review_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractConfirmationResponse:
        """读取事实确认表单；只返回脱敏证据和分层 provenance。"""

        if confirmation_service is None:
            raise HTTPException(status_code=503, detail="事实确认模块尚未启用")
        try:
            result = await confirmation_service.get_confirmation(review_id, user["user_id"])
        except ContractConfirmationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="合同审查任务不存在")
        return result

    @router.put("/{review_id}/confirmation", response_model=ContractConfirmationResponse)
    async def put_contract_confirmation(
        review_id: str,
        request: FactConfirmationRequest,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractConfirmationResponse:
        """保存确认草稿或提交确认；使用 revision 防止并发覆盖。"""

        if confirmation_service is None:
            raise HTTPException(status_code=503, detail="事实确认模块尚未启用")
        try:
            result = await confirmation_service.apply_confirmation(
                review_id,
                user["user_id"],
                request,
            )
        except ContractConfirmationError as error:
            status_code = 409 if error.code == "stale_revision" else 422
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="合同审查任务不存在")
        return result

    @router.post("/{review_id}/workflow", response_model=ContractReviewWorkflowResponse)
    async def run_contract_review_workflow(
        review_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractReviewWorkflowResponse:
        """在事实确认门禁通过后运行劳动合同审查 Workflow。"""

        if workflow_service is None:
            raise HTTPException(status_code=503, detail="合同审查 Workflow 尚未启用")
        try:
            return await workflow_service.run(review_id, user["user_id"])
        except ContractReviewWorkflowError as error:
            message = str(error)
            status_code = 404 if "不存在" in message or "无权" in message else 409
            raise HTTPException(status_code=status_code, detail=message) from error
        except Exception as error:
            logger.exception("Contract review workflow API failed: review_id=%s", review_id)
            raise HTTPException(status_code=500, detail="合同审查暂时失败，请稍后重试") from error

    @router.get("/{review_id}/report", response_model=ContractReviewReport)
    async def get_contract_report(
        review_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ContractReviewReport:
        """读取已生成的最新报告；不会因为查询而重新运行 Workflow。"""

        if workflow_service is None:
            raise HTTPException(status_code=503, detail="合同审查 Workflow 尚未启用")
        report = await workflow_service.get_report(review_id, user["user_id"])
        if report is None:
            raise HTTPException(status_code=404, detail="合同报告不存在")
        return report

    @router.get("/{review_id}/report.pdf")
    async def download_contract_report(
        review_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> Response:
        """下载当前用户有权访问的固定报告 PDF。"""

        if workflow_service is None:
            raise HTTPException(status_code=503, detail="合同审查 Workflow 尚未启用")
        pdf = await workflow_service.render_report_pdf(review_id, user["user_id"])
        if pdf is None:
            raise HTTPException(status_code=404, detail="合同报告不存在")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="contract-review-{review_id}.pdf"',
                "Cache-Control": "private, no-store",
            },
        )

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

    @router.delete("/{review_id}", status_code=204)
    async def delete_contract_review(
        review_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> Response:
        """删除当前用户的合同、事实、报告和私有文件；重复删除视为成功。"""

        deleted = await service.delete_review(review_id, user["user_id"])
        if not deleted:
            raise HTTPException(status_code=404, detail="合同审查任务不存在")
        return Response(status_code=204)

    return router
