# 复现与故障排查

## 从新 Mac 复现

1. 确认系统版本不低于 macOS 13。
2. 安装 Xcode Command Line Tools：`xcode-select --install`。
3. 安装 Node.js 20 或更高版本。
4. 克隆仓库，进入根目录。
5. 执行 `npm run setup`。
6. 执行 `npm start`。
7. 访问 `http://127.0.0.1:4173`。

## 复现检查

```bash
node --version
clang --version
npm run check
curl http://127.0.0.1:4173/api/health
```

健康检查应返回：

```json
{"ok":true,"extractor_ready":true,"platform":"darwin"}
```

## 常见问题

### `extractor_ready: false`

执行 `npm run build:extractor`。如果编译器不存在，先安装 Xcode Command Line Tools。

### 端口 4173 被占用

```bash
PORT=4174 npm start
```

然后访问 `http://127.0.0.1:4174`。

### 视频无法解码

当前解码能力由 AVFoundation 决定。先用常见 H.264 + MP4 视频测试。生产版将使用 FFmpeg 扩大编码覆盖。

### OCR 仍有错字

高精度仅代表使用 Apple Vision Accurate 模式和更多候选帧，不代表 100% 准确。请记录任务编号、时间点和错误类型，作为黄金测试集的失败样例。

## 不应上传的文件

`git status --ignored` 中以下目录应显示为已忽略：

- `data/`
- `bin/`
- `.cache/`

如果其中任何文件出现在待提交列表，请先停止提交并检查 `.gitignore`。
