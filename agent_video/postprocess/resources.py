"""Original procedural assets; generated locally, no external asset marketplace."""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw


def texture(folder: Path, seed: int) -> Path:
    rng = random.Random(seed)
    image = Image.new("RGB", (384, 384), (25, 32, 48))
    draw = ImageDraw.Draw(image)
    for _ in range(180):
        x, y, size = rng.randrange(384), rng.randrange(384), rng.randrange(4, 80)
        draw.ellipse((x-size, y-size, x+size, y+size), fill=tuple(rng.randrange(20, 150) for _ in range(3)))
    path = folder / f"抽象纹理-{seed}.png"
    image.save(path)
    return path


def animated_sticker(folder: Path, style: str) -> Path:
    frames = []
    palette = [0, 0, 0, 255, 216, 92, 255, 245, 210, 190, 219, 255] + [0]*756
    for frame in range(24):
        image = Image.new("P", (160, 160), 0)
        image.putpalette(palette)
        draw = ImageDraw.Draw(image)
        pulse = .3 + .7*(.5+.5*math.sin(frame*math.tau/24))
        # Put visible detail near every corner: only these edges peek into the video.
        for x, y in ((5, 5), (155, 5), (5, 155), (155, 155)):
            if style == "stars":
                radius = 17*pulse
                points = [(x+math.cos(i*math.pi/5-math.pi/2)*radius*(1 if i%2==0 else .4),
                           y+math.sin(i*math.pi/5-math.pi/2)*radius*(1 if i%2==0 else .4)) for i in range(10)]
                draw.polygon(points, fill=1 if frame%12<6 else 2)
            else:
                radius = 14*pulse
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), outline=3, width=3)
        frames.append(image)
    path = folder / ("闪烁星星.gif" if style == "stars" else "呼吸光环.gif")
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0,
                   transparency=0, disposal=2, optimize=False)
    return path


def animated_progress(folder: Path, style: str) -> Path:
    """A transparent row of pulsing hearts/sparkles, independent of video length."""
    frames = []
    palette = [0, 0, 0, 255, 150, 180, 255, 215, 230, 255, 246, 190] + [0]*756
    for frame in range(24):
        image = Image.new("P", (40, 40) if style == "travelling_star" else (960, 40), 0)
        image.putpalette(palette)
        draw = ImageDraw.Draw(image)
        for i, x in enumerate([20] if style == "travelling_star" else range(16, 960, 32)):
            phase = frame*math.tau/24+i*.65
            radius = 7+5*(.5+.5*math.sin(phase))
            y = 20+(4*math.sin(phase) if style == "floating" else 0)
            if style in {"sparkles", "travelling_star"}:
                count = 10 if style == "travelling_star" else 8
                radius = 11+4*(.5+.5*math.sin(phase)) if style == "travelling_star" else radius
                points = [(x+math.cos(j*math.tau/count-math.pi/2)*radius*(1 if j%2==0 else .35),
                           y+math.sin(j*math.tau/count-math.pi/2)*radius*(1 if j%2==0 else .35)) for j in range(count)]
                draw.polygon(points, fill=3 if math.sin(phase)>.1 else 2)
            else:
                points = []
                for j in range(60):
                    t = j*math.tau/60
                    points.append((x+radius*math.sin(t)**3,
                                   y-radius*(13*math.cos(t)-5*math.cos(2*t)-2*math.cos(3*t)-math.cos(4*t))/16))
                draw.polygon(points, fill=1 if math.sin(phase)<.3 else 2)
        frames.append(image)
    names = {"hearts": "跳动爱心", "floating": "漂浮爱心", "sparkles": "闪耀星光", "travelling_star": "游走小星星"}
    path = folder / ("进度条-"+names[style]+".gif")
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0,
                   transparency=0, disposal=2, optimize=False)
    return path
