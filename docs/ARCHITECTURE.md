# 架构与数据流

## 系统边界

当前是 macOS 本地技术验证版。网页服务只监听 `127.0.0.1`，不应直接暴露到公网。

```text
浏览器
  │ 原始文件流 / 状态轮询 / 结果编辑
  ▼
Node.js HTTP 服务（server.mjs）
  │ 创建任务目录，启动子进程
  ▼
Objective-C 提取器（AVFoundation + Vision + CoreImage）
  │ 媒体校验 → 采样 → 变化/清晰度筛选 → OCR → 合并
  ▼
data/jobs/<job-id>/
  ├─ input/                  原视频
  ├─ evidence/               关键帧
  ├─ progress.json           任务状态
  ├─ raw-ocr.jsonl           原始 OCR
  ├─ segments.auto.json      自动结果
  ├─ segments.user.json      可编辑结果
  ├─ manifest.json           处理参数
  └─ *.md                    下载文案
```

## 安全边界

- 上传上限为 200 MB，视频最长 10 分钟，分辨率不超过 4K。
- 内部文件名与用户文件名分离，下载只允许固定的文件名白名单。
- 结果访问需要高熵令牌，服务端只保存 SHA-256 哈希。
- 当前没有完整的资源隔离、自动清理、限额和公网防滥用能力。

## 向 MVP 迁移的接口

下一阶段应保留当前 HTTP 和输出数据结构，将实现替换为：

- FastAPI + SQLite；
- Redis + Dramatiq 独立 Worker；
- FFmpeg / ffprobe 媒体探测；
- PaddleOCR Provider；
- 坐标文本轨迹、变化点补帧和多帧投票。
