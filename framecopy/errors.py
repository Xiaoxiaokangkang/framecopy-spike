from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FramecopyError(Exception):
    code: str
    public_message: str
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.public_message}"


ERROR_MESSAGES = {
    "LINK_ENVIRONMENT_UNAVAILABLE": "视频号链接解析服务暂不可用，请改用本地视频上传。",
    "LINK_INVALID": "没有找到受支持的视频号分享链接，请重新复制后粘贴。",
    "LINK_CONTENT_UNAVAILABLE": "视频已失效、删除、设为私密或暂时无法访问。",
    "LINK_DOWNLOAD_FAILED": "视频获取失败，请稍后重试或上传本地视频。",
    "FILE_TOO_LARGE": "视频超过 200 MB 限制。",
    "DURATION_EXCEEDED": "视频超过 10 分钟限制。",
    "UNSUPPORTED_MEDIA": "无法识别该视频格式或编码。",
    "MEDIA_CORRUPTED": "视频已损坏或无法解码。",
    "NO_TEXT_DETECTED": "视频处理完成，但没有检测到有效的画面文字。",
    "OCR_INITIALIZATION_FAILED": "本地 OCR 引擎未就绪，请检查安装状态。",
    "PROCESSING_TIMEOUT": "处理时间超过限制，任务已安全终止。",
    "RESULT_GENERATION_FAILED": "结果文件生成失败，中间识别结果已保留。",
    "TASK_EXPIRED": "任务已超过保留期限并被删除。",
}


def error(code: str, *, retryable: bool = False, detail: str | None = None) -> FramecopyError:
    message = ERROR_MESSAGES.get(code, "处理失败，请记录任务编号并反馈。")
    if detail:
        message = f"{message}（{detail}）"
    return FramecopyError(code, message, retryable)

