---
name: video-download
description: 下载抖音 / B站视频并保存到本地。当用户给出抖音分享链接或分享口令（v.douyin.com / douyin.com），或 B站链接 / 短链 / BV 号（b23.tv / bili2233.cn / bilibili.com/video）并要求下载、保存、提取视频时使用。抖音取无水印最高清晰度；B站走官方接口，未登录最高 720P。Currently supports Douyin and Bilibili only — TikTok、快手、小红书未支持。Do NOT use for generic video transcoding, for B站番剧/直播/付费内容, or for platforms outside the support table.
allowed-tools: Bash, Read
---

# 视频下载（抖音 / B站）

一条命令完成：链接 → 解析 → 直链 → 下载 → 可选抽帧验水印。

平台逻辑拆在 `scripts/douyin.py`、`scripts/bilibili.py`，共享能力在 `scripts/_common.py`，
统一入口是 `scripts/fetch_video.py`。stdout 默认只输出一行 JSON（`--pretty` 时为缩进 JSON），
进度和日志走 stderr。

## 支持平台

| 平台 | 状态 | 说明 |
|---|---|---|
| 抖音 Douyin | ✅ 支持 | 无水印，取作者上传的最高清晰度（常见 1080p / 720p） |
| B站 Bilibili | ✅ 支持 | 官方接口；未登录最高 720P，合并流通常为单文件；`--page` 选分P，长视频可能分段 |
| TikTok | ❌ 未支持 | 能识别域名，返回 `unsupported_platform` |
| 快手 | ❌ 未支持 | 同上 |
| 小红书 | ❌ 未支持 | 同上 |
| B站番剧 / 直播 / 课程 / 付费内容 | ❌ 未支持 | 返回 `unsupported_platform` |

支持范围由各平台模块的 `DOMAINS`、`looks_like_id` 和入口的 `PLATFORMS` 表决定。
新增平台见文末「维护」。

## 何时使用

- 用户给抖音分享链接 / 分享口令，说"下载""保存""去水印""把视频下下来"
- 用户给抖音纯数字 aweme_id，要原视频
- 用户给 B站 `b23.tv` / `bili2233.cn` 短链、`bilibili.com/video/BV...`、BV 号或 av 号
- 需要标题、作者、分辨率、时长等元数据（`--check`）

## 何时不用

- **非抖音 / B站平台** —— 直接说明未支持，不要硬试
- **B站番剧 / 直播 / 课程 / 付费或会员专享** —— 当前不支持，直接说明
- **B站 1080P 及以上** —— 未登录拿不到，不要承诺；当前版本也不支持登录态
- **只要音频 / 文案** —— 本技能下整片；用其它工具单独处理
- **明确要"带水印原版"** —— 抖音路径给的是无水印版
- **批量搬运 / 去水印分发** —— 不主动扩展成批量下载

## 命令

脚本位于 `<技能根目录>/scripts/fetch_video.py`。技能加载时会给出 Base directory；
当前工作目录不是技能根目录时，换成绝对路径调用。`python` 不存在就用 `python3` 或 `py`。

```bash
# 抖音
python scripts/fetch_video.py "https://v.douyin.com/xxxx/"

# 抖音分享口令整段粘贴即可，脚本会自己抠出链接
python scripts/fetch_video.py "7.43 复制打开抖音，看看【迟迟的作品】 https://v.douyin.com/xxxx/ 12/27"

# B站：短链、视频链接、BV 号都可以
python scripts/fetch_video.py "https://www.bilibili.com/video/BV1xx411c7mD"
python scripts/fetch_video.py "https://b23.tv/xxxx" --page 2 --check
```

| 参数 | 说明 |
|---|---|
| `--out DIR` | 保存目录，默认 `~/Downloads` |
| `--flat` | 直接存到 `--out`，不建 `<平台>_<id>` 子目录 |
| `--page N` | B站分P序号，默认取链接里的 `p=` 或第 1 P；其它平台忽略并提示 |
| `--check` | 只解析元数据，不下载 |
| `--frame SEC` | 抽取该秒数的一帧存 JPG，用于人工查水印；可重复 |
| `--timeout N` | 下载超时秒数，默认 300 |
| `--pretty` | JSON 缩进输出 |

> `--frame` 依赖 `opencv-python`（或 `opencv-python-headless`）。未安装时下载照常进行，
> 只会跳过抽帧并在 `warnings` 里提示，不影响视频文件本身。
> `--check` 与 `--frame` 同用时只解析元数据，并在 `warnings` 里说明已忽略抽帧。

退出码：`0` 成功 · `1` 未预料错误 · `2` 平台不支持 / 无法识别 ·
`3` 解析失败 · `4` 下载失败 · `5` 输入里没有链接 · `130` 用户中断。

## 标准工作流

1. **识别平台**：抖音域名或纯数字 aweme_id；B站域名、`BV`/`av` 号。
   其它平台直接回复"暂不支持"，不要调用脚本。
2. **解析校验**（链接可能已失效，先探一下更省时间）：
   `python scripts/fetch_video.py "<链接>" --check`
3. **下载**：跑完整命令，默认落到 `~/Downloads/douyin_<aweme_id>/` 或
   `~/Downloads/bilibili_<BV号>/`。B站多P用 `--page` 选择。
4. **验水印**（按平台区分）：
   - **抖音**：加 `--frame`，抽 1–3 帧（建议时长的 25% / 50% / 75%），再用 Read 打开图片看：
     四角或边缘**没有**滚动的"抖音" logo、**没有** `@作者` + 数字 ID → 无水印。
     字幕、歌词、作者自己加的文字**不是**抖音水印，不要误判。
   - **B站**：平台不加水印，下载的就是原始流；UP 主自己加的字幕、角标不算平台水印。
     不要用"已去水印"描述 B站结果。
5. **回报**：绝对路径、标题、作者、清晰度、时长、大小；B站另报 BV 号 / 分P；
   多段视频报 `paths` 里的全部文件。

```bash
python scripts/fetch_video.py "https://v.douyin.com/xxxx/" --frame 10 --frame 28 --pretty
```

## 解读输出

抖音成功：

```json
{"ok": true, "platform": "douyin", "video_id": "...", "aweme_id": "...",
 "title": "...", "author": "迟迟", "quality": "1080p",
 "width": 1920, "height": 1080, "duration": 56, "size_mb": 9.42,
 "path": "...", "frames": [{"sec": 25, "path": "...jpg"}], "warnings": []}
```

B站成功：

```json
{"ok": true, "platform": "bilibili", "video_id": "BV...", "bvid": "BV...",
 "title": "...", "author": "...", "quality": "720P",
 "width": 1280, "height": 720, "duration": 213, "size_mb": 49.57,
 "page": 1, "part_count": 1, "path": "...", "warnings": []}
```

- `video_url` 是 CDN 直链，**有时效**，不要缓存、不要贴给用户长期使用
- `size_reported` 与接口上报偏差超过 1% 会出现在 `warnings` 里
- `frames` 只有传了 `--frame` 才出现
- `segment_count` 是分段数；B站长视频可能返回多段，`paths` 列出 `.partN` 文件，
  `path` 是第一段
- B站多P下载的文件名会带 `P<序号>`，避免同一目录下互相覆盖
- `source_api`：抖音是第三方接口名，B站是 `bilibili_web`

失败：

```json
{"ok": false, "error": {"code": "parse_failed", "message": "...", "hint": "..."}}
```

按 `hint` 处理。`parse_failed` 多半是公开解析服务临时不可用（抖音）或接口风控（B站），
**重试一次**再下结论。

## 硬性规则

**1. 不谎报水印。** 抖音没抽帧看过就不能说"已确认无水印"，只能说"下载的是无水印版本"；
抽了帧就必须真的用 Read 看图再下结论。B站平台本身不加水印，但不要用"已去水印"描述。

**2. 链接失效是常态。** 分享短链和 CDN 直链都有时效。`resolve_failed` 时让用户重新复制
分享链接，不要拿同一条反复重试。

**3. 解析来源要说清。** 抖音会把链接发给第三方服务 `api.xingzhige.com`；B站走官方
`api.bilibili.com`，不读取 cookie、不登录。用户明确在意隐私时如实告知。

**4. 落点要明确。** 回报绝对路径；Windows 下注意中文路径不要漏引号。

**5. B站清晰度上限是 720P。** 未登录拿不到 1080P 及以上；不要向用户索要 SESSDATA 或
cookie，当前版本不支持登录态。

**6. 仅个人使用。** 版权归原作者。B站用户协议限制抓取和下载，会员 / 付费 / 充电专属
内容直接拒绝；用户要求批量搬运 / 去水印分发时提示风险，不要主动扩展成批量下载。

**7. 多段视频不要谎称单文件。** 返回 `paths` 时如实说明是分段保存。

## 维护

目录结构：

```
scripts/
  fetch_video.py   # 唯一 CLI 入口：参数、平台分发、下载编排、输出
  _common.py       # 链接抽取、HTTP、下载、命名、JSON/退出码、抽帧
  douyin.py        # 抖音：短链 → aweme_id → PARSERS
  bilibili.py      # B站：b23/BV/av → view + playurl
```

平台模块契约：

- `KEY` / `NAME` / `DOMAINS` / `SUPPORTED`
- `looks_like_id(text) -> bool`
- `resolve_id(text, timeout) -> str`
- `fetch(ref_id, *, source_url=None, page=None, timeout=25) -> dict`，返回的 meta 结构见
  `_common.py` 顶部注释。

抖音接口失效时：

1. 跑 `--check` 拿错误码，不要靠猜。
2. 打开 `scripts/douyin.py`，看 `PARSERS` 列表（按顺序尝试，自动回退）。
3. 新增解析服务：实现 `parse_xxx(payload) -> dict`（返回字段参照 `parse_xingzhige`），
   在 `PARSERS` 追加 `{"name": ..., "endpoint": "https://...?url={}", "parse": parse_xxx}`。
   `endpoint` 里的 `{}` 会被替换成 URL 编码后的链接。

B站接口失效时：

1. `scripts/bilibili.py` 顶部的 `VIEW_API` / `PLAYURL_API`。
2. `playurl` 用 `fnval=1` 取合并流（无需 ffmpeg），`qn=80` 请求最高，未登录会被降级。
3. 下载请求必须带 `Referer: https://www.bilibili.com/`，否则 CDN 可能 403。
4. 分P在 `view` 返回的 `pages` 数组里，`--page` 对应数组下标 + 1。

新增平台：

1. 新建 `scripts/<platform>.py`，实现上面的契约。
2. 在 `fetch_video.py` 的 `PLATFORMS` 注册；未支持的平台把 `module` 设为 `None`。
3. 补本地测试（`.test/test_<platform>.py` 和入口测试），跑 `./.test/smoke.sh`。
   测试目录被 git 忽略，不会随技能分发。
