import os
import json
import asyncio
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont
import edge_tts
import subprocess

OUTPUT_DIR = os.path.abspath("output_video")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 脚本5个场景
SCENES = [
    {
        "id": 1,
        "title": "女装直播切片实操",
        "tag": "别再单纯刷视频了",
        "highlight": "把每天刷手机时间 · 变成实际收益",
        "voice_text": "如果你每天刷抖音超过两小时，听我一句劝，真的可以把时间换成实际收益了。",
        "bg_color": (24, 24, 32),
        "accent_color": (255, 68, 102), # 抖音红
        "sub_items": [
            "📱 手机/电脑均可实操",
            "⏱️ 每天碎片时间1-2小时",
            "👗 专注高佣女装类目"
        ]
    },
    {
        "id": 2,
        "title": "新手无需露脸 / 无需囤货",
        "tag": "低门槛轻资产",
        "highlight": "主播帮你讲解试穿 · 你只负责高光剪辑",
        "voice_text": "做女装直播切片，不需要你自己露脸，也不用进货发货。衣服有大主播在直播间帮你试穿讲解，你只需要把高光镜头剪出来挂车。",
        "bg_color": (20, 26, 36),
        "accent_color": (0, 200, 255),
        "sub_items": [
            "✨ 无需出镜拍摄",
            "📦 0库存 0押金 0物流压力",
            "🎬 剪映一键套用剪辑模版"
        ]
    },
    {
        "id": 3,
        "title": "官方正规授权 / 避开搬运",
        "tag": "合规授权通道",
        "highlight": "机构白名单报备 · 每日推送高清爆款素材",
        "voice_text": "很多人担心搬运违规。我们走的是机构官方授权通道，正规报备，每天的高清爆款素材和排班都直接整理好给到你。",
        "bg_color": (28, 20, 36),
        "accent_color": (168, 85, 247),
        "sub_items": [
            "🛡️ 官方后台白名单报备",
            "🔥 每日爆款时段精准标记",
            "📈 独家高转化二剪去重参数"
        ]
    },
    {
        "id": 4,
        "title": "手把手带跑通全流程",
        "tag": "真实带练计划",
        "highlight": "起号 · 剪辑 · 去重 · 挂车闭环带教",
        "voice_text": "一个人做确实容易走弯路，我打算直接带几位执行力强的新手，从零起号、去重技巧到挂车带货，手把手带你跑通出单闭环。",
        "bg_color": (20, 32, 28),
        "accent_color": (16, 185, 129),
        "sub_items": [
            "🎯 1对1实操辅导答疑",
            "🚀 独家女装爆款切片工程库",
            "💰 出单即见正反馈"
        ]
    },
    {
        "id": 5,
        "title": "免费领《切片实操SOP》",
        "tag": "行动号召",
        "highlight": "评论区扣【切片】· 立即上车带跑",
        "voice_text": "不搞虚的，我整理好的女装切片实操指南和去重参数表，在评论区扣个切片或者看我主页，带你一起上手实操！",
        "bg_color": (32, 22, 24),
        "accent_color": (255, 107, 107),
        "sub_items": [
            "📥 评论区回复【切片】或【想学】",
            "🎁 免费领取新手剪辑工具包",
            "👥 仅限前5位有执行力的朋友"
        ]
    }
]

FONT_PATH = "C:\\Windows\\Fonts\\msyh.ttc"
if not os.path.exists(FONT_PATH):
    FONT_PATH = "C:\\Windows\\Fonts\\simhei.ttf"

def get_font(size):
    return ImageFont.truetype(FONT_PATH, size)

async def generate_audios():
    print("Generating TTS audios...")
    voice = "zh-CN-XiaoxiaoNeural"
    for scene in SCENES:
        audio_file = os.path.join(OUTPUT_DIR, f"scene_{scene['id']}.mp3")
        comm = edge_tts.Communicate(scene["voice_text"], voice=voice, rate="+5%")
        await comm.save(audio_file)
        print(f"Generated {audio_file}")

def get_audio_duration(file_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

def create_scene_frame(scene, width=1080, height=1920, progress=0.0):
    img = Image.new("RGB", (width, height), scene["bg_color"])
    draw = ImageDraw.Draw(img)

    # 1. 顶部状态栏装饰
    font_small = get_font(34)
    font_tag = get_font(38)
    font_title = get_font(68)
    font_hl = get_font(44)
    font_body = get_font(42)

    # 装饰发光网格 / 顶部光晕
    accent = scene["accent_color"]
    for i in range(150):
        alpha = int((1 - i / 150) * 60)
        draw.line([(0, i), (width, i)], fill=(accent[0]//4, accent[1]//4, accent[2]//4))

    # 顶部标签胶囊
    tag_text = f"●  {scene['tag']}  ●"
    tag_w = int(draw.textlength(tag_text, font=font_tag))
    tag_box = [(width//2 - tag_w//2 - 35, 240), (width//2 + tag_w//2 + 35, 310)]
    draw.rounded_rectangle(tag_box, radius=35, fill=(35, 40, 50), outline=accent, width=3)
    draw.text((width//2 - tag_w//2, 252), tag_text, font=font_tag, fill=accent)

    # 主标题 (黄色或白色加粗感)
    title_text = scene["title"]
    title_w = int(draw.textlength(title_text, font=font_title))
    draw.text((width//2 - title_w//2, 360), title_text, font=font_title, fill=(255, 255, 255))

    # 核心高光条 (带背景卡片)
    hl_text = scene["highlight"]
    hl_w = int(draw.textlength(hl_text, font=font_hl))
    card_top = 480
    card_h = 100
    card_box = [(width//2 - hl_w//2 - 40, card_top), (width//2 + hl_w//2 + 40, card_top + card_h)]
    draw.rounded_rectangle(card_box, radius=20, fill=(accent[0]//3, accent[1]//3, accent[2]//3), outline=accent, width=2)
    draw.text((width//2 - hl_w//2, card_top + 24), hl_text, font=font_hl, fill=(255, 240, 160))

    # 中部模拟“切片实操UI工作台 / 信息卡片”
    panel_box = [(90, 640), (width - 90, 1380)]
    draw.rounded_rectangle(panel_box, radius=30, fill=(15, 18, 26), outline=(60, 70, 90), width=3)

    # 面板顶部标题栏 (模拟软件窗口)
    draw.rounded_rectangle([(90, 640), (width - 90, 720)], radius=30, fill=(25, 30, 42))
    draw.rectangle([(90, 690), (width - 90, 720)], fill=(25, 30, 42))
    draw.ellipse([(120, 670), (145, 695)], fill=(255, 95, 86))
    draw.ellipse([(160, 670), (185, 695)], fill=(255, 189, 46))
    draw.ellipse([(200, 670), (225, 695)], fill=(39, 201, 63))
    draw.text((250, 665), "LIVE CUTTING WORKFLOW v2.0 - 机构分发系统", font=get_font(28), fill=(160, 175, 195))

    # 卡片内部3个要点项目
    start_y = 780
    for idx, item in enumerate(scene["sub_items"]):
        item_box = [(130, start_y + idx * 170), (width - 130, start_y + idx * 170 + 125)]
        draw.rounded_rectangle(item_box, radius=20, fill=(28, 35, 48), outline=(50, 60, 80), width=2)
        # 序号标记
        draw.rounded_rectangle([(155, start_y + idx * 170 + 25), (225, start_y + idx * 170 + 95)], radius=15, fill=accent)
        draw.text((178, start_y + idx * 170 + 35), f"0{idx+1}", font=get_font(36), fill=(255, 255, 255))
        # 内容文字
        draw.text((250, start_y + idx * 170 + 38), item, font=font_body, fill=(245, 245, 250))

    # 底部提示卡片
    bottom_box = [(90, 1440), (width - 90, 1680)]
    draw.rounded_rectangle(bottom_box, radius=25, fill=(35, 25, 30), outline=(255, 100, 120), width=3)
    b_title = "🔥 2026 女装直播切片 · 搞米孵化实录"
    b_w = int(draw.textlength(b_title, font=font_body))
    draw.text((width//2 - b_w//2, 1480), b_title, font=font_body, fill=(255, 220, 120))

    b_sub = "一部手机/电脑即可参与 · 适合宝妈/上班族/剪辑新手"
    b_sub_w = int(draw.textlength(b_sub, font=font_small))
    draw.text((width//2 - b_sub_w//2, 1560), b_sub, font=font_small, fill=(200, 205, 215))

    # 底部进度条
    bar_y = 1780
    draw.rectangle([(100, bar_y), (width - 100, bar_y + 12)], fill=(50, 60, 75))
    draw.rectangle([(100, bar_y), (int(100 + (width - 200) * progress), bar_y + 12)], fill=accent)

    # 底部版权/免责声明小字
    footer_text = "正规机构官方授权合作 · 严禁低质搬运 · 坚持原创二剪"
    footer_w = int(draw.textlength(footer_text, font=get_font(26)))
    draw.text((width//2 - footer_w//2, 1830), footer_text, font=get_font(26), fill=(120, 130, 145))

    return np.array(img)

def render_scene_video(scene, duration, fps=30):
    audio_path = os.path.join(OUTPUT_DIR, f"scene_{scene['id']}.mp3")
    temp_avi = os.path.join(OUTPUT_DIR, f"scene_{scene['id']}_raw.mp4")
    out_mp4 = os.path.join(OUTPUT_DIR, f"scene_{scene['id']}_final.mp4")

    total_frames = int(duration * fps)
    width, height = 1080, 1920

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(temp_avi, fourcc, fps, (width, height))

    print(f"Rendering Scene {scene['id']} ({duration:.2f}s, {total_frames} frames)...")
    for frame_idx in range(total_frames):
        progress = frame_idx / max(1, total_frames - 1)
        frame_rgb = create_scene_frame(scene, width, height, progress)
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        writer.write(frame_bgr)
    writer.release()

    # 合并音频
    cmd = [
        "ffmpeg", "-y",
        "-i", temp_avi,
        "-i", audio_path,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_mp4
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    if os.path.exists(temp_avi):
        os.remove(temp_avi)
    return out_mp4

async def main():
    await generate_audios()
    
    scene_videos = []
    for scene in SCENES:
        audio_file = os.path.join(OUTPUT_DIR, f"scene_{scene['id']}.mp3")
        dur = get_audio_duration(audio_file) + 0.4  # 多留0.4秒缓冲呼吸
        mp4_path = render_scene_video(scene, dur)
        scene_videos.append(mp4_path)

    # 拼接所有分镜生成最终成品视频
    concat_list_path = os.path.join(OUTPUT_DIR, "concat_list.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for v in scene_videos:
            f.write(f"file '{v}'\n")

    final_output = os.path.abspath("女装切片招徒弟带教视频_成品.mp4")
    print("Concatenating scenes into final video...")
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        final_output
    ]
    subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    print(f"\nSUCCESS! Final video generated at: {final_output}")

if __name__ == "__main__":
    asyncio.run(main())
