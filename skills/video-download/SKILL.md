---
name: video-download
description: 下载抖音视频并去除水印（1080p 原画）。当用户给出抖音分享链接或分享口令（v.douyin.com / douyin.com）并要求下载、保存、去水印、提取视频时使用。Currently supports Douyin only — TikTok、快手、小红书、B站均未支持。Do NOT use for generic video transcoding or for platforms outside the support table.
allowed-tools: Bash, Read
---

# 视频无水印下载

一条命令完成：链接 → 解析 → 无水印直链 → 下载 → 可选抽帧验水印。
全部逻辑在 `scripts/fetch_video.py`，stdout 只输出一行 JSON，进度走 stderr。

## 支持平台

**当前只支持抖音。**

| 平台 | 状态 | 说明 |
|---|---|---|
| 抖音 Douyin | ✅ 支持 | 无水印，取作者上传的最高清晰度（常见 1080p / 720p） |
| TikTok | ❌ 未支持 | 能识别域名，返回 `unsupported_platform` |
| 快手 | ❌ 未支持 | 同上 |
| 小红书 | ❌ 未支持 | 同上 |
| B站 | ❌ 未支持 | 同上 |

支持范围由脚本顶部的 `PLATFORMS` 表和 `PARSERS` 列表决定。新增平台需要同时实现 ID 解析和解析器，见文末「维护」。

## 何时使用

- 用户给抖音分享链接 / 分享口令，说"下载""保存""去水印""把视频下下来"
- 用户给纯数字 aweme_id，要原视频
- 需要标题、作者、分辨率、时长等元数据（`--check`）

## 何时不用

- **非抖音平台** —— 直接说明未支持，不要硬试
- **只要音频 / 文案** —— 本技能下整片；用其它工具单独处理
- **明确要"带水印原版"** —— 本技能给的是无水印版

## 命令

脚本位于 `<技能根目录>/scripts/fetch_video.py`。技能加载时会给出 Base directory；
当前工作目录不是技能根目录时，换成绝对路径调用。`python` 不存在就用 `python3` 或 `py`。

```bash
python scripts/fetch_video.py "https://v.douyin.com/xxxx/"
```

分享口令整段粘贴即可，脚本会自己抠出链接：

```bash
python scripts/fetch_video.py "7.43 复制打开抖音，看看【迟迟的作品】 https://v.douyin.com/xxxx/ 12/27"
```

| 参数 | 说明 |
|---|---|
| `--out DIR` | 保存目录，默认 `~/Downloads` |
| `--flat` | 直接存到 `--out`，不建 `douyin_<id>` 子目录 |
| `--check` | 只解析元数据，不下载 |
| `--frame SEC` | 抽取该秒数的一帧存 JPG，用于人工查水印；可重复 |
| `--timeout N` | 下载超时秒数，默认 300 |
| `--pretty` | JSON 缩进输出 |

> `--frame` 依赖 `opencv-python`（或 `opencv-python-headless`）。未安装时下载照常进行，
> 只会跳过抽帧并在 `warnings` 里提示，不影响视频文件本身。

退出码：`0` 成功 · `2` 平台不支持 · `3` 解析失败 · `4` 下载失败 · `5` 输入里没有链接。

## 标准工作流

1. **识别平台**：看域名。不是抖音就直接回复"暂不支持"，不要调用脚本。
2. **解析校验**（链接可能已失效，先探一下更省时间）：
   `python scripts/fetch_video.py "<链接>" --check`
3. **下载**：跑完整命令，默认落到 `~/Downloads/douyin_<aweme_id>/`。
4. **验水印**：加 `--frame`，抽 1–3 帧（建议时长的 25% / 50% / 75%），再用 Read 打开图片看：
   - 四角或边缘**没有**滚动的"抖音" logo、**没有** `@作者` + 数字 ID → 无水印
   - 字幕、歌词、作者自己加的文字**不是**抖音水印，不要误判
5. **回报**：绝对路径、标题、作者、分辨率、时长、大小。

```bash
python scripts/fetch_video.py "https://v.douyin.com/xxxx/" --frame 10 --frame 28 --pretty
```

## 解读输出

成功：

```json
{"ok": true, "path": "C:\\...\\迟迟_标题_7696355215896825107.mp4",
 "title": "...", "author": "迟迟", "quality": "1080p",
 "width": 1920, "height": 1080, "duration": 56, "size_mb": 9.42,
 "frames": [{"sec": 25, "path": "...jpg"}], "warnings": []}
```

- `video_url` 是 CDN 直链，**有时效**，不要缓存、不要贴给用户长期使用
- `size_mb` 与 `size_reported` 偏差超过 1% 会出现在 `warnings` 里
- `frames` 只有传了 `--frame` 才出现

失败：

```json
{"ok": false, "error": {"code": "parse_failed", "message": "...", "hint": "..."}}
```

按 `hint` 处理。`parse_failed` 多半是公开解析服务临时不可用，**重试一次**再下结论。

## 硬性规则

**1. 不谎报水印。** 没抽帧看过就不能说"已确认无水印"，只能说"下载的是无水印版本"。
抽了帧就必须真的用 Read 看图再下结论。

**2. 链接失效是常态。** 分享短链和 CDN 直链都有时效。`resolve_failed` 时让用户重新复制
分享链接，不要拿同一条反复重试。

**3. 解析接口是第三方服务。** 调用会把分享链接发给 `api.xingzhige.com`。用户明确在意隐私时如实告知。

**4. 落点要明确。** 回报绝对路径；Windows 下注意中文路径不要漏引号。

**5. 仅个人使用。** 版权归原作者，二次发布需授权。用户要求批量搬运 / 去水印分发时提示风险，
不要主动扩展成批量下载。

## 维护

第三方接口会变，失效时按这个顺序处理：

1. 跑 `--check` 拿错误码，不要靠猜。
2. 打开 `scripts/fetch_video.py`，看 `PARSERS` 列表（按顺序尝试，自动回退）。
3. 新增解析服务：实现 `parse_xxx(payload) -> dict`（返回字段参照 `parse_xingzhige`），
   在 `PARSERS` 追加 `{"name": ..., "endpoint": "https://...?url={}", "parse": parse_xxx}`。
   `endpoint` 里的 `{}` 会被替换成 URL 编码后的分享链接。
4. 新增平台：在 `PLATFORMS` 登记域名和 `supported`，并补上 `resolve_aweme_id` 的 ID 提取规则。
