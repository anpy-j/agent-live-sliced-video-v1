"""Local procedural media generation; one cancellable batch at a time."""
import json
import math
import os
import random
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ..editor.model import ident, number

STYLES = ("漂浮光点", "渐变波纹", "几何线条", "星光纹理", "柔和光斑")


def artwork(path, seed, width, height, style):
    rng = random.Random(seed)
    image = Image.new("RGB", (width,height), tuple(rng.randrange(3,22) for _ in range(3)))
    draw = ImageDraw.Draw(image)
    unit = min(width,height)
    for i in range(90):
        x,y = rng.randrange(width),rng.randrange(height)
        radius = rng.randint(max(2,unit//100),max(3,unit//7))
        color = tuple(rng.randrange(25,210) for _ in range(3))
        if style in (0,4):
            draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=color)
        elif style == 1:
            draw.ellipse((x-radius,y-radius,x+radius,y+radius),outline=color,width=max(1,unit//180))
        elif style == 2:
            draw.line((x,y,x+rng.randint(-unit,unit),y+rng.randint(-unit,unit)),fill=color,width=max(1,unit//160))
        else:
            points=[(x+math.cos(j*math.pi/5)*radius*(1 if j%2==0 else .3),
                     y+math.sin(j*math.pi/5)*radius*(1 if j%2==0 else .3)) for j in range(10)]
            draw.polygon(points,fill=color)
    if style in (0,4):
        image=image.filter(ImageFilter.GaussianBlur(unit/(180 if style==0 else 28)))
    image.save(path)


class MaterialQueue:
    def __init__(self, service):
        self.service=service
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.thread=None
        self.process=None
        self.cancelled=threading.Event()
        with service.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            for row in con.execute("SELECT data FROM materials").fetchall():
                job=json.loads(row[0])
                if job["status"]=="running":
                    job.update(status="failed",error="上次生成被中断，已生成文件保留，可新建批次")
                    con.execute("UPDATE materials SET data=? WHERE id=?",(json.dumps(job),job["id"]))

    def jobs(self):
        with self.service.connect() as con:
            return {"jobs":[json.loads(row[0]) for row in con.execute("SELECT data FROM materials ORDER BY rowid DESC LIMIT 20")]}

    def update(self,job):
        with self.service.connect() as con:
            con.execute("INSERT OR REPLACE INTO materials VALUES (?,?)",(job["id"],json.dumps(job)))

    def start(self,payload):
        counts={key:int(number(payload.get(key,100),0,100,key)) for key in ("video_count","image_count")}
        if not sum(counts.values()):
            raise ValueError("至少生成一个视频或一张图片")
        if any(counts[key]!=float(payload.get(key,100)) for key in counts):
            raise ValueError("素材数量需要是整数")
        duration=number(payload.get("duration",10),2,30,"素材视频时长")
        resolution=payload.get("resolution","720x1280")
        if resolution not in {"160x240","360x640","720x1280"}:
            raise ValueError("请选择支持的素材分辨率")
        base=Path(str(payload.get("output_dir") or self.service.root/"画中画素材")).expanduser()
        if not base.is_absolute():
            raise ValueError("素材输出目录需要是绝对路径")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("已有素材正在生成，请等待完成或取消")
            if self.stop.is_set():
                raise ValueError("服务正在关闭")
            job_id=ident()
            folder=base/f"素材批次-{time.strftime('%Y%m%d')}-{job_id[:8]}"
            folder.mkdir(parents=True,exist_ok=False)
            for sub in ("视频","图片",".sources"):
                (folder/sub).mkdir()
            job={"id":job_id,"status":"running","created":time.time(),"progress":0,"error":"",
                 **counts,"duration":duration,"resolution":resolution,"fps":30,
                 "video_dir":str(folder/"视频"),"image_dir":str(folder/"图片"),"manifest":str(folder/"manifest.json"),
                 "image_done":0,"video_done":0,"assets":[],"current":"准备生成","seed":random.SystemRandom().getrandbits(32)}
            self.cancelled.clear()
            self.update(job)
            self.thread=threading.Thread(target=self._run,args=(job,),daemon=True,name="postprocess-materials")
            self.thread.start()
            return dict(job)

    def cancel(self,job_id):
        with self.lock:
            job=next((j for j in self.jobs()["jobs"] if j["id"]==job_id),None)
            if not job:
                raise KeyError("素材任务不存在")
            if job["status"]=="running":
                self.cancelled.set()
                if self.process and self.process.poll() is None:
                    self.process.terminate()
            return job

    def close(self):
        self.stop.set()
        self.cancelled.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        if self.thread:
            self.thread.join(timeout=10)

    def check_cancel(self):
        if self.cancelled.is_set() or self.stop.is_set():
            raise InterruptedError("生成已取消，已完成的素材保留")

    def encode(self,job,source,target,index):
        w,h=map(int,job["resolution"].split("x"))
        frames=round(job["duration"]*job["fps"])
        phase=(job["seed"]+index*137)%628/100
        # Closed periodic camera path; motion continues across repeated playback.
        vf=(f"zoompan=z='1.18+0.06*sin(2*PI*on/{frames}+{phase})':"
            f"x='(iw-iw/zoom)*(0.5+0.45*sin(2*PI*on/{frames}+{phase}))':"
            f"y='(ih-ih/zoom)*(0.5+0.45*cos(2*PI*on/{frames}+{phase}))':"
            f"d={frames}:s={w}x{h}:fps={job['fps']},format=yuv420p")
        partial=target.with_suffix(".partial.mp4")
        log=target.with_suffix(".log")
        try:
            with log.open("w",encoding="utf-8") as errors:
                with self.lock:
                    self.check_cancel()
                    self.process=subprocess.Popen(["ffmpeg","-hide_banner","-v","error","-n","-i",str(source),
                        "-vf",vf,"-frames:v",str(frames),"-an","-c:v","libx264","-threads","2","-preset","veryfast",
                        "-crf","23","-movflags","+faststart","-progress","pipe:1",str(partial)],
                        stdout=subprocess.PIPE,stderr=errors,text=True,encoding="utf-8",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                    process=self.process
                for line in process.stdout:
                    if line.startswith("out_time_us="):
                        fraction=min(1,max(0,int(line.split("=",1)[1])/1e6/job["duration"]))
                        job["progress"]=round(100*(job["image_done"]+job["video_done"]+fraction)/(job["video_count"]+job["image_count"]),1)
                        self.update(job)
                self.check_cancel()
                if process.wait()!=0:
                    raise RuntimeError(log.read_text(encoding="utf-8")[-1200:] or "素材视频生成失败")
            if target.exists():
                raise RuntimeError("生成文件已存在，拒绝覆盖")
            partial.rename(target)
        finally:
            with self.lock:
                process=self.process
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if process and process.stdout:
                    process.stdout.close()
                self.process=None
            partial.unlink(missing_ok=True)
            log.unlink(missing_ok=True)

    def _run(self,job):
        records=[]
        manifest=Path(job["manifest"])
        def save_manifest():
            temp=manifest.with_suffix(".tmp")
            temp.write_text(json.dumps({"version":1,"generator":"LiveCut procedural materials",**job,"files":records},ensure_ascii=False,indent=2),encoding="utf-8")
            temp.replace(manifest)
        try:
            save_manifest()
            w,h=map(int,job["resolution"].split("x"))
            for kind in ("image","video"):
                for index in range(job[kind+"_count"]):
                    self.check_cancel()
                    seed=(job["seed"]+index*104729+(0 if kind=="image" else 2147483647))%2**32
                    style=index%len(STYLES)
                    job["current"]=f"{'图片' if kind=='image' else '视频'} {index+1}/{job[kind+'_count']} · {STYLES[style]}"
                    self.update(job)
                    target=Path(job[kind+"_dir"])/f"{index+1:03d}-{STYLES[style]}-{seed}.{'png' if kind=='image' else 'mp4'}"
                    source=target if kind=="image" else manifest.parent/".sources"/f"{index+1:03d}.png"
                    try:
                        artwork(source,seed,w,h,style)
                        self.check_cancel()
                        if kind=="video":
                            self.encode(job,source,target,index)
                        asset=self.service.import_asset({"path":str(target)})
                        if asset["kind"]!=kind or (kind=="video" and (asset["has_audio"] or abs(asset["duration"]-job["duration"])>.1)):
                            raise ValueError("生成素材格式或时长校验失败")
                        job["assets"].append(asset["id"])
                        records.append({"kind":kind,"path":str(target),"seed":seed,"style":STYLES[style],"asset_id":asset["id"]})
                        job[kind+"_done"]+=1
                        job["progress"]=round(100*(job["image_done"]+job["video_done"])/(job["image_count"]+job["video_count"]),1)
                        self.update(job)
                        save_manifest()
                    finally:
                        if kind=="video":
                            source.unlink(missing_ok=True)
            job.update(status="completed",progress=100,current="生成完成")
        except InterruptedError as exc:
            job.update(status="cancelled",error=str(exc),current="已取消")
        except Exception as exc:
            job.update(status="failed",error=str(exc)[-1200:],current="生成失败")
        finally:
            self.update(job)
            save_manifest()
