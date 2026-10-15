# 帧文案 FrameCopy · Spike 0.1

> 上传本地视频，提取画面中的有效文字，合并重复和逐字动画，最终导出可核查的 Markdown。

![macOS](https://img.shields.io/badge/platform-macOS-17201d)
![Node.js](https://img.shields.io/badge/Node.js-%E2%89%A520-1d4f43)
![Stage](https://img.shields.io/badge/stage-Spike%200.1-d89132)

“帧文案”是视频画面文案提取产品的阶段零技术验证版。它不转写语音，只关注直接出现在画面里的标题、正文、图表文字和动画文案。

## 已实现

- 本地视频上传与媒体校验；
- 画面变化/清晰度候选帧筛选；
- Apple Vision 本机高精度 OCR；
- 相邻重复和逐字动画末态合并；
- 关键帧核查、低置信度提示与结果编辑；
- 纯净版/时间戳版 Markdown、原始 OCR 和 manifest 下载。

> 仓库定位：用于验证核心技术链路的 macOS-only Spike，不是可直接公网部署的生产版。

## 快速开始

### 环境要求

- macOS 13 或更高版本；
- Node.js 20 或更高版本；
- Xcode Command Line Tools（需要 `clang` 和 macOS SDK）；
- Chrome、Edge 或 Safari 近期版本。

```bash
xcode-select --install  # 如尚未安装
git clone <your-repository-url>
cd framecopy-spike
npm run setup
npm start
```

浏览器打开 [http://127.0.0.1:4173](http://127.0.0.1:4173)。

本项目没有 npm 运行时依赖，不需要 `npm install`。`npm run setup` 会编译本机提取器并运行自动测试。

## 当前实现边界

- 仅支持 macOS 13+，OCR 使用系统自带 Apple Vision；生产版仍按方案迁移为 PaddleOCR。
- Spike 主流程固定使用高精度 OCR，不再回落到容易对小字和中文产生乱码的标准快速模式。
- 单进程、单机、同时可运行多个子进程；尚未接入 Redis + Dramatiq。
- 已校验 200 MB、10 分钟和 4K 上限；编码兼容性由 AVFoundation 决定。
- 结果访问令牌已和任务 ID 分离，磁盘只保存令牌哈希；Spike 尚未实现邀请码、签名下载和自动清理。
- 三级筛选已实现低成本采样、图像变化/清晰度筛选和精细 OCR；变化点前后补帧、多区域轨迹和多帧逐字投票留到下一轮。

## 日常启动

```bash
cd framecopy-spike
npm run setup
npm start
```

运行数据写入 `data/jobs/<job-id>/`。该目录包含上传的原视频和 OCR 结果，已被 `.gitignore` 排除，不应提交到 GitHub。

## 可用命令

| 命令 | 用途 |
|---|---|
| `npm run setup` | 首次编译提取器并运行测试 |
| `npm start` | 启动 `127.0.0.1:4173` 本地网页 |
| `npm test` | 测试重复/逐字合并与 Markdown 生成 |
| `npm run check` | 语法检查、重新编译、全部测试 |

## 架构

```text
浏览器（无框架 HTML/CSS/JS）
  → Node.js 标准库 HTTP 服务（流式上传、任务 API、编辑、下载）
  → Objective-C 提取器（AVFoundation 解码 + Vision OCR + CoreImage 筛帧）
  → data/jobs/<id>/（证据帧、OCR、segments、Markdown、manifest）
```

`lib/merge.mjs` 是平台无关的合并与 Markdown 生成逻辑，带有自动测试。Objective-C 端保留同等算法用于处理进程内生成首份结果。

更详细的说明：

- [架构与数据流](docs/ARCHITECTURE.md)
- [复现与故障排查](docs/REPRODUCING.md)
- [测试清单](docs/TESTING.md)
- [版本记录](CHANGELOG.md)

## 隐私和仓库卫生

- 视频和任务结果只保存在当前 Mac 的 `data/` 目录。
- 编译产物 `bin/`、缓存 `.cache/` 和运行数据 `data/` 全部被 Git 忽略。
- 请不要将真实用户视频或 `job.json` 上传到 GitHub Issue。
- 任务访问令牌与任务 ID 分离，磁盘中仅保存令牌哈希。

## 许可证

当前尚未授予开源许可，保留所有权利。本仓库的公开状态不等于授权他人复制、修改或分发代码；正式开源前将另行选择 MIT 或 Apache-2.0 等许可证。

## 下一轮建议

1. 用 10—20 条黄金视频跑基准并记录召回率、候选帧数和耗时。
2. 根据失败样例补坐标轨迹、变化点前后补帧和多帧投票。
3. 将 OCR Provider 抽象为 Apple Vision / PaddleOCR，随后接入 FastAPI、Redis、Dramatiq 与 SQLite。
